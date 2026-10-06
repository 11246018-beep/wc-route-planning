"""Domain tool registry for the route-system assistant.

The registry is deliberately small in Phase 1/2.  It wraps capabilities that
already exist instead of duplicating route, ESG, or database logic.  New
simulation/write tools can be added later without changing the planner API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Callable


READ = "READ"
ANALYZE = "ANALYZE"
SIMULATE = "SIMULATE"
VALIDATE = "VALIDATE"
WRITE = "WRITE"
UNDO = "UNDO"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    category: str
    risk_level: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    execute: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]


def _result(tool: str, summary: str, data: Any, *, warnings=None, errors=None, metrics=None,
            source_type="server_context", source_name="AI context", next_step_hints=None,
            success=True) -> dict[str, Any]:
    return {
        "success": bool(success),
        "tool": tool,
        "summary": summary,
        "data": data,
        "warnings": list(warnings or []),
        "errors": list(errors or []),
        "source": {"type": source_type, "name": source_name, "updated_at": None},
        "metrics": dict(metrics or {}),
        "next_step_hints": list(next_step_hints or []),
    }


def _legacy(action: str, tool: str, context: dict[str, Any], arguments: dict[str, Any],
            previous: dict[str, Any]) -> dict[str, Any]:
    """Call the existing one-action adapter and normalize its output."""
    from .orchestrator import run_tool

    raw = run_tool(action, str(context.get("original_question") or ""), context)
    return _result(
        tool,
        raw.get("message") or f"已執行 {tool}",
        raw,
        warnings=[raw.get("apply_blocked_reason")] if raw.get("apply_blocked_reason") else [],
        errors=[raw.get("verification_error")] if raw.get("verification_error") else [],
        source_name="existing route-system domain adapter",
        success=raw.get("status") not in {"error", "blocked"},
    )


def _company_overview(context, arguments, previous):
    common = context.get("common") or {}
    return _result("get_company_overview", "已取得目前登入公司的基本資料。", {
        "company": common.get("company"),
        "counts": common.get("counts"),
        "schedule_settings": common.get("schedule_settings"),
    })


def _data_availability(context, arguments, previous):
    data = context.get("data") or {}
    availability = data.get("data_availability") or {}
    if not availability:
        availability = {
            "driver_workload": {"available": bool(data.get("driver_weekly_load") or data.get("daily_route_records"))},
            "current_routes": {"available": bool((data.get("variants") or {}).get("normal", {}).get("routes"))},
            "route_level_baseline": {"available": bool(data.get("carbon_reduction_analysis", {}).get("items"))},
        }
    return _result("get_data_availability", "已檢查目前工具可使用的資料範圍。", {
        "availability": availability,
        "query_constraints": data.get("query_constraints") or {},
        "company_key": (context.get("common") or {}).get("company", {}).get("key") or context.get("company_key"),
    })


def _driver_summary(context, arguments, previous):
    data = context.get("data") or {}
    rankings = data.get("driver_rankings") or {}
    return _result("get_driver_summary", "已取得司機工作量摘要。", {
        "highest_weekly_total": rankings.get("highest_weekly_total") or [],
        "lowest_weekly_total": rankings.get("lowest_weekly_total") or [],
        "highest_daily_total": rankings.get("highest_daily_total") or [],
    })


def _route_details(context, arguments, previous):
    data = context.get("data") or {}
    variant = arguments.get("variant") or data.get("requested_variant") or "normal"
    variant_data = (data.get("variants") or {}).get(variant) or {}
    constraints = data.get("query_constraints") or {}
    route_ids = set(arguments.get("route_ids") or constraints.get("route_ids") or [])
    driver_ids = {str(value).upper() for value in (arguments.get("driver_ids") or constraints.get("driver_ids") or [])}
    day = arguments.get("day", constraints.get("day"))
    routes = []
    for route in variant_data.get("routes") or []:
        if route_ids and str(route.get("route_id") or "").upper() not in route_ids:
            continue
        if driver_ids and str(route.get("driver") or "").upper() not in driver_ids:
            continue
        if day is not None and str(route.get("day")) != str(day):
            continue
        routes.append(route)
    limit = min(max(int(arguments.get("limit", 20)), 1), 50)
    return _result("get_route_details", f"已取得 {variant} 路線摘要。", {
        "variant": variant,
        "route_summary": variant_data.get("route_summary") or {},
        "route_version": variant_data.get("route_version"),
        "file_used": variant_data.get("file_used"),
        "routes": routes[:limit],
        "returned_count": min(len(routes), limit),
    })


def _driver_filter(context, arguments):
    data = context.get("data") or {}
    constraints = data.get("query_constraints") or {}
    requested = arguments.get("driver_id") or ((constraints.get("driver_ids") or [None])[0])
    return str(requested or "").strip().upper()


def _driver_workload(context, arguments, previous):
    data = context.get("data") or {}
    driver_id = _driver_filter(context, arguments)
    weekly = list(data.get("driver_weekly_load") or [])
    daily = list(data.get("daily_route_records") or [])
    if driver_id:
        weekly = [row for row in weekly if str(row.get("driver") or row.get("driver_code") or "").upper() == driver_id]
        daily = [row for row in daily if str(row.get("司機") or row.get("driver") or row.get("driver_code") or "").upper() == driver_id]
    weekly_rows = []
    for row in weekly[:20]:
        weekly_rows.append({
            "driver": row.get("driver") or row.get("driver_code"),
            "driver_label": row.get("driver_label"),
            "weekly_total_min": row.get("weekly_total_min"),
            "used_days": row.get("used_days"),
            "avg_per_used_day_min": row.get("avg_per_used_day_min"),
        })
    daily_rows = []
    for row in daily[:50]:
        daily_rows.append({
            "driver": row.get("司機") or row.get("driver"),
            "day": row.get("天數") or row.get("day"),
            "route_id": row.get("route_id"),
            "total_min": row.get("總工時_分") or row.get("total_min"),
            "service_min": row.get("總服務時間_分") or row.get("service_min"),
            "drive_min": row.get("總車程_分") or row.get("drive_min"),
            "distance_km": row.get("總距離_公里") or row.get("distance_km"),
            "stop_count": row.get("總站數") or row.get("stop_count"),
        })
    scope = arguments.get("scope") or (data.get("query_constraints") or {}).get("scope") or "unspecified"
    warnings = []
    if scope == "unspecified":
        warnings.append("使用者未指定單日或一週；結果同時列出 daily 與 weekly，未自行混用。")
    return _result("get_driver_workload", "已分開整理司機單日與一週工時。", {
        "action": "driver_workload",
        "driver_id": driver_id or None,
        "scope": scope,
        "weekly": weekly_rows,
        "daily": daily_rows,
        "population": data.get("driver_scope") or {"weekly_records": len(data.get("driver_weekly_load") or [])},
    }, warnings=warnings, metrics={"weekly_count": len(weekly_rows), "daily_count": len(daily_rows)})


def _driver_routes(context, arguments, previous):
    data = context.get("data") or {}
    driver_id = _driver_filter(context, arguments)
    variant = arguments.get("variant") or data.get("requested_variant") or "normal"
    routes = (data.get("variants") or {}).get(variant, {}).get("routes") or []
    if driver_id:
        routes = [route for route in routes if str(route.get("driver") or "").upper() == driver_id]
    if arguments.get("day") is not None:
        routes = [route for route in routes if str(route.get("day")) == str(arguments["day"])]
    limit = min(max(int(arguments.get("limit", 30)), 1), 50)
    return _result("get_driver_routes", "已取得指定司機的路線與點位資料。", {
        "driver_id": driver_id or None, "variant": variant, "routes": routes[:limit]
    }, metrics={"returned_count": min(len(routes), limit)})


def _rank_driver_imbalance(context, arguments, previous):
    data = context.get("data") or {}
    daily_rows = list(data.get("daily_route_records") or [])
    grouped = {}
    for row in daily_rows:
        driver = str(row.get("司機") or row.get("driver") or row.get("driver_code") or "").strip()
        if not driver:
            continue
        try:
            total = float(row.get("總工時_分") if row.get("總工時_分") is not None else row.get("total_min"))
        except (TypeError, ValueError):
            continue
        grouped.setdefault(driver, []).append(total)
    ranked = []
    for driver, values in grouped.items():
        if not values:
            continue
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        standard_deviation = math.sqrt(variance)
        ranked.append({
            "driver": driver,
            "days": len(values),
            "weekly_total_min": round(sum(values), 2),
            "average_daily_min": round(mean, 2),
            "min_daily_min": round(min(values), 2),
            "max_daily_min": round(max(values), 2),
            "daily_range_min": round(max(values) - min(values), 2),
            "standard_deviation_min": round(standard_deviation, 2),
            "coefficient_of_variation": round(standard_deviation / mean, 4) if mean else 0,
        })
    ranked.sort(key=lambda row: (row["coefficient_of_variation"], row["daily_range_min"]), reverse=True)
    limit = min(max(int(arguments.get("limit", 10)), 1), 50)
    return _result("rank_driver_imbalance", "已依單日工時離散程度完成司機工作量不均衡排名。", {
        "metric": "coefficient_of_variation_then_daily_range",
        "items": ranked[:limit],
        "limit": limit,
    }, metrics={"returned_count": min(limit, len(ranked))})


def _assess_backup_drivers(context, arguments, previous):
    data = context.get("data") or {}
    settings = (context.get("common") or {}).get("schedule_settings") or {}
    max_minutes = float(arguments.get("max_minutes") or settings.get("daily_work_minutes") or 540)
    weekly_rows = list(data.get("driver_weekly_load") or [])
    daily_rows = list(data.get("daily_route_records") or [])
    daily_by_driver = {}
    for row in daily_rows:
        driver = str(row.get("司機") or row.get("driver") or row.get("driver_code") or "").strip().upper()
        if not driver:
            continue
        try:
            total = float(row.get("總工時_分") if row.get("總工時_分") is not None else row.get("total_min"))
        except (TypeError, ValueError):
            continue
        daily_by_driver.setdefault(driver, []).append(total)
    candidates = []
    for row in weekly_rows:
        driver = str(row.get("driver") or row.get("driver_code") or "").strip().upper()
        values = daily_by_driver.get(driver, [])
        if not driver or not values:
            continue
        average = sum(values) / len(values)
        maximum = max(values)
        if maximum > max_minutes:
            continue
        candidates.append({
            "driver": driver,
            "driver_label": row.get("driver_label"),
            "weekly_total_min": row.get("weekly_total_min"),
            "used_days": len(values),
            "average_daily_min": round(average, 2),
            "max_daily_min": round(maximum, 2),
            "remaining_to_daily_limit_min": round(max_minutes - maximum, 2),
            "status": "workload_candidate_only",
        })
    candidates.sort(key=lambda item: (item["average_daily_min"], item["max_daily_min"]))
    limit = min(max(int(arguments.get("limit", 10)), 1), 50)
    return _result("assess_backup_driver_candidates", "已依工時與每日餘裕找出備援人員候選，但尚未確認實際可出勤資格。", {
        "items": candidates[:limit],
        "limit": limit,
        "eligibility": "candidate_only",
        "unverified_checks": ["當日是否可出勤", "請假與排班狀態", "車輛與人員資格", "場站與縣市限制", "接手後路線重新排序與 OSRM 驗證"],
    }, warnings=["這是依工時資料的候選名單，不等於已確認可擔任儲備人員。"], metrics={"returned_count": min(limit, len(candidates))})


def _rank_routes(context, arguments, previous):
    data = context.get("data") or {}
    variant = arguments.get("variant") or data.get("requested_variant") or "normal"
    metric = arguments.get("metric") or (data.get("query_constraints") or {}).get("metric") or "work_time"
    routes = list((data.get("variants") or {}).get(variant, {}).get("routes") or [])
    def value(route):
        metrics = route.get("metrics") or {}
        mapping = {"work_time": metrics.get("total_min"), "distance": metrics.get("dist_km"), "stops": route.get("stop_count"), "carbon": (metrics.get("dist_km") or 0) * 0.21}
        try:
            return float(mapping.get(metric) or 0)
        except (TypeError, ValueError):
            return 0.0
    limit = min(max(int(arguments.get("limit", 10)), 1), 50)
    ranked = sorted(routes, key=value, reverse=True)[:limit]
    items = [{"route_id": r.get("route_id"), "driver": r.get("driver"), "day": r.get("day"), "metric": metric, "value": round(value(r), 2), "total_min": (r.get("metrics") or {}).get("total_min"), "distance_km": (r.get("metrics") or {}).get("dist_km"), "stop_count": r.get("stop_count")} for r in ranked]
    return _result("rank_routes", f"已依 {metric} 完成路線排名。", {"variant": variant, "metric": metric, "items": items}, metrics={"returned_count": len(items)})


def _compare_variants(context, arguments, previous):
    data = context.get("data") or {}
    rows = []
    for variant, item in (data.get("variants") or {}).items():
        summary = item.get("route_summary") or {}
        totals = summary.get("totals") or {}
        esg = item.get("esg") or {}
        routes = item.get("routes") or []
        if not totals and routes:
            totals = {
                "route_count": len(routes),
                "total_work_min": round(sum(float((route.get("metrics") or {}).get("total_min") or 0) for route in routes), 2),
                "total_distance_km": round(sum(float((route.get("metrics") or {}).get("dist_km") or 0) for route in routes), 2),
            }
        rows.append({"variant": variant, "label": item.get("label"), "route_count": totals.get("route_count"), "total_work_min": totals.get("total_work_min"), "total_distance_km": totals.get("total_distance_km"), "estimated_co2_kg": esg.get("estimated_co2_kg")})
    return _result("compare_route_variants", "已完成路線模式比較。", {"variants": rows})


def _validate_schedule(context, arguments, previous):
    data = context.get("data") or {}
    variant = arguments.get("variant") or data.get("requested_variant") or "normal"
    max_minutes = arguments.get("max_minutes") or ((context.get("common") or {}).get("schedule_settings") or {}).get("daily_work_minutes") or 540
    routes = (data.get("variants") or {}).get(variant, {}).get("routes") or []
    overtime = []
    missing_metrics = []
    for route in routes:
        metrics = route.get("metrics") or {}
        total = metrics.get("total_min")
        if total is None:
            missing_metrics.append(route.get("route_id"))
        elif float(total) > float(max_minutes):
            overtime.append({"route_id": route.get("route_id"), "total_min": total, "overtime_min": round(float(total) - float(max_minutes), 2)})
    return _result("validate_schedule", "已完成每日工時與資料完整度檢查。", {"variant": variant, "max_minutes": max_minutes, "overtime_routes": overtime, "missing_metrics_routes": missing_metrics, "valid": not overtime and not missing_metrics}, warnings=["存在超時路線" ] if overtime else [])


def _validate_point_integrity(context, arguments, previous):
    data = context.get("data") or {}
    variant = arguments.get("variant") or data.get("requested_variant") or "normal"
    routes = (data.get("variants") or {}).get(variant, {}).get("routes") or []
    occurrences = {}
    missing = []
    for route in routes:
        for stop in route.get("stops") or []:
            node_id = str(stop.get("node_id") or stop.get("task_id") or "").strip()
            if not node_id:
                missing.append({"route_id": route.get("route_id"), "seq": stop.get("seq")})
            else:
                occurrences.setdefault(node_id, []).append(route.get("route_id"))
    duplicates = {key: routes for key, routes in occurrences.items() if len(routes) > 1}
    return _result("validate_point_integrity", "已完成點位遺失與重複檢查。", {"variant": variant, "duplicate_points": duplicates, "missing_id_points": missing, "valid": not duplicates and not missing})


def _carbon_summary(context, arguments, previous):
    data = context.get("data") or {}
    return _result("get_carbon_summary", "已取得路線模式碳排總覽。", {
        "action": "carbon_summary",
        "variants": data.get("variants") or {},
        "lowest_carbon_variant": data.get("lowest_carbon_variant"),
        "highest_distance_routes": data.get("carbon_route_rankings") or data.get("highest_distance_routes") or [],
        "carbon_route_rankings": data.get("carbon_route_rankings") or [],
    })


def _carbon_rank(context, arguments, previous):
    data = context.get("data") or {}
    rows = data.get("carbon_route_rankings") or data.get("highest_distance_routes") or []
    limit = arguments.get("limit", 10)
    try:
        limit = min(max(int(limit), 1), 50)
    except (TypeError, ValueError):
        return _result("rank_carbon_routes", "碳排排名參數無效。", {}, errors=["limit 必須是整數"], success=False)
    return _result("rank_carbon_routes", f"已完成 {min(limit, len(rows))} 條路線的碳排排名。", {
        "action": "carbon_summary",
        "variants": data.get("variants") or {},
        "lowest_carbon_variant": data.get("lowest_carbon_variant"),
        "highest_distance_routes": rows[:limit],
        "carbon_route_rankings": rows[:limit],
        "items": rows[:limit],
        "limit": limit,
        "calculation": "route distance × server-side co2_kg_per_km",
    }, metrics={"returned_count": min(limit, len(rows))})


def _carbon_reduction(context, arguments, previous):
    data = context.get("data") or {}
    analysis = data.get("carbon_reduction_analysis") or {}
    import re
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", str(context.get("original_question") or ""))
    target_pct = float(match.group(1)) if match else None
    normal_esg = (data.get("variants") or {}).get("normal", {}).get("esg") or {}
    current_total = float(normal_esg.get("estimated_co2_kg") or 0)
    target_reduction = round(current_total * target_pct / 100, 2) if target_pct is not None else None
    if analysis.get("status") != "verified":
        return _result("calculate_route_carbon_reduction", "目前無法計算逐路線碳排減量。", {
            "action": "carbon_reduction",
            "status": "insufficient_baseline",
            "target_reduction_pct": target_pct,
            "current_total_co2_kg": round(current_total, 2),
            "target_reduction_kg": target_reduction,
            "items": [],
            "current_high_carbon_routes": (data.get("carbon_route_rankings") or data.get("highest_distance_routes") or [])[:10],
            "reason": analysis.get("reason") or "缺少可對應的基準路線資料。",
            "required_data": ["相同 route_id 的基準路線距離", "目前路線距離", "排放係數"],
        }, warnings=[analysis.get("reason") or "缺少可對應的基準路線資料。"])
    limit = min(max(int(arguments.get("limit", 10)), 1), 50)
    return _result("calculate_route_carbon_reduction", "已完成逐路線碳排減量計算。", {
        "action": "carbon_reduction",
        "status": "verified",
        "items": (analysis.get("items") or [])[:limit],
        "limit": limit,
        "calculation": "max(baseline_distance - current_distance, 0) × emission_factor",
    }, metrics={"returned_count": min(limit, len(analysis.get("items") or []))})


def _address_parts(address):
    """Return a conservative Taiwan county/district pair from an address."""
    import re
    text = str(address or "").strip().replace("台", "臺")
    county_match = re.search(r"(.{2,3}(?:市|縣))", text)
    county = county_match.group(1) if county_match else ""
    remainder = text[county_match.end():] if county_match else text
    district_match = re.search(r"(.{1,4}?(?:區|鄉|鎮|市))", remainder)
    district = district_match.group(1) if district_match else ""
    return county, district


def _normal_text(value):
    return str(value or "").strip().replace("台", "臺").lower()


def _search_service_points(context, arguments, previous):
    common = context.get("common") or {}
    points = list(common.get("service_points") or common.get("sample_service_points") or [])
    query = _normal_text(arguments.get("query"))
    county_filter = _normal_text(arguments.get("county"))
    district_filter = _normal_text(arguments.get("district"))
    depot_filter = _normal_text(arguments.get("depot"))
    matched = []
    district_counts = {}
    county_counts = {}
    for point in points:
        county, district = _address_parts(point.get("address"))
        haystack = _normal_text(" ".join(str(point.get(key) or "") for key in ("id", "client_name", "address", "depot")))
        if query and query not in haystack:
            continue
        if county_filter and county_filter not in _normal_text(county):
            continue
        if district_filter and district_filter not in _normal_text(district):
            continue
        if depot_filter and depot_filter not in _normal_text(point.get("depot")):
            continue
        item = dict(point)
        item["county"] = county
        item["district"] = district
        matched.append(item)
        county_counts[county or "未辨識"] = county_counts.get(county or "未辨識", 0) + 1
        district_counts[district or "未辨識"] = district_counts.get(district or "未辨識", 0) + 1
    limit = min(max(int(arguments.get("limit", 50)), 1), 200)
    return _result("search_service_points", f"找到 {len(matched)} 個符合條件的公司點位。", {
        "filters": {"query": arguments.get("query"), "county": arguments.get("county"),
                    "district": arguments.get("district"), "depot": arguments.get("depot")},
        "total_count": len(matched), "returned_count": min(limit, len(matched)),
        "by_county": county_counts, "by_district": district_counts, "items": matched[:limit],
    }, metrics={"matched_count": len(matched)}, source_name="company service points")


def _search_routes_by_location(context, arguments, previous):
    data = context.get("data") or {}
    variant = str(arguments.get("variant") or data.get("requested_variant") or "normal")
    routes = list((data.get("variants") or {}).get(variant, {}).get("routes") or [])
    county_filter = _normal_text(arguments.get("county"))
    district_filter = _normal_text(arguments.get("district"))
    query = _normal_text(arguments.get("query"))
    driver = _normal_text(arguments.get("driver"))
    day = arguments.get("day")
    results = []
    for route in routes:
        if driver and driver != _normal_text(route.get("driver")):
            continue
        if day is not None and str(route.get("day")) != str(day):
            continue
        matched_stops = []
        for stop in route.get("stops") or []:
            county, district = _address_parts(stop.get("address"))
            haystack = _normal_text(" ".join(str(stop.get(key) or "") for key in ("node_id", "task_id", "address", "county")))
            if county_filter and county_filter not in _normal_text(county or stop.get("county")):
                continue
            if district_filter and district_filter not in _normal_text(district):
                continue
            if query and query not in haystack:
                continue
            matched_stops.append({**stop, "county": county or stop.get("county"), "district": district})
        location_filter_used = bool(county_filter or district_filter or query)
        if location_filter_used and not matched_stops:
            continue
        metrics = route.get("metrics") or {}
        results.append({
            "route_id": route.get("route_id"), "driver": route.get("driver"), "day": route.get("day"),
            "depot": route.get("depot"), "total_min": metrics.get("total_min"),
            "distance_km": metrics.get("dist_km"), "stop_count": route.get("stop_count"),
            "matched_stop_count": len(matched_stops), "matched_stops": matched_stops[:50],
        })
    limit = min(max(int(arguments.get("limit", 30)), 1), 100)
    return _result("search_routes_by_location", f"找到 {len(results)} 條符合條件的路線。", {
        "variant": variant, "total_count": len(results), "routes": results[:limit],
    }, metrics={"matched_route_count": len(results)}, source_name=f"{variant} route output")


def _analyze_area_coverage(context, arguments, previous):
    route_result = _search_routes_by_location(context, arguments, previous)
    routes = (route_result.get("data") or {}).get("routes") or []
    districts = {}
    drivers = {}
    total_stops = 0
    for route in routes:
        for stop in route.get("matched_stops") or []:
            district = stop.get("district") or "未辨識"
            districts[district] = districts.get(district, 0) + 1
            total_stops += 1
        driver = str(route.get("driver") or "未辨識")
        drivers.setdefault(driver, {"route_count": 0, "matched_stop_count": 0, "days": set()})
        drivers[driver]["route_count"] += 1
        drivers[driver]["matched_stop_count"] += int(route.get("matched_stop_count") or 0)
        drivers[driver]["days"].add(route.get("day"))
    driver_rows = [{"driver": key, "route_count": value["route_count"],
                    "matched_stop_count": value["matched_stop_count"],
                    "days": sorted(day for day in value["days"] if day is not None)}
                   for key, value in drivers.items()]
    driver_rows.sort(key=lambda row: row["matched_stop_count"], reverse=True)
    district_rows = [{"district": key, "stop_count": value} for key, value in districts.items()]
    district_rows.sort(key=lambda row: row["stop_count"], reverse=True)
    return _result("analyze_area_coverage", "已依目前正式路線整理區域覆蓋情況。", {
        "county": arguments.get("county"), "district": arguments.get("district"),
        "route_count": len(routes), "matched_stop_count": total_stops,
        "districts": district_rows, "drivers": driver_rows, "routes": routes,
    }, metrics={"route_count": len(routes), "stop_count": total_stops}, source_name="current route output")


def _dispatch_adapter(action, tool):
    return lambda context, arguments, previous: _legacy(action, tool, context, arguments, previous)


def _build_registry() -> dict[str, ToolDefinition]:
    common_input = {"type": "object", "properties": {"variant": {"type": "string"}}}
    common_output = {"type": "object", "required": ["success", "tool", "summary", "data"]}
    return {
        "search_service_points": ToolDefinition(
            "search_service_points", "搜尋目前公司的服務點，可依關鍵字、縣市、行政區或倉庫篩選並統計。",
            READ, "low", {"type": "object", "properties": {
                "query": {"type": "string"}, "county": {"type": "string"},
                "district": {"type": "string"}, "depot": {"type": "string"}, "limit": {"type": "integer"}}},
            common_output, _search_service_points),
        "search_routes_by_location": ToolDefinition(
            "search_routes_by_location", "搜尋正式路線，可依縣市、行政區、地址、司機、天數與模式篩選。",
            READ, "low", {"type": "object", "properties": {
                "query": {"type": "string"}, "county": {"type": "string"},
                "district": {"type": "string"}, "driver": {"type": "string"},
                "day": {"type": "integer"}, "variant": {"type": "string"}, "limit": {"type": "integer"}}},
            common_output, _search_routes_by_location),
        "analyze_area_coverage": ToolDefinition(
            "analyze_area_coverage", "依縣市或行政區分析正式路線涵蓋區域、點位數、司機與工時。詢問清理區域時優先使用。",
            ANALYZE, "low", {"type": "object", "properties": {
                "county": {"type": "string"}, "district": {"type": "string"},
                "query": {"type": "string"}, "variant": {"type": "string"}, "limit": {"type": "integer"}}},
            common_output, _analyze_area_coverage),
        "get_company_overview": ToolDefinition("get_company_overview", "查詢目前登入公司的基本資料與排程設定。", READ, "low", {"type": "object", "properties": {}}, common_output, _company_overview),
        "get_data_availability": ToolDefinition("get_data_availability", "檢查各 Domain Tool 所需資料是否存在、筆數與限制。", READ, "low", {"type": "object", "properties": {}}, common_output, _data_availability),
        "get_driver_summary": ToolDefinition("get_driver_summary", "查詢司機週工時、日工時與工作量排名。", READ, "low", common_input, common_output, _driver_summary),
        "get_driver_workload": ToolDefinition("get_driver_workload", "查詢指定司機，明確分開單日與一週工時。", READ, "low", {"type": "object", "properties": {"driver_id": {"type": "string"}, "scope": {"type": "string"}}}, common_output, _driver_workload),
        "get_driver_routes": ToolDefinition("get_driver_routes", "查詢指定司機的日期、路線與站點。", READ, "low", {"type": "object", "properties": {"driver_id": {"type": "string"}, "variant": {"type": "string"}, "day": {"type": "integer"}, "limit": {"type": "integer"}}}, common_output, _driver_routes),
        "rank_driver_imbalance": ToolDefinition("rank_driver_imbalance", "以變異係數、標準差與日工時範圍計算司機工作量不均衡程度。", ANALYZE, "low", {"type": "object", "properties": {"limit": {"type": "integer"}}}, common_output, _rank_driver_imbalance),
        "assess_backup_driver_candidates": ToolDefinition("assess_backup_driver_candidates", "依工時餘裕找出備援司機候選；不宣告出勤、資格或接手路線已驗證。", ANALYZE, "medium", {"type": "object", "properties": {"limit": {"type": "integer"}, "max_minutes": {"type": "number"}}}, common_output, _assess_backup_drivers),
        "get_route_details": ToolDefinition("get_route_details", "取得指定模式的路線摘要與目前版本。", READ, "low", common_input, common_output, _route_details),
        "get_unassigned_points": ToolDefinition("get_unassigned_points", "查詢目前未排入的點位與縣市摘要。", READ, "low", common_input, common_output, _dispatch_adapter("unassigned_points", "get_unassigned_points")),
        "analyze_overtime_routes": ToolDefinition("analyze_overtime_routes", "分析超過每日工時上限的路線。", ANALYZE, "low", common_input, common_output, _dispatch_adapter("overtime_routes", "analyze_overtime_routes")),
        "get_carbon_summary": ToolDefinition("get_carbon_summary", "取得公司或路線模式的碳排總覽；不負責逐路線排名。", READ, "low", common_input, common_output, _carbon_summary),
        "rank_carbon_routes": ToolDefinition("rank_carbon_routes", "依 server-side 路線距離與排放係數排列單一路線碳排。", ANALYZE, "low", {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}}, common_output, _carbon_rank),
        "calculate_route_carbon_reduction": ToolDefinition("calculate_route_carbon_reduction", "比較相同 route_id 的基準與目前距離，計算逐路線碳排減量；沒有可對應基準時拒絕猜測。", ANALYZE, "low", {"type": "object", "properties": {"limit": {"type": "integer"}}}, common_output, _carbon_reduction),
        "rank_routes": ToolDefinition("rank_routes", "依工時、距離、站點數或碳排排列路線。", ANALYZE, "low", {"type": "object", "properties": {"variant": {"type": "string"}, "metric": {"type": "string"}, "limit": {"type": "integer"}}}, common_output, _rank_routes),
        "compare_route_variants": ToolDefinition("compare_route_variants", "比較不同路線模式的工時、距離與碳排。", ANALYZE, "low", {"type": "object", "properties": {}}, common_output, _compare_variants),
        "validate_schedule": ToolDefinition("validate_schedule", "驗證路線是否超過每日工時上限，以及工時資料是否完整。", VALIDATE, "medium", {"type": "object", "properties": {"variant": {"type": "string"}, "max_minutes": {"type": "number"}}}, common_output, _validate_schedule),
        "validate_point_integrity": ToolDefinition("validate_point_integrity", "驗證點位是否遺失、缺少 ID 或重複出現在多條路線。", VALIDATE, "medium", {"type": "object", "properties": {"variant": {"type": "string"}}}, common_output, _validate_point_integrity),
        "get_route_diagnostics": ToolDefinition("get_route_diagnostics", "取得目前路線候選、工時與路線模式診斷資料。", ANALYZE, "low", common_input, common_output, _dispatch_adapter("route_merge_analysis", "get_route_diagnostics")),
        "simulate_route_change": ToolDefinition("simulate_route_change", "根據現有資料建立調整候選；不修改正式路線。", SIMULATE, "medium", {"type": "object", "properties": {}}, common_output, _dispatch_adapter("route_change_simulation", "simulate_route_change")),
        "analyze_cleaning_records": ToolDefinition("analyze_cleaning_records", "分析清潔紀錄與連續異常點位。", ANALYZE, "low", {"type": "object", "properties": {}}, common_output, _dispatch_adapter("cleaning_summary", "analyze_cleaning_records")),
    }


TOOL_REGISTRY = _build_registry()


def get_tool(name: str) -> ToolDefinition | None:
    return TOOL_REGISTRY.get(str(name or "").strip())


def validate_arguments(definition: ToolDefinition, arguments: Any) -> list[str]:
    if not isinstance(arguments, dict):
        return [f"{definition.name}.arguments 必須是 object"]
    schema = definition.input_schema or {}
    properties = schema.get("properties") or {}
    errors = []
    for required in schema.get("required") or []:
        if required not in arguments:
            errors.append(f"{definition.name} 缺少必要參數：{required}")
    for key, value in arguments.items():
        spec = properties.get(key) or {}
        expected = spec.get("type")
        if expected == "integer" and not isinstance(value, int):
            errors.append(f"{definition.name}.{key} 必須是 integer")
        if expected == "string" and not isinstance(value, str):
            errors.append(f"{definition.name}.{key} 必須是 string")
        if isinstance(value, int) and spec.get("minimum") is not None and value < spec["minimum"]:
            errors.append(f"{definition.name}.{key} 小於允許下限")
        if isinstance(value, int) and spec.get("maximum") is not None and value > spec["maximum"]:
            errors.append(f"{definition.name}.{key} 超過允許上限")
    return errors


def list_tools(*, include_write=False) -> list[dict[str, Any]]:
    return [
        {"name": item.name, "description": item.description, "category": item.category,
         "risk_level": item.risk_level, "input_schema": item.input_schema,
         "output_schema": item.output_schema}
        for item in TOOL_REGISTRY.values()
        if include_write or item.category not in {SIMULATE, WRITE, UNDO}
    ]
