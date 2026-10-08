import json
from pathlib import Path

import pandas as pd


BASE = Path("/Users/CherryWu/Documents/11.0/route_system")
OUT = BASE / "output"
REPORT_JSON = OUT / "Driver_Workload_Cause_Report_Data.json"


def classify_reason(row, avg_service, avg_drive, avg_stops, avg_drive_per_stop, avg_workdays):
    reasons = []
    if row["一週總服務時間"] >= avg_service * 1.12:
        reasons.append("服務時間高")
    if row["一週總行駛時間"] >= avg_drive * 1.12:
        reasons.append("行駛時間高")
    if row["一週停靠點數"] >= avg_stops * 1.12:
        reasons.append("點數多")
    if row["平均每站行駛時間"] >= avg_drive_per_stop * 1.20:
        reasons.append("點位分散")
    if row["工作天數"] < avg_workdays and row["平均每日工時_以工作天"] >= 480:
        reasons.append("工作天數少但工時集中")
    if row["所屬倉庫"] == "Wugu":
        reasons.append("倉庫/縣市限制")
    return " / ".join(reasons) if reasons else "接近平均或混合因素"


def main():
    daily = pd.read_excel(OUT / "Daily_Route_Summary.xlsx")
    weekly = pd.read_excel(OUT / "Driver_Weekly_Load_Strict.xlsx")
    stops = pd.read_excel(OUT / "Weekly_Schedule_Summary.xlsx")
    diag = pd.read_csv(OUT / "Route_Cost_Diagnostics.csv")

    daily = daily.rename(
        columns={
            "司機": "司機編號",
            "司機標籤": "司機標籤",
            "天數": "天數",
            "總站數": "停靠點數",
            "總服務時間_分": "服務時間",
            "總車程_分": "行駛時間",
            "總里程_km": "行駛距離",
            "總工時_分": "總工時",
        }
    )
    weekly = weekly.rename(columns={"driver": "司機編號", "driver_label": "司機標籤", "depot": "所屬倉庫"})

    drivers = weekly[["司機編號", "司機標籤", "所屬倉庫"]].drop_duplicates()
    driver_cause = daily.groupby("司機編號", as_index=False).agg(
        一週總工時=("總工時", "sum"),
        一週總服務時間=("服務時間", "sum"),
        一週總行駛時間=("行駛時間", "sum"),
        一週停靠點數=("停靠點數", "sum"),
        工作天數=("總工時", lambda s: int((s > 0).sum())),
    )
    driver_cause = drivers.merge(driver_cause, on="司機編號", how="left").fillna(0)
    driver_cause["行駛時間占總工時比例"] = driver_cause.apply(
        lambda r: r["一週總行駛時間"] / r["一週總工時"] if r["一週總工時"] else 0,
        axis=1,
    )
    driver_cause["服務時間占總工時比例"] = driver_cause.apply(
        lambda r: r["一週總服務時間"] / r["一週總工時"] if r["一週總工時"] else 0,
        axis=1,
    )
    driver_cause["平均每站行駛時間"] = driver_cause.apply(
        lambda r: r["一週總行駛時間"] / r["一週停靠點數"] if r["一週停靠點數"] else 0,
        axis=1,
    )
    driver_cause["平均每站服務時間"] = driver_cause.apply(
        lambda r: r["一週總服務時間"] / r["一週停靠點數"] if r["一週停靠點數"] else 0,
        axis=1,
    )
    driver_cause["平均每日工時_以工作天"] = driver_cause.apply(
        lambda r: r["一週總工時"] / r["工作天數"] if r["工作天數"] else 0,
        axis=1,
    )

    avg_service = float(driver_cause["一週總服務時間"].mean())
    avg_drive = float(driver_cause["一週總行駛時間"].mean())
    avg_stops = float(driver_cause["一週停靠點數"].mean())
    avg_drive_per_stop = float(driver_cause["平均每站行駛時間"].mean())
    avg_workdays = float(driver_cause["工作天數"].mean())
    avg_total = float(driver_cause["一週總工時"].mean())

    driver_cause["高工時原因判斷"] = driver_cause.apply(
        lambda r: classify_reason(r, avg_service, avg_drive, avg_stops, avg_drive_per_stop, avg_workdays),
        axis=1,
    )
    driver_cause["週工時與平均差異"] = driver_cause["一週總工時"] - avg_total
    driver_cause["週工時狀態"] = driver_cause["週工時與平均差異"].apply(
        lambda x: "偏高" if x >= avg_total * 0.15 else ("偏低" if x <= -avg_total * 0.15 else "接近平均")
    )

    stops_renamed = stops.rename(
        columns={
            "driver": "司機編號",
            "depot_code": "所屬倉庫",
            "day": "天數",
            "county": "縣市",
            "task_id": "任務ID",
            "service_time_min": "服務時間",
            "travel_time_min": "行駛時間",
            "travel_dist_km": "行駛距離",
        }
    )
    county_mix = stops_renamed.groupby(["司機編號", "所屬倉庫", "縣市"], as_index=False).agg(
        停靠點數=("任務ID", "count"),
        服務時間=("服務時間", "sum"),
        行駛時間=("行駛時間", "sum"),
        行駛距離=("行駛距離", "sum"),
    )
    county_mix["平均每站行駛時間"] = county_mix["行駛時間"] / county_mix["停靠點數"]
    county_mix = county_mix.sort_values(["司機編號", "行駛時間"], ascending=[True, False])

    daily_cause = daily.copy()
    daily_cause["行駛占比"] = daily_cause.apply(lambda r: r["行駛時間"] / r["總工時"] if r["總工時"] else 0, axis=1)
    daily_cause["服務占比"] = daily_cause.apply(lambda r: r["服務時間"] / r["總工時"] if r["總工時"] else 0, axis=1)
    daily_cause["平均每站行駛時間"] = daily_cause.apply(lambda r: r["行駛時間"] / r["停靠點數"] if r["停靠點數"] else 0, axis=1)

    top_tables = {
        "行駛時間最高前5": driver_cause.sort_values("一週總行駛時間", ascending=False).head(5),
        "行駛占比最高前5": driver_cause.sort_values("行駛時間占總工時比例", ascending=False).head(5),
        "平均每站行駛最高前5": driver_cause.sort_values("平均每站行駛時間", ascending=False).head(5),
        "服務時間最高前5": driver_cause.sort_values("一週總服務時間", ascending=False).head(5),
        "停靠點數最多前5": driver_cause.sort_values("一週停靠點數", ascending=False).head(5),
    }
    top_records = []
    for category, df in top_tables.items():
        for rank, (_, row) in enumerate(df.iterrows(), start=1):
            top_records.append(
                {
                    "類別": category,
                    "排名": rank,
                    "司機編號": row["司機編號"],
                    "所屬倉庫": row["所屬倉庫"],
                    "一週總工時": row["一週總工時"],
                    "一週總服務時間": row["一週總服務時間"],
                    "一週總行駛時間": row["一週總行駛時間"],
                    "行駛時間占比": row["行駛時間占總工時比例"],
                    "停靠點數": row["一週停靠點數"],
                    "平均每站行駛時間": row["平均每站行駛時間"],
                    "高工時原因判斷": row["高工時原因判斷"],
                }
            )

    drive_share_mean = float(driver_cause["行駛時間占總工時比例"].mean())
    service_share_mean = float(driver_cause["服務時間占總工時比例"].mean())
    service_std = float(driver_cause["一週總服務時間"].std(ddof=0))
    drive_std = float(driver_cause["一週總行駛時間"].std(ddof=0))
    summary = [
        {
            "項目": "主要不平均來源",
            "內容": "服務時間仍是總工時的大宗，但司機之間的總工時落差，主要由行駛時間、點位分散、工作天數集中與倉庫/縣市限制共同造成。",
        },
        {
            "項目": "全體平均行駛占比",
            "內容": f"{drive_share_mean:.1%}",
        },
        {
            "項目": "全體平均服務占比",
            "內容": f"{service_share_mean:.1%}",
        },
        {
            "項目": "服務時間標準差",
            "內容": f"{service_std:.1f} 分鐘",
        },
        {
            "項目": "行駛時間標準差",
            "內容": f"{drive_std:.1f} 分鐘",
        },
        {
            "項目": "優先改善方向",
            "內容": "先處理行駛時間高且平均每站行駛時間高的路線，再看服務時間極高的司機；不要只看停靠點數。",
        },
        {
            "項目": "最小風險做法",
            "內容": "在 NORMAL 後加保守均衡 pass：同日、同倉、同縣市內，把高行駛時間路線的少量邊緣點轉給低工時司機，轉移後重新 2-Opt 與 OSRM Route 驗證，超時或變差就回滾。",
        },
    ]

    output = {
        "driver_cause": driver_cause.sort_values("一週總工時", ascending=False).round(4).to_dict("records"),
        "top_records": pd.DataFrame(top_records).round(4).to_dict("records"),
        "county_mix": county_mix.round(4).to_dict("records"),
        "daily_cause": daily_cause.sort_values(["天數", "總工時"], ascending=[True, False]).round(4).to_dict("records"),
        "summary": summary,
        "diagnostic": {
            "avg_total": avg_total,
            "avg_service": avg_service,
            "avg_drive": avg_drive,
            "avg_stops": avg_stops,
            "avg_drive_per_stop": avg_drive_per_stop,
            "drive_share_mean": drive_share_mean,
            "service_share_mean": service_share_mean,
            "service_std": service_std,
            "drive_std": drive_std,
            "osrm_over_540": int((diag["OSRM是否超過540分鐘"].astype(str) == "是").sum()),
        },
    }
    REPORT_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(REPORT_JSON)
    print(pd.DataFrame(top_records).head(25).to_string(index=False))


if __name__ == "__main__":
    main()
