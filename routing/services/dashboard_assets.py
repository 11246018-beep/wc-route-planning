from __future__ import annotations

from datetime import datetime
from pathlib import Path
from collections import defaultdict
import json
import re

import pandas as pd


MAX_MINUTES = 540

DEPOT_INFO = {
    "Wugu": {
        "code": "Wugu",
        "name": "五股總部",
        "lat": 25.07154,
        "lon": 121.44169,
    },
    "Pingzhen": {
        "code": "Pingzhen",
        "name": "平鎮總部",
        "lat": 24.90703,
        "lon": 121.226872,
    },
    "Unknown": {
        "code": "Unknown",
        "name": "總部未設定",
        "lat": None,
        "lon": None,
    },
}


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_grouped_json(path: Path):
    if not path.exists():
        return [], {}
    raw = load_json(path)
    if isinstance(raw, dict):
        return raw.get("routes", []), raw.get("meta", {})
    return [], {}


def to_float(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        text = str(value).strip()
        if text == "" or text.lower() == "nan":
            return default
        return float(text)
    except Exception:
        return default


def to_int(value, default=0):
    try:
        if pd.isna(value):
            return default
        text = str(value).strip()
        if text == "" or text.lower() == "nan":
            return default
        return int(float(text))
    except Exception:
        return default


def clean_text(value, default=""):
    try:
        if pd.isna(value):
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def driver_label(code):
    s = str(code or "").upper()
    if s.startswith("P") and s[1:].isdigit():
        return f"{s}｜平鎮{s[1:].lstrip('0') or '0'}"
    if s.startswith("W") and s[1:].isdigit():
        return f"{s}｜五股{s[1:].lstrip('0') or '0'}"
    return s


def infer_depot_from_driver(driver_code):
    s = str(driver_code or "").upper()
    if s.startswith("W"):
        return DEPOT_INFO["Wugu"].copy()
    if s.startswith("P"):
        return DEPOT_INFO["Pingzhen"].copy()
    return DEPOT_INFO["Unknown"].copy()


def build_normal_routes_from_routes_new(schedule_rows):
    grouped = defaultdict(list)
    for row in schedule_rows:
        driver = clean_text(row.get("driver")).upper()
        day = to_int(row.get("day"))
        if not driver or day <= 0:
            continue
        grouped[(driver, day)].append(row)

    routes = []

    for (driver, day), items in sorted(grouped.items(), key=lambda x: (x[0][0], x[0][1])):
        items = sorted(
            items,
            key=lambda r: (to_int(r.get("seq")), clean_text(r.get("task_id")))
        )

        depot = infer_depot_from_driver(driver)

        stops = []
        service_total = 0.0
        drive_total = 0.0
        dist_total = 0.0
        county_set = set()

        for item in items:
            seq = to_int(item.get("seq"))
            service_min = round(to_float(item.get("service_time_min")), 2)
            travel_time_min = round(to_float(item.get("travel_time_min")), 2)
            travel_dist_km = round(to_float(item.get("travel_dist_km")), 2)
            county = clean_text(item.get("county")) or None

            if county:
                county_set.add(county)

            service_total += service_min
            drive_total += travel_time_min
            dist_total += travel_dist_km

            stops.append(
                {
                    "seq": seq,
                    "task_id": clean_text(item.get("task_id")) or None,
                    "node_id": clean_text(item.get("node_id")) or None,
                    "county": county,
                    "address": clean_text(item.get("address")) or None,
                    "lat": to_float(item.get("lat"), None),
                    "lon": to_float(item.get("lon"), None),
                    "service_min": service_min,
                    "travel_time_min": travel_time_min,
                    "travel_dist_km": travel_dist_km,
                }
            )

        total_min = round(service_total + drive_total, 2)

        routes.append(
            {
                "route_id": f"NORMAL-{driver}-D{day:02d}",
                "driver": driver,
                "driver_label": clean_text(items[0].get("driver_label")) or driver_label(driver),
                "day": day,
                "depot": depot,
                "stop_count": len(stops),
                "counties": sorted(county_set),
                "cross_county": len(county_set) > 1,
                "metrics": {
                    "service_min": round(service_total, 2),
                    "drive_min": round(drive_total, 2),
                    "dist_km": round(dist_total, 2),
                    "total_min": total_min,
                    "overtime_min": round(max(0, total_min - MAX_MINUTES), 2),
                },
                "stops": stops,
            }
        )

    payload = {
        "meta": {
            "variant": "normal",
            "label": "不跨縣市",
            "note": "由 routes_new.json 轉成 Dashboard grouped 格式，KPI 以正式排程結果為準。",
        },
        "routes": routes,
    }
    return payload


def load_normal_routes_and_refresh(output_dir: Path):
    routes_new_path = output_dir / "routes_new.json"
    routes_normal_path = output_dir / "routes_normal.json"

    if routes_new_path.exists():
        raw = load_json(routes_new_path)
        if isinstance(raw, list) and raw:
            payload = build_normal_routes_from_routes_new(raw)
            routes_normal_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return payload["routes"], payload["meta"], "routes_new.json"

    routes, meta = load_grouped_json(routes_normal_path)
    return routes, meta, "routes_normal.json"


def parse_old_routes_from_map_html(project_root: Path):
    html_path = project_root / "map.html"
    if not html_path.exists():
        return []

    text = html_path.read_text(encoding="utf-8", errors="ignore")
    match = re.search(
        r'<script id="old-data" type="application/json">\s*(\{.*?\})\s*</script>',
        text,
        re.S,
    )
    if not match:
        return []

    raw = json.loads(match.group(1))
    raw_routes = raw.get("routes", [])

    depot_counters = {
        "Wugu": 0,
        "Pingzhen": 0,
    }

    routes = []
    for idx, route in enumerate(raw_routes, start=1):
        depot_code = route.get("depot") or "Pingzhen"
        if depot_code not in {"Wugu", "Pingzhen"}:
            depot_code = "Pingzhen"

        if depot_code == "Wugu":
            depot_counters["Wugu"] += 1
            driver_code = f"W{depot_counters['Wugu']:02d}"
            depot_name = "五股總部"
            depot_lat = 25.07154
            depot_lon = 121.44169
        else:
            depot_counters["Pingzhen"] += 1
            driver_code = f"P{depot_counters['Pingzhen']:02d}"
            depot_name = "平鎮總部"
            depot_lat = 24.90703
            depot_lon = 121.226872

        stops = []
        service_total = 0.0
        for stop in route.get("stops", []):
            service_min = float(stop.get("service_min") or 0)
            service_total += service_min
            stops.append(
                {
                    "seq": int(stop.get("seq") or 0),
                    "task_id": stop.get("customer") or stop.get("task_id"),
                    "county": stop.get("county"),
                    "address": stop.get("address"),
                    "lat": float(stop.get("lat") or 0),
                    "lon": float(stop.get("lon") or 0),
                    "service_min": service_min,
                    "travel_time_min": 0,
                    "travel_dist_km": 0,
                }
            )

        routes.append(
            {
                "route_id": f"OLD-{idx:03d}",
                "driver": driver_code,
                "driver_label": driver_label(driver_code),
                "original_driver_name": route.get("driver"),
                "day": int(route.get("day") or 0),
                "depot": {
                    "code": depot_code,
                    "name": depot_name,
                    "lat": depot_lat,
                    "lon": depot_lon,
                },
                "stop_count": len(stops),
                "counties": sorted({s["county"] for s in stops if s["county"]}),
                "cross_county": len({s["county"] for s in stops if s["county"]}) > 1,
                "metrics": {
                    "service_min": round(service_total, 2),
                    "drive_min": 0,
                    "dist_km": 0,
                    "total_min": round(service_total, 2),
                    "overtime_min": 0,
                },
                "stops": stops,
            }
        )

    return routes


def flatten_summary_rows(routes, variant, source_file):
    rows = []
    for route in routes:
        rows.append(
            {
                "variant": variant,
                "source_file": source_file,
                "route_id": route.get("route_id"),
                "driver": route.get("driver"),
                "driver_label": route.get("driver_label"),
                "day": route.get("day"),
                "depot_name": route.get("depot", {}).get("name"),
                "stop_count": route.get("stop_count"),
                "counties": " / ".join(route.get("counties") or []),
                "cross_county": route.get("cross_county"),
                "service_min": route.get("metrics", {}).get("service_min"),
                "drive_min": route.get("metrics", {}).get("drive_min"),
                "dist_km": route.get("metrics", {}).get("dist_km"),
                "total_min": route.get("metrics", {}).get("total_min"),
                "overtime_min": route.get("metrics", {}).get("overtime_min"),
            }
        )
    return rows


def flatten_stop_rows(routes, variant):
    rows = []
    for route in routes:
        for stop in route.get("stops", []):
            rows.append(
                {
                    "variant": variant,
                    "route_id": route.get("route_id"),
                    "driver": route.get("driver"),
                    "driver_label": route.get("driver_label"),
                    "day": route.get("day"),
                    "seq": stop.get("seq"),
                    "task_id": stop.get("task_id"),
                    "county": stop.get("county"),
                    "address": stop.get("address"),
                    "service_min": stop.get("service_min"),
                    "travel_time_min": stop.get("travel_time_min"),
                    "travel_dist_km": stop.get("travel_dist_km"),
                    "lat": stop.get("lat"),
                    "lon": stop.get("lon"),
                }
            )
    return rows


def export_dashboard_assets(project_root: Path):
    output_dir = project_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    normal_routes, normal_meta, normal_source = load_normal_routes_and_refresh(output_dir)
    cross_routes, _ = load_grouped_json(output_dir / "routes_cross.json")
    compact_routes, _ = load_grouped_json(output_dir / "routes_compact.json")

    old_routes = parse_old_routes_from_map_html(project_root)
    if old_routes:
        old_payload = {
            "meta": {
                "variant": "old",
                "label": "舊路線",
                "generated_from": "map.html",
                "note": "從舊 map.html 的 old-data 區塊解析，並將司機名稱轉成與新路線一致的代碼格式。",
            },
            "routes": old_routes,
        }
        (output_dir / "old_routes.json").write_text(
            json.dumps(old_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    summary_rows = []
    stop_rows = []

    summary_rows.extend(flatten_summary_rows(normal_routes, "normal", normal_source))
    summary_rows.extend(flatten_summary_rows(cross_routes, "cross", "routes_cross.json"))
    summary_rows.extend(flatten_summary_rows(compact_routes, "compact", "routes_compact.json"))

    stop_rows.extend(flatten_stop_rows(normal_routes, "normal"))
    stop_rows.extend(flatten_stop_rows(cross_routes, "cross"))
    stop_rows.extend(flatten_stop_rows(compact_routes, "compact"))

    if old_routes:
        summary_rows.extend(flatten_summary_rows(old_routes, "old", "old_routes.json"))
        stop_rows.extend(flatten_stop_rows(old_routes, "old"))

    summary_df = pd.DataFrame(summary_rows)
    stops_df = pd.DataFrame(stop_rows)

    processed_nodes_csv = output_dir / "processed_nodes_phase1.csv"
    all_points_df = pd.read_csv(processed_nodes_csv) if processed_nodes_csv.exists() else pd.DataFrame()

    latest_report = output_dir / "Dispatch_Report_Latest.xlsx"
    stamped_report = output_dir / f"Dispatch_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    for report_path in [latest_report, stamped_report]:
        with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
            summary_df.to_excel(writer, sheet_name="route_summary", index=False)
            stops_df.to_excel(writer, sheet_name="route_stops", index=False)
            if not all_points_df.empty:
                all_points_df.to_excel(writer, sheet_name="all_points", index=False)

    return {
        "latest_report": str(latest_report),
        "stamped_report": str(stamped_report),
        "old_routes_count": len(old_routes),
        "normal_routes_count": len(normal_routes),
        "cross_routes_count": len(cross_routes),
        "compact_routes_count": len(compact_routes),
        "normal_source": normal_source,
        "normal_note": normal_meta.get("note") if isinstance(normal_meta, dict) else "",
    }