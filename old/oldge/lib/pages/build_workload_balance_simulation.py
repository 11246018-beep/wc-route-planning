from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
SERVICES_DIR = BASE_DIR / "routing" / "services"
if str(SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICES_DIR))

from phase2_scheduler import OSRMClient, RoutingCostProvider, make_local_cost_getter, run_two_opt  # noqa: E402


DEPOTS = {
    "Wugu": {"lat": 25.07154, "lon": 121.44169},
    "Pingzhen": {"lat": 24.90703, "lon": 121.226872},
}
MAX_DAILY_MINUTES = 540.0
MAX_EVALUATIONS = 60


def json_default(value):
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def fnum(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def route_key(row):
    return (
        int(row["day"]),
        str(row["depot_code"]),
        str(row["county"]),
        str(row["driver"]),
    )


def depot_point(depot_code):
    depot = DEPOTS[depot_code]
    return {
        "task_id": "__DEPOT__",
        "lat": depot["lat"],
        "lon": depot["lon"],
        "service_time_min": 0.0,
    }


def task_from_row(row):
    return {
        "task_id": str(row["task_id"]),
        "node_id": str(row["node_id"]),
        "address": str(row["address"]),
        "county": str(row["county"]),
        "lat": fnum(row["lat"]),
        "lon": fnum(row["lon"]),
        "service_time_min": fnum(row["service_time_min"]),
        "travel_time_min": fnum(row["travel_time_min"]),
        "travel_dist_km": fnum(row["travel_dist_km"]),
        "seq": int(row["seq"]),
    }


def summarize_tasks(tasks):
    return {
        "stops": len(tasks),
        "service": sum(fnum(t["service_time_min"]) for t in tasks),
    }


def evaluate_route(tasks, depot_code, osrm, cost_provider, label):
    if not tasks:
        return {
            "tasks": [],
            "stops": 0,
            "service": 0.0,
            "drive": 0.0,
            "distance": 0.0,
            "total": 0.0,
            "source": "empty",
        }

    route_pts = [depot_point(depot_code)] + [dict(t) for t in tasks]
    get_cost, stats, _local_cache = make_local_cost_getter(cost_provider, persist_fallback=False)
    optimized = run_two_opt(route_pts, get_cost, context_label=label, cost_stats=stats)
    ordered = optimized[1:]
    coords = [(DEPOTS[depot_code]["lat"], DEPOTS[depot_code]["lon"])] + [
        (fnum(t["lat"]), fnum(t["lon"])) for t in ordered
    ]
    route_data = osrm.get_route_batch(coords)
    service = sum(fnum(t["service_time_min"]) for t in ordered)
    drive = fnum(route_data.get("duration"))
    distance = fnum(route_data.get("distance"))
    return {
        "tasks": ordered,
        "stops": len(ordered),
        "service": service,
        "drive": drive,
        "distance": distance,
        "total": service + drive,
        "source": route_data.get("source", "unknown"),
    }


def route_std(route_totals):
    values = list(route_totals.values())
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def main():
    schedule_path = OUTPUT_DIR / "Weekly_Schedule_Summary.xlsx"
    if not schedule_path.exists():
        raise FileNotFoundError(schedule_path)

    rows = pd.read_excel(schedule_path)
    routes = {}
    route_meta = {}
    for key, group in rows.sort_values(["day", "driver", "seq"]).groupby(
        ["day", "depot_code", "county", "driver"], sort=False
    ):
        tasks = [task_from_row(row) for _, row in group.sort_values("seq").iterrows()]
        summary = summarize_tasks(tasks)
        summary["drive"] = sum(fnum(t["travel_time_min"]) for t in tasks)
        summary["distance"] = sum(fnum(t["travel_dist_km"]) for t in tasks)
        summary["total"] = summary["service"] + summary["drive"]
        routes[key] = tasks
        route_meta[key] = summary

    all_totals = {key: meta["total"] for key, meta in route_meta.items()}
    before_std = route_std(all_totals)
    before_avg = statistics.mean(all_totals.values()) if all_totals else 0.0

    by_bucket = {}
    for key in routes:
        day, depot, county, _driver = key
        by_bucket.setdefault((day, depot, county), []).append(key)

    osrm = OSRMClient()
    cost_provider = RoutingCostProvider()
    candidates = []
    evaluated = 0

    for bucket, keys in by_bucket.items():
        if len(keys) < 2:
            continue
        keys = sorted(keys, key=lambda k: route_meta[k]["total"], reverse=True)
        for source_key in keys:
            source_total = route_meta[source_key]["total"]
            if source_total < before_avg or route_meta[source_key]["stops"] < 4:
                continue
            source_tasks = routes[source_key]
            edge_tasks = source_tasks[:2] + source_tasks[-2:] + sorted(
                source_tasks, key=lambda t: fnum(t["travel_time_min"]), reverse=True
            )[:3]
            seen_task_ids = set()
            move_tasks = []
            for task in edge_tasks:
                if task["task_id"] not in seen_task_ids:
                    move_tasks.append(task)
                    seen_task_ids.add(task["task_id"])
            for target_key in reversed(keys):
                if target_key == source_key:
                    continue
                target_total = route_meta[target_key]["total"]
                if source_total - target_total < 45:
                    continue
                if MAX_DAILY_MINUTES - target_total < 20:
                    continue

                for move_task in move_tasks:
                    if evaluated >= MAX_EVALUATIONS:
                        break
                    if target_total + fnum(move_task["service_time_min"]) > MAX_DAILY_MINUTES:
                        continue
                    evaluated += 1
                    source_after_tasks = [
                        t for t in source_tasks if t["task_id"] != move_task["task_id"]
                    ]
                    target_after_tasks = routes[target_key] + [move_task]
                    source_after = evaluate_route(
                        source_after_tasks,
                        source_key[1],
                        osrm,
                        cost_provider,
                        f"sim source {source_key[3]} D{source_key[0]} {move_task['task_id']}",
                    )
                    target_after = evaluate_route(
                        target_after_tasks,
                        target_key[1],
                        osrm,
                        cost_provider,
                        f"sim target {target_key[3]} D{target_key[0]} {move_task['task_id']}",
                    )
                    before_pair_total = source_total + target_total
                    after_pair_total = source_after["total"] + target_after["total"]
                    before_gap = abs(source_total - target_total)
                    after_gap = abs(source_after["total"] - target_after["total"])
                    after_totals = dict(all_totals)
                    after_totals[source_key] = source_after["total"]
                    after_totals[target_key] = target_after["total"]
                    after_std = route_std(after_totals)
                    feasible = (
                        source_after["total"] <= MAX_DAILY_MINUTES
                        and target_after["total"] <= MAX_DAILY_MINUTES
                    )
                    total_increase = after_pair_total - before_pair_total
                    improves = after_gap < before_gap and after_std < before_std
                    recommend = feasible and improves and total_increase <= 15
                    day, depot, county = bucket
                    candidates.append(
                        {
                            "日期": int(day),
                            "倉庫": depot,
                            "縣市": county,
                            "來源司機": source_key[3],
                            "目標司機": target_key[3],
                            "轉移點位": move_task["task_id"],
                            "轉移地址": move_task["address"],
                            "轉移點服務時間": round(fnum(move_task["service_time_min"]), 2),
                            "轉移前來源司機工時": round(source_total, 2),
                            "轉移前目標司機工時": round(target_total, 2),
                            "轉移後來源司機工時": round(source_after["total"], 2),
                            "轉移後目標司機工時": round(target_after["total"], 2),
                            "是否仍符合540分鐘": "是" if feasible else "否",
                            "總里程是否增加": "是" if (
                                source_after["distance"] + target_after["distance"]
                                > route_meta[source_key]["distance"] + route_meta[target_key]["distance"]
                            ) else "否",
                            "總工時是否增加": "是" if total_increase > 0 else "否",
                            "總工時增加分鐘": round(total_increase, 2),
                            "工時差距改善分鐘": round(before_gap - after_gap, 2),
                            "標準差改善分鐘": round(before_std - after_std, 2),
                            "是否建議接受": "是" if recommend else "否",
                            "備註": (
                                "同日同倉同縣市，通過 OSRM 驗證"
                                if recommend
                                else "可行但效益不足或總工時增加偏高"
                                if feasible
                                else "轉移後超過 540 分鐘，不建議"
                            ),
                        }
                    )
                if evaluated >= MAX_EVALUATIONS:
                    break
            if evaluated >= MAX_EVALUATIONS:
                break
        if evaluated >= MAX_EVALUATIONS:
            break

    recommended = [row for row in candidates if row["是否建議接受"] == "是"]
    best_improvement = max([row["標準差改善分鐘"] for row in recommended], default=0.0)
    best_days = sorted({row["日期"] for row in recommended})
    summary = {
        "目前路線數": len(routes),
        "評估候選數": len(candidates),
        "建議接受數": len(recommended),
        "目前日路線工時標準差": round(before_std, 2),
        "最多可改善標準差分鐘": round(best_improvement, 2),
        "最適合調整日期": ", ".join(f"Day {day}" for day in best_days) if best_days else "目前沒有明顯低風險候選",
        "是否值得做正式後處理": "值得先做小型後處理 pass" if recommended else "暫不建議，需先改善候選搜尋或放寬限制",
        "白話結論": (
            "有少量同日、同倉庫、同縣市的單點轉移空間，但每次轉移都必須重新 2-Opt 並用 OSRM Route 驗證。"
            if recommended
            else "在目前嚴格限制下，低風險且能明顯降低不平均的單點轉移不多。"
        ),
    }

    output = {
        "summary": summary,
        "candidates": candidates,
        "recommended": recommended,
        "source_files": [
            str(schedule_path),
            str(OUTPUT_DIR / "Daily_Route_Summary.xlsx"),
            str(OUTPUT_DIR / "Driver_Workload_Cause_Report.xlsx"),
        ],
    }
    out_path = OUTPUT_DIR / "workload_balance_simulation_data.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default))
    print(f"saved: {out_path}")


if __name__ == "__main__":
    main()
