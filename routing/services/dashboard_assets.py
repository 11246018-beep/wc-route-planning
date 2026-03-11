from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import re

import pandas as pd


def load_grouped_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        return raw.get("routes", []), raw.get("meta", {})
    return [], {}


def driver_label(code):
    s = str(code or "").upper()
    if s.startswith("P") and s[1:].isdigit():
        return f"{s}｜平鎮{s[1:].lstrip('0') or '0'}"
    if s.startswith("W") and s[1:].isdigit():
        return f"{s}｜五股{s[1:].lstrip('0') or '0'}"
    return s


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

    normal_routes, _ = load_grouped_json(output_dir / "routes_normal.json")
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

    summary_rows.extend(flatten_summary_rows(normal_routes, "normal", "routes_normal.json"))
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
    }