from __future__ import annotations

import math
from pathlib import Path

try:
    from routing.services.phase2_scheduler import OSRMClient
    from routing.services.routing_cost_provider import RoutingCostProvider
except ImportError:
    from phase2_scheduler import OSRMClient
    from routing_cost_provider import RoutingCostProvider


def haversine_km(lat1, lon1, lat2, lon2):
    radius_km = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class CrossCompactCosting:
    def __init__(self, label="phase2-cross", max_scan_candidates=200):
        self.label = label
        self.cost_provider = RoutingCostProvider()
        self.osrm = OSRMClient()
        self.local_cache = {}
        self.local_hits = 0
        self.local_calls = 0
        self.max_scan_candidates = max(int(max_scan_candidates or 200), 1)

    def log(self, message):
        print(f"[{self.label}] {message}", flush=True)

    def coord(self, item):
        return (float(item["lat"]), float(item["lon"]))

    def depot_coord(self, depot):
        return (float(depot["lat"]), float(depot["lon"]))

    def pair_key(self, origin, dest):
        return (
            (round(float(origin[0]), 5), round(float(origin[1]), 5)),
            (round(float(dest[0]), 5), round(float(dest[1]), 5)),
        )

    def get_cost(self, origin, dest, persist_fallback=False):
        key = self.pair_key(origin, dest)
        if key in self.local_cache:
            self.local_hits += 1
            return self.local_cache[key]
        self.local_calls += 1
        cost = self.cost_provider.get_cost(origin, dest, persist_fallback=persist_fallback)
        self.local_cache[key] = cost
        return cost

    def warm_tasks(self, depot, tasks, context=""):
        coords = [self.depot_coord(depot)] + [self.coord(t) for t in tasks]
        self.log(f"OSRM Table 預熱開始{context}: locations={len(coords)}")
        self.cost_provider.warm_costs(coords)
        self.log(f"OSRM Table 預熱完成{context}: stats={self.cost_provider.stats_json()}")

    def route_cost(self, depot, tasks, return_to_depot=True):
        if not tasks:
            return {"drive_min": 0.0, "dist_km": 0.0, "source": "Cache", "used_fallback": False}
        coords = [self.depot_coord(depot)] + [self.coord(t) for t in tasks]
        if return_to_depot:
            coords.append(self.depot_coord(depot))
        total_duration = 0.0
        total_distance = 0.0
        sources = []
        used_fallback = False
        persist = len(coords) <= self.cost_provider.table_limit
        for idx in range(len(coords) - 1):
            cost = self.get_cost(coords[idx], coords[idx + 1], persist_fallback=persist)
            total_duration += cost["duration"]
            total_distance += cost["distance"]
            sources.append(cost["source"])
            used_fallback = used_fallback or bool(cost.get("used_fallback"))
        return {
            "drive_min": total_duration,
            "dist_km": total_distance,
            "source": self.cost_provider.describe_sources(sources),
            "used_fallback": used_fallback,
        }

    def candidate_incremental_metrics(self, route, depot, task):
        current = route.get("_metrics") or {
            "service_min": 0.0,
            "drive_min": 0.0,
            "dist_km": 0.0,
        }
        depot_coord = self.depot_coord(depot)
        task_coord = self.coord(task)
        if route["tasks"]:
            last_coord = self.coord(route["tasks"][-1])
            remove = self.get_cost(last_coord, depot_coord, persist_fallback=False)
            add_1 = self.get_cost(last_coord, task_coord, persist_fallback=False)
        else:
            remove = {"duration": 0.0, "distance": 0.0}
            add_1 = self.get_cost(depot_coord, task_coord, persist_fallback=False)
        add_2 = self.get_cost(task_coord, depot_coord, persist_fallback=False)
        service_min = current["service_min"] + float(task["service_time"])
        drive_min = current["drive_min"] - remove["duration"] + add_1["duration"] + add_2["duration"]
        dist_km = current["dist_km"] - remove["distance"] + add_1["distance"] + add_2["distance"]
        counties = set(route.get("_counties") or [])
        if task.get("county"):
            counties.add(task["county"])
        return {
            "service_min": service_min,
            "drive_min": drive_min,
            "dist_km": dist_km,
            "total_min": service_min + drive_min,
            "counties": sorted(counties),
            "cross_county": len(counties) > 1,
            "overtime_min": max(0.0, service_min + drive_min - 540),
        }

    def apply_route_metrics(self, route, metrics):
        route["_metrics"] = {
            "service_min": metrics["service_min"],
            "drive_min": metrics["drive_min"],
            "dist_km": metrics["dist_km"],
        }
        route["_counties"] = set(metrics["counties"])

    def nearest_neighbor_order(self, depot, tasks):
        remaining = [dict(t) for t in tasks]
        ordered = []
        current = self.depot_coord(depot)

        while remaining:
            if len(remaining) > self.max_scan_candidates:
                indexed = sorted(
                    enumerate(remaining),
                    key=lambda pair: haversine_km(current[0], current[1], pair[1]["lat"], pair[1]["lon"]),
                )[: self.max_scan_candidates]
            else:
                indexed = list(enumerate(remaining))
            idx, chosen = min(
                indexed,
                key=lambda pair: self.get_cost(current, self.coord(pair[1]), persist_fallback=False)["duration"],
            )
            ordered.append(chosen)
            current = self.coord(chosen)
            remaining.pop(idx)

        return ordered

    def osrm_route(self, depot, ordered):
        depot_coord = self.depot_coord(depot)
        coords = [depot_coord] + [self.coord(t) for t in ordered] + [depot_coord]
        if len(coords) < 2:
            return {"duration": 0.0, "distance": 0.0, "legs": [], "geometry": None, "source": "Empty Route"}
        return self.osrm.get_route_batch(coords)

    def stats_json(self):
        return self.cost_provider.stats_json()
