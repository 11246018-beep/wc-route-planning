from __future__ import annotations

import copy
import json
import os
from datetime import datetime
from pathlib import Path

from .routing_cost_provider import RoutingCostProvider


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    """Convert route sequence values without failing the dry-run pipeline."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coord(item):
    lat = _number(item.get("lat"), None)
    lon = _number(item.get("lon"), None)
    return None if lat is None or lon is None else (lat, lon)


def _route_coords(route, stops=None):
    stops = list(route.get("stops") or []) if stops is None else list(stops)
    depot = _coord(route.get("depot") or {})
    points = [_coord(stop) for stop in stops]
    if depot is None or any(point is None for point in points):
        return []
    return [depot, *points]


def _route_cost(route, stops, provider):
    coords = _route_coords(route, stops)
    if len(coords) < 2:
        return {"duration": 0.0, "distance": 0.0, "source": "none", "used_fallback": False}
    provider.warm_costs(coords)
    return provider.route_cost(coords)


def _same_point(stop, candidate):
    candidate_id = str(candidate.get("node_id") or "").strip()
    stop_id = str(stop.get("node_id") or "").strip()
    if candidate_id and stop_id and candidate_id == stop_id:
        return True
    return (
        str(stop.get("address") or "").strip() == str(candidate.get("address") or "").strip()
        and round(_number(stop.get("lat")), 5) == round(_number(candidate.get("lat")), 5)
        and round(_number(stop.get("lon")), 5) == round(_number(candidate.get("lon")), 5)
    )


def _normalise_stops(route, stops, cost):
    result = []
    previous = _coord(route.get("depot") or {})
    for seq, raw in enumerate(stops, start=1):
        stop = dict(raw)
        current = _coord(stop)
        leg = {"duration": 0.0, "distance": 0.0}
        if previous is not None and current is not None:
            leg = cost.get_cost(previous, current)
        stop["seq"] = seq
        stop["travel_time_min"] = round(_number(leg.get("duration")), 2)
        stop["travel_dist_km"] = round(_number(leg.get("distance")), 2)
        result.append(stop)
        previous = current
    return result


def insert_candidates(payload, candidates, start_day=1, max_minutes=540, provider=None, locked_prefixes=None,
                      excluded_route_ids=None, allow_cross_county=False):
    """Insert selected points into future route slots without rebuilding existing routes."""
    result = copy.deepcopy(payload if isinstance(payload, dict) else {})
    routes = result.setdefault("routes", [])
    owns_provider = provider is None
    provider = provider or RoutingCostProvider()
    inserted = []
    unassigned = []
    locked_prefixes = locked_prefixes or {}
    excluded_route_ids = {str(value) for value in (excluded_route_ids or set())}

    try:
        for raw_candidate in candidates:
            candidate = dict(raw_candidate)
            visits = max(int(_number(candidate.pop("visit_count", 1), 1)), 1)
            used_days = set()
            variant = str((result.get("meta") or {}).get("variant") or "normal").strip().lower()
            candidate_county = str(candidate.get("county") or "").strip()
            candidate_depot = str(candidate.get("depot_code") or "").strip()
            candidate_start_day = max(
                int(start_day),
                int(_number(candidate.get("original_day"), 0)) + 1 if candidate.get("source") == "skipped" else int(start_day),
            )

            for visit_idx in range(1, visits + 1):
                options = []
                for route in routes:
                    if str(route.get("route_id") or "") in excluded_route_ids:
                        continue
                    day = int(_number(route.get("day"), 0))
                    if day < candidate_start_day or day in used_days:
                        continue
                    route_depot = str((route.get("depot") or {}).get("code") or "").strip()
                    if candidate_depot and route_depot and candidate_depot != route_depot:
                        continue
                    route_counties = {str(value or "").strip() for value in route.get("counties") or [] if str(value or "").strip()}
                    if variant == "normal" and candidate_county and route_counties and candidate_county not in route_counties and not allow_cross_county:
                        continue
                    existing = list(route.get("stops") or [])
                    if any(_same_point(stop, candidate) for stop in existing):
                        continue
                    current_metrics = route.get("metrics") or {}
                    current_total = _number(current_metrics.get("total_min"))
                    current_service = _number(current_metrics.get("service_min"))

                    locked_count = max(int(_number(locked_prefixes.get(str(route.get("route_id") or "")), 0)), 0)
                    for position in range(min(locked_count, len(existing)), len(existing) + 1):
                        proposed = existing[:position] + [candidate] + existing[position:]
                        cost = _route_cost(route, proposed, provider)
                        service = current_service + _number(candidate.get("service_min"), 10.0)
                        total = service + _number(cost.get("duration"))
                        if total > float(max_minutes):
                            continue
                        delta = total - current_total
                        score = (
                            delta,
                            current_total,
                            len(existing),
                            day,
                            str(route.get("driver") or ""),
                        )
                        options.append((score, route, proposed, cost, service, total, position))

                # Pair costs only rank candidates. A complete OSRM route is the
                # hard authority for writing an adjustment, so a fallback or
                # an OSRM total above the daily limit can never be accepted.
                best = None
                for option in sorted(options, key=lambda item: item[0]):
                    score, route, proposed, _, service, _, position = option
                    exact = provider.route_geometry(_route_coords(route, proposed))
                    if exact.get("used_fallback") or exact.get("duration") is None:
                        continue
                    exact_total = service + _number(exact.get("duration"))
                    if exact_total > float(max_minutes) + 1e-9:
                        continue
                    exact_score = (
                        exact_total - _number((route.get("metrics") or {}).get("total_min")),
                        *score[1:],
                    )
                    exact_cost = {
                        "duration": _number(exact.get("duration")),
                        "distance": _number(exact.get("distance")),
                        "source": exact.get("source") or "OSRM Route",
                        "used_fallback": False,
                    }
                    best = (exact_score, route, proposed, exact_cost, service, exact_total, position)
                    break

                if best is None:
                    failed = dict(candidate)
                    failed["visit_idx"] = visit_idx
                    failed["reason"] = "後續路線沒有不超過每日工時上限的插入位置"
                    unassigned.append(failed)
                    continue

                _, route, proposed, cost, service, total, position = best
                route["stops"] = _normalise_stops(route, proposed, provider)
                route["stop_count"] = len(route["stops"])
                route["counties"] = sorted({str(stop.get("county") or "").strip() for stop in route["stops"] if stop.get("county")})
                route["cross_county"] = len(route["counties"]) > 1
                route["metrics"] = {
                    "service_min": round(service, 2),
                    "drive_min": round(_number(cost.get("duration")), 2),
                    "dist_km": round(_number(cost.get("distance")), 2),
                    "total_min": round(total, 2),
                    "overtime_min": round(max(0.0, total - float(max_minutes)), 2),
                }
                used_days.add(int(_number(route.get("day"), 0)))
                inserted.append({
                    "candidate_key": candidate.get("candidate_key"),
                    "node_id": candidate.get("node_id"),
                    "visit_idx": visit_idx,
                    "route_id": route.get("route_id"),
                    "driver": route.get("driver"),
                    "day": route.get("day"),
                    "position": position + 1,
                    "added_minutes": round(best[0][0], 2),
                    "used_fallback": bool(cost.get("used_fallback")),
                })

        meta = result.setdefault("meta", {})
        history = list(meta.get("incremental_adjustments") or [])
        history.append({
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "start_day": int(start_day),
            "inserted_count": len(inserted),
            "unassigned_count": len(unassigned),
        })
        meta["incremental_adjustments"] = history[-20:]
        meta["last_incremental_update"] = history[-1]
        return result, inserted, unassigned
    finally:
        if owns_provider:
            provider.close()


def write_payload_atomic(path, payload, create_backup=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists() and create_backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup = path.with_name(f"{path.stem}.before_incremental_{stamp}{path.suffix}")
        backup.write_bytes(path.read_bytes())
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return backup


def low_workload_routes(payload, threshold_minutes=180, max_minutes=540, same_driver_only=False):
    """List impractically short routes and compatible merge targets."""
    routes = list((payload or {}).get("routes") or [])
    variant = str(((payload or {}).get("meta") or {}).get("variant") or "normal").lower()
    result = []
    for source in routes:
        stops = list(source.get("stops") or [])
        total = _number((source.get("metrics") or {}).get("total_min"))
        if not stops or (len(stops) > 1 and total >= float(threshold_minutes)):
            continue
        depot = str((source.get("depot") or {}).get("code") or "")
        counties = {str(s.get("county") or "").strip() for s in stops if s.get("county")}
        targets = []
        for target in routes:
            if target is source:
                continue
            if same_driver_only and str(target.get("driver") or "") != str(source.get("driver") or ""):
                continue
            target_depot = str((target.get("depot") or {}).get("code") or "")
            target_counties = {str(v or "").strip() for v in target.get("counties") or [] if str(v or "").strip()}
            target_total = _number((target.get("metrics") or {}).get("total_min"))
            service = sum(_number(s.get("service_min"), 10) for s in stops)
            if depot and target_depot and depot != target_depot:
                continue
            if target_total + service > float(max_minutes):
                continue
            targets.append({"route_id": target.get("route_id"), "driver": target.get("driver"),
                            "day": target.get("day"), "stop_count": len(target.get("stops") or []),
                            "total_min": round(target_total, 2),
                            "remaining_min": round(float(max_minutes) - target_total, 2),
                            "requires_cross_county": bool(variant == "normal" and counties and target_counties and not counties.issubset(target_counties))})
        targets.sort(key=lambda x: (x["requires_cross_county"], -x["remaining_min"], x["day"], str(x["driver"])))
        result.append({"route_id": source.get("route_id"), "driver": source.get("driver"),
                       "day": source.get("day"), "depot": source.get("depot"),
                       "stop_count": len(stops), "total_min": round(total, 2),
                       "stops": stops, "target_routes": targets[:20]})
    return sorted(result, key=lambda x: (x["stop_count"], x["total_min"]))


def move_route_stops(payload, source_route_id, target_route_id, max_minutes=540, provider=None, allow_cross_county=False):
    """Move all stops from a short route into one selected route atomically."""
    result = copy.deepcopy(payload if isinstance(payload, dict) else {})
    routes = result.get("routes") or []
    source = next((r for r in routes if str(r.get("route_id")) == str(source_route_id)), None)
    target = next((r for r in routes if str(r.get("route_id")) == str(target_route_id)), None)
    if not source or not target or source is target:
        raise ValueError("找不到來源或目標路線。")
    moving_stops = list(source.get("stops") or [])
    source_before_total = round(_number((source.get("metrics") or {}).get("total_min")), 2)
    target_before_total = round(_number((target.get("metrics") or {}).get("total_min")), 2)
    target_original_coords = [[_number(stop.get("lat")), _number(stop.get("lon"))] for stop in target.get("stops") or []]
    if not moving_stops:
        raise ValueError("來源路線沒有可移動的點位。")
    source_depot = str((source.get("depot") or {}).get("code") or "")
    target_depot = str((target.get("depot") or {}).get("code") or "")
    if source_depot and target_depot and source_depot != target_depot:
        raise ValueError("不同出發站的路線不能合併。")
    variant = str((result.get("meta") or {}).get("variant") or "normal").lower()
    source_counties = {str(s.get("county") or "").strip() for s in moving_stops if s.get("county")}
    target_counties = {str(v or "").strip() for v in target.get("counties") or [] if str(v or "").strip()}
    if variant == "normal" and source_counties and target_counties and not source_counties.issubset(target_counties) and not allow_cross_county:
        raise ValueError("一般模式不能合併到不同縣市的路線。")
    owns_provider = provider is None
    provider = provider or RoutingCostProvider()
    try:
        proposed = list(target.get("stops") or [])
        details = []
        for moving in moving_stops:
            best = None
            for position in range(len(proposed) + 1):
                trial = proposed[:position] + [moving] + proposed[position:]
                cost = _route_cost(target, trial, provider)
                service = sum(_number(s.get("service_min"), 10) for s in trial)
                total = service + _number(cost.get("duration"))
                if total <= float(max_minutes) and (best is None or total < best[0]):
                    best = (total, position, trial, cost, service)
            if best is None:
                raise ValueError(f"合併後會超過每日 {max_minutes} 分鐘，未變更原路線。")
            total, position, proposed, cost, service = best
            details.append({
                "node_id": moving.get("node_id"),
                "address": moving.get("address"),
                "original_seq": moving.get("seq"),
                "position": position + 1,
            })
        target["stops"] = _normalise_stops(target, proposed, provider)
        target["stop_count"] = len(proposed)
        target["counties"] = sorted({str(s.get("county") or "").strip() for s in proposed if s.get("county")})
        target["cross_county"] = len(target["counties"]) > 1
        target["metrics"] = {"service_min": round(service, 2), "drive_min": round(_number(cost.get("duration")), 2),
                             "dist_km": round(_number(cost.get("distance")), 2), "total_min": round(total, 2),
                             "overtime_min": round(max(0.0, total - float(max_minutes)), 2)}
        final_positions = {
            str(stop.get("node_id")): _to_int(stop.get("seq"), 0)
            for stop in target.get("stops") or []
        }
        for detail in details:
            detail["final_target_seq"] = final_positions.get(str(detail.get("node_id")), detail.get("position"))
        routes.remove(source)
        history = list(result.setdefault("meta", {}).get("manual_route_merges") or [])
        history.append({"updated_at": datetime.now().isoformat(timespec="seconds"), "source_route_id": source_route_id,
                        "target_route_id": target_route_id, "moved_stop_count": len(moving_stops),
                        "manual_cross_county_override": bool(allow_cross_county)})
        result["meta"]["manual_route_merges"] = history[-20:]
        geometry = provider.route_geometry(_route_coords(target))
        return result, {"source_route_id": source_route_id, "target_route_id": target_route_id,
                        "moved_stop_count": len(moving_stops), "source_before_total_min": source_before_total,
                        "target_before_total_min": target_before_total, "target_total_min": round(total, 2),
                        "added_minutes": round(total - target_before_total, 2),
                        "cross_county": bool(target.get("cross_county")), "insertions": details,
                        "route_geometry": geometry.get("coordinates") or [],
                        "geometry_fallback": bool(geometry.get("used_fallback")),
                        "geometry_source": geometry.get("source"),
                        "original_stop_coords": target_original_coords}
    finally:
        if owns_provider:
            provider.close()


def recommend_merge_targets(payload, source_route_id, target_route_ids, max_minutes=540, limit=3,
                            allow_cross_county=False):
    """OSRM-score target routes and return the best practical alternatives."""
    provider = RoutingCostProvider()
    options = []
    try:
        for target_id in target_route_ids:
            try:
                updated, summary = move_route_stops(payload, source_route_id, target_id, max_minutes,
                                                    provider=provider, allow_cross_county=allow_cross_county)
            except ValueError:
                continue
            target = next(r for r in updated.get("routes") or [] if str(r.get("route_id")) == str(target_id))
            coords = _route_coords(target)
            quality = "理想" if 360 <= summary["target_total_min"] <= 500 else ("可接受" if summary["target_total_min"] <= max_minutes else "超時")
            options.append({**summary, "driver": target.get("driver"), "day": target.get("day"),
                            "distance_km": round(_number((target.get("metrics") or {}).get("dist_km")), 2),
                            "quality": quality, "used_fallback": bool(provider.route_cost(coords).get("used_fallback")) if coords else False,
                            "route_coords": [[lat, lon] for lat, lon in coords],
                            "route_geometry": summary.get("route_geometry") or [[lat, lon] for lat, lon in coords],
                            "geometry_fallback": bool(summary.get("geometry_fallback")),
                            "moved_coords": [[_number(s.get("lat")), _number(s.get("lon"))] for s in next(r for r in (payload.get("routes") or []) if str(r.get("route_id")) == str(source_route_id)).get("stops") or []]})
        options.sort(key=lambda x: (x["cross_county"], x["used_fallback"], abs(450 - x["target_total_min"]), x["added_minutes"]))
        for index, option in enumerate(options[:limit], 1):
            option["rank"] = index
            option["recommended"] = index == 1
        return options[:limit]
    finally:
        provider.close()
