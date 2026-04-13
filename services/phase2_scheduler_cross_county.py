from __future__ import annotations

from pathlib import Path
import json
import math
import re

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = BASE_DIR / "output"
INPUT_CSV = OUTPUT_DIR / "processed_nodes_phase1.csv"

DAY_COUNT = 6
MAX_MINUTES = 540

DEPOTS = {
    "Wugu": {"code": "Wugu", "name": "五股總部", "lat": 25.07154, "lon": 121.44169},
    "Pingzhen": {"code": "Pingzhen", "name": "平鎮總部", "lat": 24.90703, "lon": 121.226872},
}

DRIVER_CONFIG = {
    "Wugu": 2,
    "Pingzhen": 12,
}


def to_float(value, default=0.0):
    try:
        if value is None:
            return default
        text = str(value).strip()
        if text == "" or text.lower() == "nan":
            return default
        return float(text)
    except Exception:
        return default


def to_int(value, default=0):
    try:
        if value is None:
            return default
        text = str(value).strip()
        if text == "" or text.lower() == "nan":
            return default
        return int(float(text))
    except Exception:
        return default


def clean_text(value):
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return None
    return text


def parse_county(addr):
    addr = str(addr).strip()
    pattern = r'(基隆市|台北市|臺北市|新北市|桃園市|桃園縣|新竹市|新竹縣|苗栗縣|台中市|臺中市|彰化縣|南投縣|雲林縣|嘉義市|嘉義縣|台南市|臺南市|高雄市|屏東縣|宜蘭縣|花蓮縣|台東縣|臺東縣|澎湖縣|金門縣|連江縣)'
    match = re.search(pattern, addr)
    if match:
        return match.group(1).replace("臺", "台")
    if len(addr) >= 3 and addr[2] in ["縣", "市"]:
        return addr[:3].replace("臺", "台")
    return "Unknown"


def get_depot(raw):
    text = str(raw)
    if "五股" in text:
        return "Wugu"
    if "平鎮" in text:
        return "Pingzhen"
    return "Unknown"


def infer_depot_from_county(county):
    if county in ["台北市", "新北市", "基隆市"]:
        return "Wugu"
    return "Pingzhen"


def driver_label(code):
    s = str(code or "").upper()
    if s.startswith("P") and s[1:].isdigit():
        return f"{s}｜平鎮{s[1:].lstrip('0') or '0'}"
    if s.startswith("W") and s[1:].isdigit():
        return f"{s}｜五股{s[1:].lstrip('0') or '0'}"
    return s


def haversine(lat1, lon1, lat2, lon2):
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def route_distance_km(depot, tasks):
    pts = [(depot["lat"], depot["lon"])]
    for task in tasks:
        pts.append((task["lat"], task["lon"]))
    pts.append((depot["lat"], depot["lon"]))
    total = 0.0
    for i in range(1, len(pts)):
        total += haversine(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1])
    return total * 1.25


def route_metrics(depot, tasks):
    dist_km = route_distance_km(depot, tasks) if tasks else 0.0
    drive_min = (dist_km / 35.0) * 60.0 if dist_km > 0 else 0.0
    service_min = sum(task["service_time"] for task in tasks)
    total_min = drive_min + service_min
    counties = sorted({task["county"] for task in tasks if task["county"]})
    return {
        "service_min": round(service_min, 2),
        "drive_min": round(drive_min, 2),
        "dist_km": round(dist_km, 2),
        "total_min": round(total_min, 2),
        "counties": counties,
        "cross_county": len(counties) > 1,
        "overtime_min": round(max(0.0, total_min - MAX_MINUTES), 2),
    }


def nearest_neighbor_order(depot, tasks):
    remaining = [dict(t) for t in tasks]
    ordered = []
    cur_lat = depot["lat"]
    cur_lon = depot["lon"]

    while remaining:
        remaining.sort(key=lambda t: haversine(cur_lat, cur_lon, t["lat"], t["lon"]))
        chosen = remaining.pop(0)
        ordered.append(chosen)
        cur_lat = chosen["lat"]
        cur_lon = chosen["lon"]

    return ordered


def build_tasks(df):
    tasks = []
    counter = 1

    for _, row in df.iterrows():
        county = parse_county(row.get("Address", ""))
        depot = get_depot(row.get("Depot_Raw"))
        if depot == "Unknown":
            depot = infer_depot_from_county(county)

        weekly_1 = to_int(row.get("weekly_1"), 1)
        weekly_2 = to_int(row.get("weekly_2"), 0)
        visits = max(1, weekly_1 + weekly_2)
        service_time_total = to_float(row.get("Service_Time"), 0.0)
        service_time_per_visit = service_time_total / visits if visits else service_time_total

        for visit_idx in range(1, visits + 1):
            tasks.append(
                {
                    "task_id": f"T{counter:05d}",
                    "node_id": clean_text(row.get("Node_ID")) or f"N_{counter:05d}",
                    "lat": to_float(row.get("Lat"), 0.0),
                    "lon": to_float(row.get("Lon"), 0.0),
                    "service_time": round(service_time_per_visit, 2),
                    "county": county,
                    "depot_code": depot,
                    "address": clean_text(row.get("Address")),
                    "freq": clean_text(row.get("Freq")),
                    "visit_idx": visit_idx,
                    "original_id": clean_text(row.get("Original_ID")),
                    "order_id": clean_text(row.get("order_id")),
                }
            )
            counter += 1

    return tasks


def make_route_slots():
    slots = []
    for depot_code, count in DRIVER_CONFIG.items():
        prefix = "W" if depot_code == "Wugu" else "P"
        for n in range(1, count + 1):
            driver = f"{prefix}{n:02d}"
            for day in range(1, DAY_COUNT + 1):
                slots.append(
                    {
                        "driver": driver,
                        "day": day,
                        "depot_code": depot_code,
                        "tasks": [],
                    }
                )
    return slots


def task_order_key(task):
    depot = DEPOTS[task["depot_code"]]
    dist = haversine(depot["lat"], depot["lon"], task["lat"], task["lon"])
    return (task["depot_code"], -task["service_time"], -dist, task["task_id"])


def candidate_score(route, task):
    if route["depot_code"] != task["depot_code"]:
        return None

    depot = DEPOTS[route["depot_code"]]
    new_tasks = route["tasks"] + [task]
    metrics = route_metrics(depot, new_tasks)

    overflow_penalty = max(0.0, metrics["total_min"] - MAX_MINUTES) * 10000
    county_penalty = max(0, len(metrics["counties"]) - 1) * 8.0
    return overflow_penalty + metrics["total_min"] + county_penalty


def assign_cross(tasks):
    route_slots = make_route_slots()
    ordered_tasks = sorted(tasks, key=task_order_key)

    for task in ordered_tasks:
        candidates = []
        for route in route_slots:
            score = candidate_score(route, task)
            if score is not None:
                candidates.append((score, route))

        if not candidates:
            raise RuntimeError(f"無法安排任務 {task['task_id']} 到跨縣市版本")

        candidates.sort(key=lambda x: x[0])
        best_route = candidates[0][1]
        best_route["tasks"].append(task)

    return route_slots


def finalize_routes(route_slots):
    routes = []
    flat_rows = []

    for route in route_slots:
        if not route["tasks"]:
            continue

        depot = DEPOTS[route["depot_code"]].copy()
        ordered = nearest_neighbor_order(depot, route["tasks"])
        metrics = route_metrics(depot, ordered)

        stops = []
        prev_lat = depot["lat"]
        prev_lon = depot["lon"]

        for idx, task in enumerate(ordered, start=1):
            leg_km = haversine(prev_lat, prev_lon, task["lat"], task["lon"]) * 1.25
            leg_min = (leg_km / 35.0) * 60.0 if leg_km > 0 else 0.0

            stop = {
                "seq": idx,
                "task_id": task["task_id"],
                "node_id": task["node_id"],
                "county": task["county"],
                "address": task["address"],
                "lat": task["lat"],
                "lon": task["lon"],
                "service_min": round(task["service_time"], 2),
                "travel_time_min": round(leg_min, 2),
                "travel_dist_km": round(leg_km, 2),
                "freq": task["freq"],
            }
            stops.append(stop)

            flat_rows.append(
                {
                    "driver": route["driver"],
                    "day": route["day"],
                    **stop,
                }
            )

            prev_lat = task["lat"]
            prev_lon = task["lon"]

        routes.append(
            {
                "route_id": f"{route['driver']}-D{route['day']}",
                "driver": route["driver"],
                "driver_label": driver_label(route["driver"]),
                "day": route["day"],
                "depot": depot,
                "stop_count": len(stops),
                "counties": metrics["counties"],
                "cross_county": metrics["cross_county"],
                "metrics": {
                    "service_min": metrics["service_min"],
                    "drive_min": metrics["drive_min"],
                    "dist_km": metrics["dist_km"],
                    "total_min": metrics["total_min"],
                    "overtime_min": metrics["overtime_min"],
                },
                "stops": stops,
            }
        )

    routes.sort(key=lambda r: (r["driver"], r["day"]))
    flat_rows.sort(key=lambda r: (r["driver"], r["day"], r["seq"]))
    return routes, flat_rows


def save_outputs(routes, flat_rows):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "meta": {
            "variant": "cross",
            "label": "可跨縣市",
            "note": "可跨縣市版本，由 phase2_scheduler_cross_county.py 直接輸出。",
        },
        "routes": routes,
    }

    (OUTPUT_DIR / "routes_cross.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary_rows = []
    stop_rows = []

    for route in routes:
        summary_rows.append(
            {
                "route_id": route["route_id"],
                "driver": route["driver"],
                "driver_label": route["driver_label"],
                "day": route["day"],
                "depot_name": route["depot"]["name"],
                "stop_count": route["stop_count"],
                "counties": " / ".join(route["counties"]),
                "cross_county": route["cross_county"],
                "service_min": route["metrics"]["service_min"],
                "drive_min": route["metrics"]["drive_min"],
                "dist_km": route["metrics"]["dist_km"],
                "total_min": route["metrics"]["total_min"],
                "overtime_min": route["metrics"]["overtime_min"],
            }
        )

        for stop in route["stops"]:
            stop_rows.append(
                {
                    "route_id": route["route_id"],
                    "driver": route["driver"],
                    "day": route["day"],
                    "seq": stop["seq"],
                    "task_id": stop["task_id"],
                    "county": stop["county"],
                    "address": stop["address"],
                    "service_min": stop["service_min"],
                    "travel_time_min": stop["travel_time_min"],
                    "travel_dist_km": stop["travel_dist_km"],
                    "lat": stop["lat"],
                    "lon": stop["lon"],
                }
            )

    summary_df = pd.DataFrame(summary_rows)
    stops_df = pd.DataFrame(stop_rows)

    with pd.ExcelWriter(OUTPUT_DIR / "Weekly_Schedule_Summary_cross.xlsx", engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="route_summary", index=False)
        stops_df.to_excel(writer, sheet_name="route_stops", index=False)

    daily_summary = summary_df[
        ["driver", "day", "stop_count", "service_min", "drive_min", "dist_km", "total_min", "overtime_min"]
    ].copy()
    daily_summary.to_excel(OUTPUT_DIR / "Daily_Route_Summary_cross.xlsx", index=False)


def main():
    if not INPUT_CSV.exists():
        raise FileNotFoundError(f"找不到 {INPUT_CSV}")

    df = pd.read_csv(INPUT_CSV)
    tasks = build_tasks(df)
    print(f"Total tasks generated: {len(tasks)}")

    route_slots = assign_cross(tasks)
    routes, flat_rows = finalize_routes(route_slots)
    save_outputs(routes, flat_rows)

    used_routes = len(routes)
    total_stops = sum(r["stop_count"] for r in routes)
    total_service = sum(r["metrics"]["service_min"] for r in routes)
    total_drive = sum(r["metrics"]["drive_min"] for r in routes)
    cross_routes = sum(1 for r in routes if r["cross_county"])

    print("Variant: cross")
    print(f"Routes: {used_routes}")
    print(f"Stops: {total_stops}")
    print(f"Service minutes: {round(total_service, 1)}")
    print(f"Drive minutes: {round(total_drive, 1)}")
    print(f"Cross-county routes: {cross_routes}")
    print("phase2_scheduler_cross_county.py completed successfully")


if __name__ == "__main__":
    main()