import hashlib
import json
import math
import os
import sqlite3
import time

import requests


DEFAULT_OSRM_BASE_URL = os.environ.get("DISPATCH_OSRM_BASE_URL", "http://router.project-osrm.org")
DEFAULT_TABLE_LIMIT = int(os.environ.get("DISPATCH_OSRM_TABLE_MAX_LOCATIONS", "80"))
DEFAULT_TABLE_TIMEOUT = float(os.environ.get("DISPATCH_OSRM_TABLE_TIMEOUT", "12"))
DEFAULT_ALLOW_PAIR_LOOKUP = os.environ.get("DISPATCH_OSRM_PAIR_LOOKUP", "").lower() in ("1", "true", "yes")
DEFAULT_TABLE_CACHE_COVERAGE = float(os.environ.get("DISPATCH_OSRM_TABLE_CACHE_COVERAGE", "0.90"))
DEFAULT_TABLE_CHUNK_OVERLAP = int(os.environ.get("DISPATCH_OSRM_TABLE_CHUNK_OVERLAP", "12"))
DEFAULT_TABLE_MAX_CHUNKS = int(os.environ.get("DISPATCH_OSRM_TABLE_MAX_CHUNKS", "40"))


def haversine_km(lat1, lon1, lat2, lon2):
    radius_km = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class RoutingCostProvider:
    """
    Point-to-point driving cost provider for route construction.

    The scheduler uses this for cheap comparisons such as nearest-neighbor,
    2-Opt, and feasibility checks. Final route geometry still comes from
    OSRM Route API in OSRMClient.
    """

    def __init__(
        self,
        cache_file="../osrm_cache.db",
        base_url=DEFAULT_OSRM_BASE_URL,
        table_limit=DEFAULT_TABLE_LIMIT,
        timeout=DEFAULT_TABLE_TIMEOUT,
        allow_pair_lookup=DEFAULT_ALLOW_PAIR_LOOKUP,
        fallback_factor=1.3,
        fallback_speed_kmh=40.0,
        table_cache_coverage=DEFAULT_TABLE_CACHE_COVERAGE,
        table_chunk_overlap=DEFAULT_TABLE_CHUNK_OVERLAP,
        table_max_chunks=DEFAULT_TABLE_MAX_CHUNKS,
    ):
        cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), cache_file)
        self.conn = sqlite3.connect(cache_path, timeout=30)
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.cursor = self.conn.cursor()
        self.base_url = base_url.rstrip("/")
        self.table_limit = max(int(table_limit), 2)
        self.timeout = float(timeout)
        self.allow_pair_lookup = bool(allow_pair_lookup)
        self.fallback_factor = fallback_factor
        self.fallback_speed_kmh = fallback_speed_kmh
        self.table_cache_coverage = max(0.0, min(float(table_cache_coverage), 1.0))
        self.table_chunk_overlap = max(0, int(table_chunk_overlap))
        self.table_max_chunks = max(1, int(table_max_chunks))
        self.memory_cache = {}
        self.table_group_cache = set()
        self.stats = {
            "table_requests": 0,
            "table_requests_saved": 0,
            "cache_hits": 0,
            "memory_hits": 0,
            "cache_misses": 0,
            "fallbacks": 0,
            "fallbacks_not_saved": 0,
        }
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS pair_cost_cache (
                origin_key TEXT NOT NULL,
                dest_key TEXT NOT NULL,
                duration_min REAL NOT NULL,
                distance_km REAL NOT NULL,
                source TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (origin_key, dest_key)
            )
            """
        )
        self.conn.commit()
        print(
            f"[RoutingCostProvider] ready: base_url={self.base_url}, "
            f"table_limit={self.table_limit}, timeout={self.timeout}s, "
            f"pair_lookup={self.allow_pair_lookup}, "
            f"table_cache_coverage={self.table_cache_coverage:.0%}",
            flush=True,
        )

    def close(self):
        self.conn.close()

    def coord_key(self, coord):
        lat, lon = coord
        return f"{round(float(lat), 5)},{round(float(lon), 5)}"

    def _fallback_cost(self, origin, dest):
        dist_km = haversine_km(origin[0], origin[1], dest[0], dest[1]) * self.fallback_factor
        duration_min = (dist_km / self.fallback_speed_kmh) * 60.0 if self.fallback_speed_kmh else 0.0
        self.stats["fallbacks"] += 1
        return {
            "duration": duration_min,
            "distance": dist_km,
            "source": "Haversine Fallback",
            "used_fallback": True,
        }

    def _get_cached(self, origin, dest):
        origin_key = self.coord_key(origin)
        dest_key = self.coord_key(dest)
        memory_key = (origin_key, dest_key)
        if memory_key in self.memory_cache:
            self.stats["memory_hits"] += 1
            cached = dict(self.memory_cache[memory_key])
            cached["source"] = "Memory Cache" if cached.get("source") != "Haversine Fallback (Memory)" else cached["source"]
            return cached
        self.cursor.execute(
            """
            SELECT duration_min, distance_km, source
            FROM pair_cost_cache
            WHERE origin_key = ? AND dest_key = ?
            """,
            (origin_key, dest_key),
        )
        row = self.cursor.fetchone()
        if not row:
            self.stats["cache_misses"] += 1
            return None
        self.stats["cache_hits"] += 1
        return {
            "duration": row[0],
            "distance": row[1],
            "source": "Cache",
            "cached_source": row[2],
            "used_fallback": row[2] == "Haversine Fallback",
        }

    def _save_cost(self, origin, dest, duration_min, distance_km, source):
        origin_key = self.coord_key(origin)
        dest_key = self.coord_key(dest)
        self.memory_cache[(origin_key, dest_key)] = {
            "duration": float(duration_min),
            "distance": float(distance_km),
            "source": source,
            "used_fallback": source.startswith("Haversine Fallback"),
        }
        self.cursor.execute(
            """
            INSERT OR REPLACE INTO pair_cost_cache
            (origin_key, dest_key, duration_min, distance_km, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                origin_key,
                dest_key,
                float(duration_min),
                float(distance_km),
                source,
                time.time(),
            ),
        )

    def _table_group_key(self, unique):
        return hashlib.md5("|".join(sorted(self.coord_key(coord) for coord in unique)).encode()).hexdigest()

    @staticmethod
    def _spatial_key(coord, bounds):
        """Morton/Z-order key: keeps geographically close coordinates close in a flat list."""
        min_lat, max_lat, min_lon, max_lon = bounds
        lat_span = max(max_lat - min_lat, 1e-9)
        lon_span = max(max_lon - min_lon, 1e-9)
        x = int(((coord[1] - min_lon) / lon_span) * 65535)
        y = int(((coord[0] - min_lat) / lat_span) * 65535)
        result = 0
        for bit in range(16):
            result |= ((x >> bit) & 1) << (bit * 2)
            result |= ((y >> bit) & 1) << (bit * 2 + 1)
        return result

    def _spatial_table_batches(self, unique):
        """Create overlapping local OSRM batches; batching never defines route regions."""
        anchor = unique[0]
        points = unique[1:]
        if not points:
            return [unique]
        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        bounds = (min(lats), max(lats), min(lons), max(lons))
        points.sort(key=lambda coord: self._spatial_key(coord, bounds))

        capacity = self.table_limit - 1
        overlap = min(self.table_chunk_overlap, max(capacity - 1, 0))
        stride = max(1, capacity - overlap)
        batches = []
        for start in range(0, len(points), stride):
            batch = [anchor] + points[start:start + capacity]
            if len(batch) >= 2:
                batches.append(batch)
            if start + capacity >= len(points):
                break
        return batches

    def _cache_coverage(self, unique):
        total = len(unique) * len(unique)
        if total == 0:
            return 1.0, 0, 0

        keys = [self.coord_key(coord) for coord in unique]
        memory_hits = 0
        missing_pairs = []
        for origin_key in keys:
            for dest_key in keys:
                memory_value = self.memory_cache.get((origin_key, dest_key))
                # A Haversine value is only a temporary safety net.  It must
                # remain eligible for OSRM Table replacement during warm-up.
                if memory_value and not memory_value.get("used_fallback", False):
                    memory_hits += 1
                else:
                    missing_pairs.append((origin_key, dest_key))

        db_hits = 0
        for idx in range(0, len(missing_pairs), 900):
            batch = missing_pairs[idx:idx + 900]
            if not batch:
                continue
            placeholders = ",".join(["(?, ?)"] * len(batch))
            params = []
            for origin_key, dest_key in batch:
                params.extend([origin_key, dest_key])
            self.cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM pair_cost_cache
                WHERE (origin_key, dest_key) IN ({placeholders})
                  AND source NOT LIKE 'Haversine Fallback%'
                """,
                params,
            )
            db_hits += int(self.cursor.fetchone()[0])

        cached = memory_hits + db_hits
        return cached / total, cached, total

    def warm_costs(self, coords):
        unique = []
        seen = set()
        for coord in coords:
            key = self.coord_key(coord)
            if key not in seen:
                unique.append((float(coord[0]), float(coord[1])))
                seen.add(key)

        if len(unique) < 2:
            return False

        group_key = self._table_group_key(unique)
        if group_key in self.table_group_cache:
            self.stats["table_requests_saved"] += 1
            print(
                f"[OSRM Table] skip cached group: locations={len(unique)}, "
                f"saved_requests={self.stats['table_requests_saved']}",
                flush=True,
            )
            return False

        if len(unique) > self.table_limit:
            # The first coordinate is the depot.  Include it in every spatially
            # local, overlapping batch so depot costs and points around chunk
            # boundaries still receive real road costs.  These chunks only warm
            # the cost cache; they do not split or constrain route assignment.
            batches = self._spatial_table_batches(unique)
            limited_batches = batches[:self.table_max_chunks]
            print(
                f"[OSRM Table] spatial warm-up: locations={len(unique)}, "
                f"batches={len(limited_batches)}/{len(batches)}, overlap={self.table_chunk_overlap}",
                flush=True,
            )
            warmed = False
            for batch in limited_batches:
                warmed = self.warm_costs(batch) or warmed
            if len(limited_batches) == len(batches):
                self.table_group_cache.add(group_key)
            return warmed

        coverage, cached_pairs, total_pairs = self._cache_coverage(unique)
        if coverage >= self.table_cache_coverage:
            self.table_group_cache.add(group_key)
            self.stats["table_requests_saved"] += 1
            print(
                f"[OSRM Table] skip cache-covered: locations={len(unique)}, "
                f"coverage={coverage:.1%} ({cached_pairs}/{total_pairs}), "
                f"saved_requests={self.stats['table_requests_saved']}",
                flush=True,
            )
            return False

        print(
            f"[OSRM Table] cache coverage: locations={len(unique)}, "
            f"coverage={coverage:.1%} ({cached_pairs}/{total_pairs}); request needed",
            flush=True,
        )

        coord_string = ";".join([f"{lon},{lat}" for lat, lon in unique])
        url = f"{self.base_url}/table/v1/driving/{coord_string}?annotations=duration,distance"

        try:
            self.table_group_cache.add(group_key)
            self.stats["table_requests"] += 1
            print(
                f"[OSRM Table] request #{self.stats['table_requests']}: "
                f"{len(unique)} locations, cache_hit={self.stats['cache_hits']}, "
                f"memory_hit={self.stats['memory_hits']}, cache_miss={self.stats['cache_misses']}, "
                f"fallback={self.stats['fallbacks']}, saved_requests={self.stats['table_requests_saved']}",
                flush=True,
            )
            resp = requests.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                print(
                    f"[OSRM Table] failed: HTTP {resp.status_code}; fallback will be used when needed",
                    flush=True,
                )
                return False
            data = resp.json()
            if data.get("code") != "Ok":
                print(
                    f"[OSRM Table] failed: code={data.get('code')} message={data.get('message', '')}; "
                    "fallback will be used when needed",
                    flush=True,
                )
                return False
            durations = data.get("durations") or []
            distances = data.get("distances") or []
            if not durations or not distances:
                return False

            for i, origin in enumerate(unique):
                for j, dest in enumerate(unique):
                    if i == j:
                        self._save_cost(origin, dest, 0.0, 0.0, "OSRM Table")
                        continue
                    duration = durations[i][j] if i < len(durations) and j < len(durations[i]) else None
                    distance = distances[i][j] if i < len(distances) and j < len(distances[i]) else None
                    if duration is None or distance is None:
                        continue
                    self._save_cost(origin, dest, duration / 60.0, distance / 1000.0, "OSRM Table")
            self.conn.commit()
            print(
                f"[OSRM Table] completed: stored up to {len(unique) * len(unique)} pair costs",
                flush=True,
            )
            return True
        except Exception as exc:
            print(f"[OSRM Table] error: {type(exc).__name__}: {exc}; fallback will be used", flush=True)
            return False

    def get_cost(self, origin, dest, persist_fallback=True):
        if self.coord_key(origin) == self.coord_key(dest):
            return {"duration": 0.0, "distance": 0.0, "source": "Cache", "used_fallback": False}

        cached = self._get_cached(origin, dest)
        if cached:
            return cached

        if self.allow_pair_lookup and self.warm_costs([origin, dest]):
            cached = self._get_cached(origin, dest)
            if cached:
                return cached

        fallback = self._fallback_cost(origin, dest)
        if self.stats["fallbacks"] <= 20:
            print(
                f"[RoutingCostProvider] fallback #{self.stats['fallbacks']}: "
                f"{self.coord_key(origin)} -> {self.coord_key(dest)}",
                flush=True,
            )
        elif self.stats["fallbacks"] % 10000 == 0:
            print(
                f"[RoutingCostProvider] fallback count={self.stats['fallbacks']}, "
                f"not_saved={self.stats['fallbacks_not_saved']}",
                flush=True,
            )
        if not persist_fallback:
            self.stats["fallbacks_not_saved"] += 1
            fallback["source"] = "Haversine Fallback (Memory)"
            self.memory_cache[(self.coord_key(origin), self.coord_key(dest))] = dict(fallback)
            return fallback
        self._save_cost(origin, dest, fallback["duration"], fallback["distance"], fallback["source"])
        self.conn.commit()
        return fallback

    def route_cost(self, coords):
        duration = 0.0
        distance = 0.0
        sources = []
        used_fallback = False

        for idx in range(len(coords) - 1):
            cost = self.get_cost(coords[idx], coords[idx + 1])
            duration += cost["duration"]
            distance += cost["distance"]
            sources.append(cost["source"])
            used_fallback = used_fallback or cost.get("used_fallback", False)

        source_label = self.describe_sources(sources)
        return {
            "duration": duration,
            "distance": distance,
            "source": source_label,
            "used_fallback": used_fallback,
        }

    def route_geometry(self, coords):
        """Return OSRM road geometry and exact route totals, or an explicit fallback."""
        clean = [(float(lat), float(lon)) for lat, lon in coords if lat is not None and lon is not None]
        if len(clean) < 2:
            return {"coordinates": [[lat, lon] for lat, lon in clean], "duration": 0.0, "distance": 0.0,
                    "used_fallback": True, "source": "Insufficient points"}
        try:
            coordinate_text = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in clean)
            url = f"{self.base_url}/route/v1/driving/{coordinate_text}?overview=full&geometries=geojson&steps=false"
            response = requests.get(url, timeout=self.timeout)
            data = response.json() if response.status_code == 200 else {}
            geometry = (((data.get("routes") or [{}])[0].get("geometry") or {}).get("coordinates") or [])
            if data.get("code") == "Ok" and geometry:
                route = (data.get("routes") or [{}])[0]
                return {"coordinates": [[lat, lon] for lon, lat in geometry],
                        "duration": float(route.get("duration") or 0.0) / 60.0,
                        "distance": float(route.get("distance") or 0.0) / 1000.0,
                        "used_fallback": False, "source": "OSRM Route"}
        except Exception:
            pass
        return {"coordinates": [[lat, lon] for lat, lon in clean], "duration": None, "distance": None,
                "used_fallback": True, "source": "Straight-line fallback"}

    def describe_sources(self, sources):
        ordered = []
        for source in sources:
            if source not in ordered:
                ordered.append(source)
        return " / ".join(ordered) if ordered else "Cache"

    def route_hash(self, coords):
        c_str = "|".join([self.coord_key(coord) for coord in coords])
        return hashlib.md5(c_str.encode()).hexdigest()

    def stats_json(self):
        return json.dumps(self.stats, ensure_ascii=False)
