from __future__ import annotations

from pathlib import Path
import json
import math
import os
import re
import sys

import pandas as pd

try:
    from .driver_roster import build_schedule_driver_slots, schedule_sort_key
except ImportError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from routing.services.driver_roster import build_schedule_driver_slots, schedule_sort_key


BASE_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = BASE_DIR / "output"
INPUT_CSV = OUTPUT_DIR / "processed_nodes_phase1.csv"

def env_int(name, default):
    try:
        value = os.environ.get(name)
        if value is None or str(value).strip() == "":
            return default
        return max(int(float(value)), 1)
    except Exception:
        return default


def env_float(name, default=None):
    try:
        value = os.environ.get(name)
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except Exception:
        return default


DAY_COUNT = env_int("DISPATCH_SCHEDULE_DAYS", 6)
MAX_MINUTES = env_int("DISPATCH_DAILY_WORK_MINUTES", 540)
DEFAULT_SERVICE_MINUTES = env_int("DISPATCH_DEFAULT_SERVICE_MINUTES", 10)
VARIANT_KEY = "compact"
VARIANT_LABEL = "跨縣市精簡版"
UNASSIGNED_EXPORT_NAME = "Unassigned_Points_compact.xlsx"
UNASSIGNED_DOWNLOAD_KEY = "unassigned_compact"

DEPOTS = {
    "Wugu": {"code": "Wugu", "name": "五股總部", "lat": 25.07154, "lon": 121.44169},
    "Pingzhen": {"code": "Pingzhen", "name": "平鎮總部", "lat": 24.90703, "lon": 121.226872},
}
custom_depot_lat = env_float("DISPATCH_DEPOT_LAT")
custom_depot_lon = env_float("DISPATCH_DEPOT_LON")
if custom_depot_lat is not None and custom_depot_lon is not None:
    DEPOTS["Pingzhen"] = {
        "code": "Pingzhen",
        "name": os.environ.get("DISPATCH_DEPOT_NAME") or "自訂倉庫",
        "lat": custom_depot_lat,
        "lon": custom_depot_lon,
    }

try:
    AVAILABLE_DEPOTS = {item["depot_code"] for item in build_schedule_driver_slots() if item.get("depot_code")}
except Exception:
    AVAILABLE_DEPOTS = {"Pingzhen"} if custom_depot_lat is not None and custom_depot_lon is not None else set(DEPOTS.keys())
DEFAULT_DEPOT_CODE = "Pingzhen" if "Pingzhen" in AVAILABLE_DEPOTS else next(iter(AVAILABLE_DEPOTS), "Pingzhen")

def driver_sort_key(driver_code, schedule_slot):
    """用來排序司機與排程席位的函數"""
    slot_priority = {'P': 1, 'W': 0}  # 優先順序，'W' 優先於 'P'
    return (slot_priority.get(schedule_slot[0], 9), driver_code)

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
    if custom_depot_lat is not None and custom_depot_lon is not None:
        return DEFAULT_DEPOT_CODE
    if county in ["台北市", "新北市", "基隆市"]:
        return "Wugu"
    return "Pingzhen"



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
        if depot not in AVAILABLE_DEPOTS:
            depot = DEFAULT_DEPOT_CODE

        weekly_1 = to_int(row.get("weekly_1"), 1)
        weekly_2 = to_int(row.get("weekly_2"), 0)
        visits = max(1, weekly_1 + weekly_2)
        service_time_total = to_float(row.get("Service_Time"), DEFAULT_SERVICE_MINUTES)
        if service_time_total <= 0:
            service_time_total = DEFAULT_SERVICE_MINUTES
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
    for roster_item in build_schedule_driver_slots():
        for day in range(1, DAY_COUNT + 1):
            slots.append(
                {
                    "driver": roster_item["driver_code"],
                    "driver_label": roster_item["driver_label"],
                    "schedule_slot": roster_item["slot_code"],
                    "day": day,
                    "depot_code": roster_item["depot_code"],
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

    if metrics["total_min"] > MAX_MINUTES:
        return None

    open_new_route_penalty = 220.0 if not route["tasks"] else 0.0
    county_penalty = max(0, len(metrics["counties"]) - 1) * 4.0

    return metrics["total_min"] + open_new_route_penalty + county_penalty



def assign_compact(tasks):
    route_slots = make_route_slots()
    ordered_tasks = sorted(tasks, key=task_order_key)
    unassigned_tasks = []

    for task in ordered_tasks:
        candidates = []
        for route in route_slots:
            score = candidate_score(route, task)
            if score is not None:
                candidates.append((score, route))

        if not candidates:
            unassigned_tasks.append(task)
            continue

        candidates.sort(
            key=lambda x: (
                x[0],
                schedule_sort_key(x[1].get("schedule_slot"), x[1].get("driver")),
                x[1].get("day", 0),
            )
        )
        best_route = candidates[0][1]
        best_route["tasks"].append(task)

    return route_slots, unassigned_tasks



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
                    "driver_label": route["driver_label"],
                    "schedule_slot": route["schedule_slot"],
                    "depot_code": route["depot_code"],
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
                "driver_label": route["driver_label"],
                "schedule_slot": route["schedule_slot"],
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

    routes.sort(key=lambda r: (schedule_sort_key(r.get("schedule_slot"), r.get("driver")), r["day"]))
    flat_rows.sort(key=lambda r: (schedule_sort_key(r.get("schedule_slot"), r.get("driver")), r["day"], r["seq"]))
    return routes, flat_rows



def build_unassigned_point_exports(processed_df, unassigned_tasks):
    node_lookup = {}
    total_db_points = 0

    for _, row in processed_df.iterrows():
        node_id = clean_text(row.get("Node_ID"))
        if not node_id:
            continue
        node_lookup[node_id] = row
        total_db_points += max(1, to_int(row.get("Order_Count"), 1))

    unassigned_node_ids = sorted({clean_text(item.get("node_id")) for item in unassigned_tasks if clean_text(item.get("node_id"))})
    export_rows = []
    unassigned_db_points = 0

    for node_id in unassigned_node_ids:
        row = node_lookup.get(node_id)
        if row is None:
            continue

        order_count = max(1, to_int(row.get("Order_Count"), 1))
        names = [name.strip() for name in str(row.get("Original_ID") or "").split(" | ") if name.strip()]
        if not names:
            names = [node_id]

        missing_visit_count = sum(1 for task in unassigned_tasks if clean_text(task.get("node_id")) == node_id)
        weekly_1 = to_int(row.get("weekly_1"), 1)
        weekly_2 = to_int(row.get("weekly_2"), 0)
        required_visits = max(1, weekly_1 + weekly_2)

        unassigned_db_points += max(order_count, len(names))

        for idx, original_name in enumerate(names, start=1):
            export_rows.append(
                {
                    "node_id": node_id,
                    "original_point_seq": idx,
                    "original_point_name": original_name,
                    "address": clean_text(row.get("Address")),
                    "depot_raw": clean_text(row.get("Depot_Raw")),
                    "freq": clean_text(row.get("Freq")),
                    "required_visits": required_visits,
                    "missing_visit_count": missing_visit_count,
                    "order_count_in_node": order_count,
                    "lat": to_float(row.get("Lat"), None),
                    "lon": to_float(row.get("Lon"), None),
                }
            )

    summary = {
        "variant": VARIANT_KEY,
        "label": VARIANT_LABEL,
        "total_db_points": total_db_points,
        "scheduled_db_points": max(total_db_points - unassigned_db_points, 0),
        "unassigned_db_points": unassigned_db_points,
        "unassigned_node_count": len(unassigned_node_ids),
        "assigned_task_count": 0,
        "unassigned_task_count": len(unassigned_tasks),
        "unassigned_download_key": UNASSIGNED_DOWNLOAD_KEY,
        "unassigned_download_filename": UNASSIGNED_EXPORT_NAME,
        "summary_message": "",
    }
    return export_rows, summary



def save_outputs(routes, flat_rows, processed_df, unassigned_tasks):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    unassigned_rows, summary_meta = build_unassigned_point_exports(processed_df, unassigned_tasks)
    summary_meta["assigned_task_count"] = len(flat_rows)
    summary_meta["summary_message"] = (
        f"重排完成：已排入 {summary_meta['scheduled_db_points']} 個點位，"
        f"未排入 {summary_meta['unassigned_db_points']} 個點位。"
    )

    payload = {
        "meta": {
            "variant": VARIANT_KEY,
            "label": VARIANT_LABEL,
            "note": "跨縣市精簡版，由 phase2_scheduler_cross_county_compact.py 直接輸出。",
            **summary_meta,
        },
        "routes": routes,
    }

    (OUTPUT_DIR / "routes_compact.json").write_text(
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
                "schedule_slot": route.get("schedule_slot"),
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
                    "driver_label": route["driver_label"],
                    "schedule_slot": route.get("schedule_slot"),
                    "day": route["day"],
                    "seq": stop["seq"],
                    "task_id": stop["task_id"],
                    "node_id": stop["node_id"],
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
    unassigned_df = pd.DataFrame(unassigned_rows)

    with pd.ExcelWriter(OUTPUT_DIR / "Weekly_Schedule_Summary_compact.xlsx", engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="route_summary", index=False)
        stops_df.to_excel(writer, sheet_name="route_stops", index=False)
        unassigned_df.to_excel(writer, sheet_name="unassigned_points", index=False)

    daily_summary = summary_df[
        ["driver", "driver_label", "schedule_slot", "day", "stop_count", "service_min", "drive_min", "dist_km", "total_min", "overtime_min"]
    ].copy()
    daily_summary.to_excel(OUTPUT_DIR / "Daily_Route_Summary_compact.xlsx", index=False)
    unassigned_df.to_excel(OUTPUT_DIR / UNASSIGNED_EXPORT_NAME, index=False)



def main():
    if not INPUT_CSV.exists():
        raise FileNotFoundError(f"找不到 {INPUT_CSV}")

    df = pd.read_csv(INPUT_CSV)
    tasks = build_tasks(df)
    print(f"Total tasks generated: {len(tasks)}")

    route_slots, unassigned_tasks = assign_compact(tasks)
    routes, flat_rows = finalize_routes(route_slots)
    save_outputs(routes, flat_rows, df, unassigned_tasks)

    used_routes = len(routes)
    total_stops = sum(r["stop_count"] for r in routes)
    total_service = sum(r["metrics"]["service_min"] for r in routes)
    total_drive = sum(r["metrics"]["drive_min"] for r in routes)
    cross_routes = sum(1 for r in routes if r["cross_county"])

    print("Variant: compact")
    print(f"Routes: {used_routes}")
    print(f"Stops: {total_stops}")
    print(f"Service minutes: {round(total_service, 1)}")
    print(f"Drive minutes: {round(total_drive, 1)}")
    print(f"Cross-county routes: {cross_routes}")
    print(f"Unassigned tasks: {len(unassigned_tasks)}")
    print("phase2_scheduler_cross_county_compact.py completed successfully")


if __name__ == "__main__":
    main()
