# -*- coding: utf-8 -*-
"""
====================================================================================================
專案名稱：台灣北部多場站週期性車輛路徑問題 (Multi-Depot Periodic VRP) - Phase 1 (優化版)
====================================================================================================
角色設定：資深作業研究 (OR) 資料科學家
功能模組：數位地基 (Digital Foundation)

主要功能：
    1. 資料讀取與清洗 (Smart Load & Clean)
       - 智慧編碼偵測與型別轉換
       - 經緯度精度標準化 (5位小數 ≈ 1.1m 誤差)
       - 缺失值處理與異常值過濾

    2. 節點濃縮 (Node Condensation)
       - 將同經緯度的多筆工單合併為單一超級節點 (Super-Node)
       - 服務時間累加，頻率採邏輯OR (有一筆2x即視為2x)
       - 保留完整追溯資訊 (order_id)

    3. 錨點吸附 (Highway Anchoring)
       - 計算每個客戶點到最近交流道的歐式距離
       - 為後續路徑規劃提供高速公路路網參考

    4. 互動地圖生成 (Advanced Visualization)
       - 多圖層設計：聚合視圖、頻率分佈、倉庫分佈、序號分佈
       - 視覺編碼：星號(2x高頻) vs 驚嘆號(1x低頻)
       - 顏色編碼：紅(五股/2x) vs 藍(平鎮/1x)

====================================================================================================
"""

# ==========================================
# 0. 套件導入 (Import Libraries)
# ==========================================
import os
import sys
from pathlib import Path
import html
import re
from dataclasses import dataclass
from typing import List

import django
import pandas as pd
import numpy as np
import folium
from folium import IFrame
from folium.plugins import MarkerCluster
from scipy.spatial import distance

sys.stdout.reconfigure(encoding="utf-8")

# ==========================================
# Django 環境設定
# ==========================================
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[3]      # ...\projects\projects
DJANGO_APP_ROOT = CURRENT_FILE.parents[2]   # ...\projects\projects\route_system

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if str(DJANGO_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "route_system.settings")
django.setup()

from routing.models import ServicePoint

# ==========================================
# 1. 全域參數設定 (Configuration)
# ==========================================
BASE_DIR = DJANGO_APP_ROOT
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_CSV = OUTPUT_DIR / "processed_nodes_phase1.csv"
OUTPUT_MAP = OUTPUT_DIR / "maintenance_map_phase1.html"

# 舊版相容常數（給 run_all.py 使用）
OUTPUT_CSV_NAME = "processed_nodes_phase1.csv"
OUTPUT_HTML_NAME = "maintenance_map_phase1.html"
OUTPUT_MAP_NAME = OUTPUT_HTML_NAME

OUTPUT_CSV_PATH = str(OUTPUT_CSV)
OUTPUT_HTML_PATH = str(OUTPUT_MAP)
OUTPUT_MAP_PATH = OUTPUT_HTML_PATH

COORD_PRECISION = 5
MAP_CENTER = [24.98, 121.35]
MAP_ZOOM = 10

WUGU_DEPOT = {"name": "五股總部", "lat": 25.07154, "lon": 121.44169}
PINGZHEN_DEPOT = {"name": "平鎮總部", "lat": 24.90703, "lon": 121.226872}

DEPOTS = [WUGU_DEPOT, PINGZHEN_DEPOT]

# 北部常見交流道/路網錨點（可自行再補）
ANCHORS = [
    {"name": "五股交流道", "lat": 25.08233, "lon": 121.43879},
    {"name": "林口交流道", "lat": 25.07911, "lon": 121.37246},
    {"name": "泰山轉接道", "lat": 25.04960, "lon": 121.42123},
    {"name": "桃園交流道", "lat": 25.01210, "lon": 121.29261},
    {"name": "機場系統交流道", "lat": 25.06139, "lon": 121.23628},
    {"name": "中壢轉接道", "lat": 24.96544, "lon": 121.22826},
    {"name": "平鎮系統交流道", "lat": 24.93044, "lon": 121.21472},
    {"name": "楊梅交流道", "lat": 24.90083, "lon": 121.14556},
]


# ==========================================
# 2. 資料結構
# ==========================================
@dataclass
class DepotInfo:
    name: str
    lat: float
    lon: float


# ==========================================
# 3. 輔助函式
# ==========================================
def safe_float(value, default=np.nan):
    try:
        if value is None:
            return default
        text = str(value).strip()
        if text == "" or text.lower() == "nan":
            return default
        return float(text)
    except Exception:
        return default


def safe_int(value, default=0):
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
        return ""
    text = str(value).strip()
    if text.lower() == "nan":
        return ""
    return text


def detect_depot(raw_text: str, lat: float, lon: float) -> str:
    text = clean_text(raw_text)
    if "五股" in text:
        return "Wugu"
    if "平鎮" in text:
        return "Pingzhen"

    if np.isnan(lat) or np.isnan(lon):
        return "Unknown"

    d_wugu = euclidean_km(lat, lon, WUGU_DEPOT["lat"], WUGU_DEPOT["lon"])
    d_pingzhen = euclidean_km(lat, lon, PINGZHEN_DEPOT["lat"], PINGZHEN_DEPOT["lon"])
    return "Wugu" if d_wugu <= d_pingzhen else "Pingzhen"


def depot_name(depot_code: str) -> str:
    if depot_code == "Wugu":
        return "五股總部"
    if depot_code == "Pingzhen":
        return "平鎮總部"
    return "未知場站"


def euclidean_km(lat1, lon1, lat2, lon2):
    return float(distance.euclidean((lat1, lon1), (lat2, lon2)) * 111)


def nearest_anchor(lat: float, lon: float):
    if np.isnan(lat) or np.isnan(lon):
        return "", np.nan

    best = None
    best_dist = None
    for a in ANCHORS:
        d = euclidean_km(lat, lon, a["lat"], a["lon"])
        if best_dist is None or d < best_dist:
            best = a
            best_dist = d

    return best["name"], round(best_dist, 3)


def parse_county(address: str) -> str:
    addr = clean_text(address)
    pattern = r"(基隆市|台北市|臺北市|新北市|桃園市|桃園縣|新竹市|新竹縣|苗栗縣|台中市|臺中市|彰化縣|南投縣|雲林縣|嘉義市|嘉義縣|台南市|臺南市|高雄市|屏東縣|宜蘭縣|花蓮縣|台東縣|臺東縣|澎湖縣|金門縣|連江縣)"
    match = re.search(pattern, addr)
    if match:
        return match.group(1).replace("臺", "台")
    if len(addr) >= 3 and addr[2] in ["縣", "市"]:
        return addr[:3].replace("臺", "台")
    return "Unknown"


def popup_html(row):
    lines = [
        f"<b>Node_ID：</b>{html.escape(clean_text(row.get('Node_ID')))}",
        f"<b>場站：</b>{html.escape(clean_text(row.get('Depot_Name')))}",
        f"<b>地址：</b>{html.escape(clean_text(row.get('Address')))}",
        f"<b>頻率：</b>{html.escape(clean_text(row.get('Freq')))}",
        f"<b>Service_Time：</b>{row.get('Service_Time', '')}",
        f"<b>weekly_1：</b>{row.get('weekly_1', '')}",
        f"<b>weekly_2：</b>{row.get('weekly_2', '')}",
        f"<b>最近交流道：</b>{html.escape(clean_text(row.get('Nearest_Anchor')))}",
        f"<b>距交流道(km)：</b>{row.get('Anchor_Dist_KM', '')}",
        f"<b>工單數：</b>{row.get('Order_Count', '')}",
    ]
    body = "<br>".join(lines)
    return f"<div style='font-size:12px; width:320px'>{body}</div>"


def marker_color(row):
    depot = clean_text(row.get("Depot_Code"))
    is_2x = safe_int(row.get("weekly_2"), 0) > 0
    if depot == "Wugu":
        return "red" if is_2x else "lightred"
    if depot == "Pingzhen":
        return "blue" if is_2x else "lightblue"
    return "gray"


def marker_icon(row):
    is_2x = safe_int(row.get("weekly_2"), 0) > 0
    return "star" if is_2x else "info-sign"


# ==========================================
# 4. 讀取 ServicePoint 並整理成 DataFrame
# ==========================================
def load_service_points_df() -> pd.DataFrame:
    qs = ServicePoint.objects.all().values()
    df = pd.DataFrame(list(qs))

    print("ServicePoint 欄位：", list(df.columns))

    if df.empty:
        raise ValueError("ServicePoint 資料表沒有資料，無法產生 processed_nodes_phase1.csv")

    rename_map = {}
    for col in df.columns:
        lower = col.lower().strip()

        # 經緯度欄位容錯
        if lower in ("latitude", "lat", "緯度", "y", "lat_wgs84"):
            rename_map[col] = "Lat"
        elif lower in ("longitude", "lon", "lng", "經度", "x", "lon_wgs84", "lng_wgs84"):
            rename_map[col] = "Lon"

        # 場站
        elif lower in ("depot_raw", "depot", "depot_name", "場站", "站別"):
            rename_map[col] = "Depot_Raw"

        # 地址
        elif lower in ("address", "addr", "地址"):
            rename_map[col] = "Address"

        # 服務時間
        elif lower in ("service_time", "service_minutes", "service_min", "服務時間"):
            rename_map[col] = "Service_Time"

        # 其他欄位
        elif lower in ("node_id", "node", "節點編號"):
            rename_map[col] = "Node_ID"
        elif lower in ("freq", "frequency", "頻率"):
            rename_map[col] = "Freq"
        elif lower in ("weekly_1", "week_1", "每週一次"):
            rename_map[col] = "weekly_1"
        elif lower in ("weekly_2", "week_2", "每週兩次"):
            rename_map[col] = "weekly_2"
        elif lower in ("order_id", "工單id", "工單編號"):
            rename_map[col] = "order_id"
        elif lower in ("original_id", "原始id"):
            rename_map[col] = "Original_ID"

    df = df.rename(columns=rename_map)

    required_defaults = {
        "Lat": np.nan,
        "Lon": np.nan,
        "Depot_Raw": "",
        "Address": "",
        "Service_Time": 0,
        "Node_ID": "",
        "Freq": "",
        "weekly_1": 1,
        "weekly_2": 0,
        "order_id": "",
        "Original_ID": "",
    }
    for col, default in required_defaults.items():
        if col not in df.columns:
            df[col] = default

    df["Lat"] = df["Lat"].apply(safe_float).round(COORD_PRECISION)
    df["Lon"] = df["Lon"].apply(safe_float).round(COORD_PRECISION)
    df["Service_Time"] = df["Service_Time"].apply(lambda x: max(0.0, safe_float(x, 0.0)))
    df["weekly_1"] = df["weekly_1"].apply(lambda x: max(0, safe_int(x, 1)))
    df["weekly_2"] = df["weekly_2"].apply(lambda x: max(0, safe_int(x, 0)))

    for col in ["Depot_Raw", "Address", "Node_ID", "Freq", "order_id", "Original_ID"]:
        df[col] = df[col].apply(clean_text)

    print("Lat 非空筆數：", df["Lat"].notna().sum())
    print("Lon 非空筆數：", df["Lon"].notna().sum())
    print(df[["Lat", "Lon"]].head())

    df = df.dropna(subset=["Lat", "Lon"]).copy()
    df = df[(df["Lat"].between(20, 27)) & (df["Lon"].between(118, 123))].copy()

    if df.empty:
        raise ValueError("清洗後沒有有效經緯度資料")

    return df

# ==========================================
# 5. 節點濃縮
# ==========================================
def condense_nodes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Coord_Key"] = list(zip(df["Lat"], df["Lon"]))

    condensed_rows = []

    grouped = df.groupby("Coord_Key", dropna=False)
    for _, group in grouped:
        first = group.iloc[0].copy()

        lat = round(safe_float(first["Lat"]), COORD_PRECISION)
        lon = round(safe_float(first["Lon"]), COORD_PRECISION)

        service_time = round(group["Service_Time"].apply(lambda x: safe_float(x, 0.0)).sum(), 2)
        weekly_1 = int(group["weekly_1"].apply(lambda x: max(0, safe_int(x, 0))).max())
        weekly_2 = int(group["weekly_2"].apply(lambda x: max(0, safe_int(x, 0))).max())

        depot_code = detect_depot(
            first.get("Depot_Raw", ""),
            lat,
            lon,
        )
        county = parse_county(first.get("Address", ""))
        anchor_name, anchor_dist = nearest_anchor(lat, lon)

        node_id = clean_text(first.get("Node_ID")) or f"N_{lat}_{lon}"
        order_ids = [clean_text(x) for x in group["order_id"].tolist() if clean_text(x)]
        original_ids = [clean_text(x) for x in group["Original_ID"].tolist() if clean_text(x)]

        condensed_rows.append(
            {
                "Node_ID": node_id,
                "Lat": lat,
                "Lon": lon,
                "Depot_Raw": clean_text(first.get("Depot_Raw", "")),
                "Depot_Code": depot_code,
                "Depot_Name": depot_name(depot_code),
                "Address": clean_text(first.get("Address", "")),
                "County": county,
                "Service_Time": service_time,
                "weekly_1": weekly_1,
                "weekly_2": weekly_2,
                "Freq": clean_text(first.get("Freq", "")),
                "Order_Count": len(group),
                "order_id": "|".join(order_ids),
                "Original_ID": "|".join(original_ids),
                "Nearest_Anchor": anchor_name,
                "Anchor_Dist_KM": anchor_dist,
            }
        )

    result = pd.DataFrame(condensed_rows)

    if result.empty:
        raise ValueError("節點濃縮後沒有資料")

    result = result.sort_values(
        by=["Depot_Code", "County", "Lat", "Lon", "Node_ID"],
        ascending=[True, True, True, True, True],
    ).reset_index(drop=True)

    return result


# ==========================================
# 6. 互動地圖生成
# ==========================================
def generate_map(df: pd.DataFrame, save_path: Path):
    m = folium.Map(location=MAP_CENTER, zoom_start=MAP_ZOOM, tiles="OpenStreetMap")

    cluster_all = MarkerCluster(name="客戶點（聚合）").add_to(m)
    fg_2x = folium.FeatureGroup(name="高頻節點 (2x)")
    fg_1x = folium.FeatureGroup(name="低頻節點 (1x)")
    fg_wugu = folium.FeatureGroup(name="五股節點")
    fg_pingzhen = folium.FeatureGroup(name="平鎮節點")
    fg_depots = folium.FeatureGroup(name="場站")

    for depot in DEPOTS:
        folium.Marker(
            location=[depot["lat"], depot["lon"]],
            popup=depot["name"],
            tooltip=depot["name"],
            icon=folium.Icon(color="green", icon="home"),
        ).add_to(fg_depots)

    for _, row in df.iterrows():
        lat = safe_float(row["Lat"])
        lon = safe_float(row["Lon"])

        popup = folium.Popup(IFrame(html=popup_html(row), width=350, height=220), max_width=360)
        icon = folium.Icon(color=marker_color(row), icon=marker_icon(row), prefix="glyphicon")

        marker = folium.Marker(
            location=[lat, lon],
            popup=popup,
            tooltip=clean_text(row.get("Node_ID")),
            icon=icon,
        )
        marker.add_to(cluster_all)

        is_2x = safe_int(row.get("weekly_2"), 0) > 0
        if is_2x:
            folium.Marker(
                location=[lat, lon],
                popup=popup,
                tooltip=clean_text(row.get("Node_ID")),
                icon=icon,
            ).add_to(fg_2x)
        else:
            folium.Marker(
                location=[lat, lon],
                popup=popup,
                tooltip=clean_text(row.get("Node_ID")),
                icon=icon,
            ).add_to(fg_1x)

        depot_code = clean_text(row.get("Depot_Code"))
        if depot_code == "Wugu":
            folium.CircleMarker(
                location=[lat, lon],
                radius=5,
                color="red",
                fill=True,
                fill_opacity=0.7,
                tooltip=clean_text(row.get("Node_ID")),
            ).add_to(fg_wugu)
        elif depot_code == "Pingzhen":
            folium.CircleMarker(
                location=[lat, lon],
                radius=5,
                color="blue",
                fill=True,
                fill_opacity=0.7,
                tooltip=clean_text(row.get("Node_ID")),
            ).add_to(fg_pingzhen)

    fg_2x.add_to(m)
    fg_1x.add_to(m)
    fg_wugu.add_to(m)
    fg_pingzhen.add_to(m)
    fg_depots.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    m.save(str(save_path))

# ==========================================
# 舊版相容函式（給 run_all.py 使用）
# ==========================================
def load_from_database():
    """
    舊版相容：從 Django 資料庫讀取 ServicePoint，回傳原始 DataFrame
    """
    return load_service_points_df()


def load_and_process_data(raw_df):
    """
    舊版相容：接收原始 DataFrame，回傳 phase1 濃縮後節點資料
    """
    return condense_nodes(raw_df)


def save_processed_nodes(df_nodes, output_csv=None):
    """
    舊版相容：儲存 phase1 節點資料到 CSV
    """
    save_path = Path(output_csv) if output_csv else OUTPUT_CSV
    save_path.parent.mkdir(parents=True, exist_ok=True)
    df_nodes.to_csv(save_path, index=False, encoding="utf-8-sig")
    print(f"已輸出 CSV: {save_path}")
    return str(save_path)


def generate_interactive_map(df_nodes, output_file=None):
    """
    舊版相容：輸出 phase1 地圖
    """
    save_path = Path(output_file) if output_file else OUTPUT_MAP
    save_path.parent.mkdir(parents=True, exist_ok=True)
    generate_map(df_nodes, save_path)
    print(f"已輸出地圖: {save_path}")
    return str(save_path)

def generate_html_map(df_nodes, output_file=None):
    """
    舊版相容：run_all.py 需要的函式名稱
    """
    save_path = Path(output_file) if output_file else OUTPUT_MAP
    save_path.parent.mkdir(parents=True, exist_ok=True)
    generate_map(df_nodes, save_path)
    print(f"已輸出地圖: {save_path}")
    return str(save_path)




# ==========================================
# 7. 主程式
# ==========================================
def main():
    print("=== Phase 1 開始 ===")
    print(f"專案根目錄: {PROJECT_ROOT}")
    print(f"Django App 根目錄: {DJANGO_APP_ROOT}")
    print(f"輸出資料夾: {OUTPUT_DIR}")

    raw_df = load_service_points_df()
    print(f"原始資料筆數: {len(raw_df)}")

    condensed_df = condense_nodes(raw_df)
    print(f"節點濃縮後筆數: {len(condensed_df)}")

    condensed_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"已輸出 CSV: {OUTPUT_CSV}")

    generate_map(condensed_df, OUTPUT_MAP)
    print(f"已輸出地圖: {OUTPUT_MAP}")

    depot_summary = condensed_df.groupby("Depot_Code").size().to_dict()
    freq_2x = int((condensed_df["weekly_2"] > 0).sum())
    freq_1x = int(len(condensed_df) - freq_2x)

    print("=== Phase 1 完成 ===")
    print(f"場站統計: {depot_summary}")
    print(f"2x 節點數: {freq_2x}")
    print(f"1x 節點數: {freq_1x}")


if __name__ == "__main__":
    main()