import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


BASE = Path("/Users/CherryWu/Documents/11.0/route_system")
OUT = BASE / "output"
REPORT_JSON = OUT / "Driver_Workload_Balance_Report_Data.json"


def status_from_diff(diff, avg):
    if avg <= 0:
        return "正常"
    pct = diff / avg
    if pct >= 0.15:
        return "過高"
    if pct <= -0.15:
        return "過低"
    return "接近平均"


def imbalance_label(gap, avg):
    if avg <= 0:
        return "無資料"
    ratio = gap / avg
    if ratio >= 1.0:
        return "明顯不平均"
    if ratio >= 0.5:
        return "偏不平均"
    return "尚可"


def load_inputs():
    daily = pd.read_excel(OUT / "Daily_Route_Summary.xlsx")
    weekly = pd.read_excel(OUT / "Driver_Weekly_Load_Strict.xlsx")
    stops = pd.read_excel(OUT / "Weekly_Schedule_Summary.xlsx")
    diag = pd.read_csv(OUT / "Route_Cost_Diagnostics.csv")
    routes = json.load(open(OUT / "routes_new.json", encoding="utf-8"))
    return daily, weekly, stops, diag, routes


def build_report_data():
    daily, weekly, stops, diag, routes = load_inputs()

    drivers = weekly[["driver", "driver_label", "depot"]].drop_duplicates().sort_values(["depot", "driver"])
    days = sorted(daily["天數"].dropna().astype(int).unique().tolist())
    full_index = pd.MultiIndex.from_product(
        [drivers["driver"].tolist(), days],
        names=["司機編號", "天數"],
    ).to_frame(index=False)
    full = full_index.merge(
        drivers.rename(columns={"driver": "司機編號", "driver_label": "司機標籤", "depot": "所屬倉庫"}),
        on="司機編號",
        how="left",
    )
    daily_renamed = daily.rename(
        columns={
            "司機": "司機編號",
            "司機標籤": "司機標籤_daily",
            "總站數": "停靠點數",
            "總服務時間_分": "服務時間",
            "總車程_分": "行駛時間",
            "總里程_km": "行駛距離",
            "總工時_分": "總工時",
        }
    )
    full = full.merge(
        daily_renamed[["司機編號", "天數", "停靠點數", "服務時間", "行駛時間", "行駛距離", "總工時"]],
        on=["司機編號", "天數"],
        how="left",
    )
    for col in ["停靠點數", "服務時間", "行駛時間", "行駛距離", "總工時"]:
        full[col] = full[col].fillna(0.0)

    weekly_calc = full.groupby(["司機編號", "司機標籤", "所屬倉庫"], as_index=False).agg(
        一週總工時=("總工時", "sum"),
        一週總服務時間=("服務時間", "sum"),
        一週總行駛時間=("行駛時間", "sum"),
        工作天數=("總工時", lambda s: int((s > 0).sum())),
        一週停靠點數=("停靠點數", "sum"),
    )
    weekly_calc["平均每日工時_以工作天"] = weekly_calc.apply(
        lambda r: r["一週總工時"] / r["工作天數"] if r["工作天數"] else 0.0,
        axis=1,
    )
    weekly_calc["平均每日工時_以6天"] = weekly_calc["一週總工時"] / len(days)
    avg_weekly = float(weekly_calc["一週總工時"].mean())
    weekly_calc["與全體平均差異"] = weekly_calc["一週總工時"] - avg_weekly
    weekly_calc["與全體平均差異%"] = weekly_calc["與全體平均差異"] / avg_weekly if avg_weekly else 0
    weekly_calc["狀態"] = weekly_calc["與全體平均差異"].apply(lambda x: status_from_diff(x, avg_weekly))
    weekly_calc = weekly_calc.sort_values("一週總工時", ascending=False)

    daily_summary = full.groupby("天數", as_index=False).agg(
        當天總工時=("總工時", "sum"),
        當天最高工時=("總工時", "max"),
        當天最低工時=("總工時", "min"),
        當天平均工時=("總工時", "mean"),
        當天工作司機數=("總工時", lambda s: int((s > 0).sum())),
        當天空班司機數=("總工時", lambda s: int((s == 0).sum())),
    )
    daily_summary["工時差距"] = daily_summary["當天最高工時"] - daily_summary["當天最低工時"]
    daily_summary["不平均程度"] = daily_summary.apply(lambda r: imbalance_label(r["工時差距"], r["當天平均工時"]), axis=1)

    weekly_stats = {
        "一週總工時平均值": avg_weekly,
        "最大值": float(weekly_calc["一週總工時"].max()),
        "最小值": float(weekly_calc["一週總工時"].min()),
        "標準差": float(weekly_calc["一週總工時"].std(ddof=0)),
        "最大差距": float(weekly_calc["一週總工時"].max() - weekly_calc["一週總工時"].min()),
        "司機數": int(len(weekly_calc)),
        "週工時變異係數": float(weekly_calc["一週總工時"].std(ddof=0) / avg_weekly) if avg_weekly else 0,
    }

    full["剩餘可用工時"] = 540 - full["總工時"]
    receivers = full[full["剩餘可用工時"] >= 90].sort_values(["天數", "所屬倉庫", "剩餘可用工時"], ascending=[True, True, False])
    donors = full[full["總工時"] >= 500].sort_values(["天數", "所屬倉庫", "總工時"], ascending=[True, True, False])

    stops_renamed = stops.rename(
        columns={
            "driver": "司機編號",
            "depot_code": "所屬倉庫",
            "day": "天數",
            "county": "縣市",
            "service_time_min": "服務時間",
            "travel_time_min": "到站行駛時間",
            "task_id": "任務ID",
            "node_id": "節點ID",
            "address": "地址",
        }
    )
    route_counties = (
        stops_renamed.groupby(["司機編號", "天數", "所屬倉庫", "縣市"], as_index=False)
        .agg(站數=("任務ID", "count"), 服務時間=("服務時間", "sum"))
    )

    opportunities = []
    for _, donor in donors.iterrows():
        d_counties = route_counties[
            (route_counties["司機編號"] == donor["司機編號"])
            & (route_counties["天數"] == donor["天數"])
            & (route_counties["所屬倉庫"] == donor["所屬倉庫"])
        ]
        for _, county_row in d_counties.iterrows():
            same_day_depot = full[
                (full["天數"] == donor["天數"])
                & (full["所屬倉庫"] == donor["所屬倉庫"])
                & (full["司機編號"] != donor["司機編號"])
                & (full["剩餘可用工時"] >= 90)
            ].copy()
            if same_day_depot.empty:
                continue

            receiver_counties = route_counties[
                (route_counties["天數"] == donor["天數"])
                & (route_counties["所屬倉庫"] == donor["所屬倉庫"])
            ][["司機編號", "縣市"]].drop_duplicates()
            same_day_depot = same_day_depot.merge(receiver_counties, on="司機編號", how="left", suffixes=("", "_既有"))
            same_day_depot["縣市_既有"] = same_day_depot["縣市"].fillna("")
            possible = same_day_depot[
                (same_day_depot["總工時"] == 0)
                | (same_day_depot["縣市_既有"] == county_row["縣市"])
            ]
            if possible.empty:
                continue
            best = possible.sort_values("剩餘可用工時", ascending=False).iloc[0]
            opportunities.append(
                {
                    "日期": int(donor["天數"]),
                    "倉庫": donor["所屬倉庫"],
                    "可減少司機": donor["司機編號"],
                    "可減少司機當日工時": round(float(donor["總工時"]), 2),
                    "可接更多司機": best["司機編號"],
                    "可接更多司機當日工時": round(float(best["總工時"]), 2),
                    "接收司機剩餘工時": round(float(best["剩餘可用工時"]), 2),
                    "縣市": county_row["縣市"],
                    "可考慮轉移站數來源": int(county_row["站數"]),
                    "可考慮轉移服務時間來源": round(float(county_row["服務時間"]), 2),
                    "注意": "僅代表同日同倉且不跨縣市規則下有空間；實際轉移需重新插入排序並用 OSRM Route 驗證。",
                }
            )
    opportunities_df = pd.DataFrame(opportunities).drop_duplicates().head(30)

    high = weekly_calc[weekly_calc["狀態"] == "過高"]
    low = weekly_calc[weekly_calc["狀態"] == "過低"]
    notes = [
        {
            "項目": "整體判斷",
            "內容": "週總工時分配不平均；五股 W01/W02 與部分平鎮司機差距明顯，且每日有空班或低工時司機。",
        },
        {
            "項目": "高負載司機",
            "內容": "、".join(high["司機編號"].tolist()) if not high.empty else "無明顯高於平均者",
        },
        {
            "項目": "低負載司機",
            "內容": "、".join(low["司機編號"].tolist()) if not low.empty else "無明顯低於平均者",
        },
        {
            "項目": "是否建議修改演算法",
            "內容": "建議先加一個後處理的工時均衡 pass，不要重寫 NN/Greedy/2-Opt。每次只嘗試少量同日、同倉、同縣市的點位轉移，成功後必須重新跑 OSRM Route 驗證。",
        },
        {
            "項目": "修改風險",
            "內容": "中等。若只用估算轉移，可能破壞 540 分鐘限制或讓路線繞路；必須用 OSRM legs/Route 重新驗證，且保留 rollback。",
        },
    ]

    output = {
        "weekly_driver": weekly_calc.round(4).to_dict("records"),
        "daily_driver": full.sort_values(["天數", "所屬倉庫", "司機編號"]).round(4).to_dict("records"),
        "daily_summary": daily_summary.round(4).to_dict("records"),
        "weekly_stats": weekly_stats,
        "receivers": receivers.head(30).round(4).to_dict("records"),
        "donors": donors.head(30).round(4).to_dict("records"),
        "opportunities": opportunities_df.to_dict("records") if not opportunities_df.empty else [],
        "notes": notes,
        "diagnostic_summary": {
            "routes_count": int(len(diag)),
            "fallback_routes": int((diag["是否使用備援"].astype(str) == "是").sum()),
            "over_540_osrm": int((diag["OSRM是否超過540分鐘"].astype(str) == "是").sum()),
            "avg_osrm_total_min": float(diag["OSRM 總工時（分鐘）"].mean()),
            "max_osrm_total_min": float(diag["OSRM 總工時（分鐘）"].max()),
        },
    }
    REPORT_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(REPORT_JSON)
    print(json.dumps(weekly_stats, ensure_ascii=False, indent=2))
    print("high", high["司機編號"].tolist())
    print("low", low["司機編號"].tolist())


if __name__ == "__main__":
    build_report_data()
