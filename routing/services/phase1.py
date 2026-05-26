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
import pandas as pd  # 資料處理與分析
import numpy as np  # 數值計算
import folium  # 互動地圖繪製
from folium import IFrame  # 客製化彈出視窗
from folium.plugins import MarkerCluster  # 地圖標記聚合
import os  # 檔案系統操作
import sys
import django
sys.stdout.reconfigure(encoding='utf-8')

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "route_system.settings")
django.setup()
from routing.models import ServicePoint
import html  # HTML特殊字元轉義
import re  # 正則表達式處理
from dataclasses import dataclass  # 資料類別定義
from typing import List  # 型別標註
from scipy.spatial import distance  # 空間距離計算

# ==========================================
# 1. 全域參數設定 (Configuration)
# ==========================================

# --- 輸出檔案設定 ---
# 注意：只從 Django 資料庫讀取，不再使用 Excel 檔案
OUTPUT_MAP_NAME = 'maintenance_map_phase1.html'  # 輸出地圖檔名
OUTPUT_CSV_NAME = 'processed_nodes_phase1.csv'  # 輸出節點資料

# --- 場站 (Depots) 座標與視覺設定 ---
# 定義：總部位置與地圖顯示顏色
# 用途：作為路徑規劃的起點/終點，以及地圖視覺化參考
DEPOTS = {
    'Wugu': {  # 五股總部
        'Lat': 25.07154,  # 緯度
        'Lon': 121.44169,  # 經度
        'Color': 'red',  # 地圖標記顏色 (紅色)
        'Drivers': 2  # 可用司機數量 (新增)
    },
    'Pingzhen': {  # 平鎮總部
        'Lat': 24.90703,  # 緯度
        'Lon': 121.226872,  # 經度
        'Color': 'blue',  # 地圖標記顏色 (藍色)
        'Drivers': 12  # 可用司機數量 (新增)
    }
}


# --- 錨點資料結構定義 ---
# 用途：定義交流道錨點的資料格式
# 優點：使用 dataclass 提供型別檢查與自動生成 __init__
@dataclass
class Anchor:
    """交流道錨點資料結構"""
    name: str  # 交流道名稱 (例如：國1-五股)
    highway_type: str  # 路網類型 (N1=國道一號, N3=國道三號, TH61=台61西濱, TH64-68=快速道路)
    lat: float  # 緯度
    lon: float  # 經度


# --- 完整路網錨點清單 (基隆至苗栗) ---
# 設計理念：涵蓋主要高速公路出口，作為路徑規劃的骨幹節點
# 資料來源：台灣國道與快速道路實際交流道位置
# 應用：後續計算每個客戶點最近的交流道，用於路徑優化與地理分群
ANCHORS: List[Anchor] = [
    # ═══════════════════════════════════════
    # 國道一號 (中山高速公路) - 南北主幹線
    # ═══════════════════════════════════════
    Anchor("國1-基隆", "N1", 25.1210, 121.7400),  # 最北端
    Anchor("國1-八堵", "N1", 25.1034, 121.7215),
    Anchor("國1-五堵", "N1", 25.0820, 121.6900),
    Anchor("國1-汐止", "N1", 25.0660, 121.6500),
    Anchor("國1-內湖", "N1", 25.0780, 121.5900),
    Anchor("國1-圓山", "N1", 25.0730, 121.5200),
    Anchor("國1-台北", "N1", 25.0580, 121.5100),
    Anchor("國1-三重", "N1", 25.0620, 121.4700),
    Anchor("國1-五股", "N1", 25.0900, 121.4400),  # 五股總部鄰近
    Anchor("國1-林口", "N1", 25.0700, 121.3600),
    Anchor("國1-南崁", "N1", 25.0550, 121.2900),
    Anchor("國1-桃園", "N1", 25.0200, 121.2500),
    Anchor("國1-中壢", "N1", 24.9800, 121.2300),
    Anchor("國1-平鎮系統", "N1", 24.9400, 121.2100),  # 平鎮總部鄰近
    Anchor("國1-楊梅", "N1", 24.9100, 121.1500),
    Anchor("國1-湖口", "N1", 24.8800, 121.0500),
    Anchor("國1-竹北", "N1", 24.8300, 121.0100),
    Anchor("國1-新竹", "N1", 24.8000, 120.9800),
    Anchor("國1-竹南", "N1", 24.7000, 120.8800),
    Anchor("國1-頭份", "N1", 24.6800, 120.9000),
    Anchor("國1-苗栗", "N1", 24.5700, 120.8200),  # 最南端

    # ═══════════════════════════════════════
    # 國道三號 (福爾摩沙高速公路) - 替代路線
    # ═══════════════════════════════════════
    Anchor("國3-汐止系統", "N3", 25.0700, 121.6200),
    Anchor("國3-深坑", "N3", 25.0000, 121.6200),
    Anchor("國3-樹林", "N3", 24.9900, 121.4000),
    Anchor("國3-三鶯", "N3", 24.9400, 121.3200),
    Anchor("國3-大溪", "N3", 24.8800, 121.2900),
    Anchor("國3-龍潭", "N3", 24.8400, 121.2100),
    Anchor("國3-關西", "N3", 24.7800, 121.1800),
    Anchor("國3-竹南", "N3", 24.6900, 120.8900),
    Anchor("國3-通霄", "N3", 24.4900, 120.6900),

    # ═══════════════════════════════════════
    # 台61線 (西部濱海快速公路) - 沿海走廊
    # ═══════════════════════════════════════
    Anchor("台61-八里", "TH61", 25.1500, 121.4000),
    Anchor("台61-林口", "TH61", 25.0800, 121.3300),
    Anchor("台61-觀音", "TH61", 25.0400, 121.0800),
    Anchor("台61-新豐", "TH61", 24.8800, 120.9900),
    Anchor("台61-新竹", "TH61", 24.8100, 120.9300),
    Anchor("台61-竹南", "TH61", 24.6900, 120.8500),

    # ═══════════════════════════════════════
    # 快速道路 (橫向連結) - 東西向聯絡道
    # ═══════════════════════════════════════
    Anchor("台64-板橋", "TH64", 25.0100, 121.4600),  # 板橋-八里快速道路
    Anchor("台65-新莊", "TH65", 25.0600, 121.4300),  # 新莊-林口快速道路
    Anchor("台66-平鎮", "TH66", 24.9000, 121.2000),  # 東西向快速道路-觀音大溪線
    Anchor("台68-竹東", "TH68", 24.7400, 121.0700),  # 東西向快速道路-南寮竹東線
]

# --- 轉換錨點清單為 DataFrame ---
# 目的：將 Python 物件轉換為 Pandas DataFrame 以便進行向量化運算
# 應用：計算距離矩陣時需要 NumPy array 格式
REAL_ANCHORS = pd.DataFrame([vars(a) for a in ANCHORS])  # vars() 將 dataclass 轉為 dict
REAL_ANCHORS = REAL_ANCHORS.rename(columns={'name': 'IC_Name', 'lat': 'Lat', 'lon': 'Lon'})


# ==========================================
# 2. 資料處理核心函式 (Data Processing Core)
# ==========================================
def load_from_database():
    """
    從 Django 資料庫讀取 ServicePoint
    """
    print("\n>>> 從資料庫讀取 ServicePoint ...")

    qs = ServicePoint.objects.all().values()

    df = pd.DataFrame(list(qs))

    print(f"✓ 成功讀取 {len(df)} 筆資料")

    return df
def load_and_process_data(raw_df):
    """
    資料載入與處理主函式

    功能流程：
        1. 智慧讀取檔案 (自動偵測格式與編碼)
        2. 欄位對應與型別轉換
        3. 資料清洗 (移除無效座標)
        4. 頻率邏輯判定 (2x vs 1x)
        5. 節點濃縮 (同位置合併)
        6. 錨點吸附 (計算最近交流道)

    參數：
        raw_df (pd.DataFrame): 從 Django 資料庫讀取的原始資料

    回傳：
        pd.DataFrame: 處理後的節點資料表，包含濃縮後的超級節點
    """
    print(f"\n{'=' * 80}")
    print(f">>> [Phase 1] Processing data from Django database")
    print(f"{'=' * 80}")

    # ─────────────────────────────────────
    # 步驟 1: 欄位對應與型別轉換
    # ─────────────────────────────────────
    # 清理欄位名稱：移除前後空白
    raw_df.columns = raw_df.columns.str.strip()
    print(f"    ✓ 原始資料筆數: {len(raw_df)}")

    # 初始化處理後的 DataFrame
    df = pd.DataFrame()

    # 1.1 客戶 ID：強制轉為字串以保留前導零
    df['Original_ID'] = raw_df['client_name'].astype(str)

    # 1.2 經緯度處理：
    # - 使用 pd.to_numeric() 強制轉換，錯誤值變為 NaN
    # - round(5) 統一精度到小數點後5位 (約1.1公尺誤差)
    df['Lat'] = pd.to_numeric(raw_df['lat'], errors='coerce').round(5)
    df['Lon'] = pd.to_numeric(raw_df['lon'], errors='coerce').round(5)

    # 1.3 服務時間：
    # - 轉換為數值型別
    # - 空值補 10 分鐘 (預設最小服務時間)
    df['S_Time_Raw'] = pd.to_numeric(raw_df['service_time'], errors='coerce').fillna(10)

    # 1.4 輔助資訊欄位：地址、倉庫、訂單號、樓層、週期資訊
    cols_to_process = [
        ('address', 'Address'),
        ('depot', 'Depot_Raw'),
        ('order_id', 'order_id'),  # 使用 order_id 作為 order_id
        ('floor', 'floor')
    ]
    
    for raw_col, target_col in cols_to_process:
        if raw_col in raw_df.columns:
            df[target_col] = raw_df[raw_col].astype(str).replace(['nan', 'NaN', 'None'], '').str.strip()
        else:
            df[target_col] = ''

    # 1.5 週期資訊：weekly_1 和 weekly_2
    df['weekly_1'] = pd.to_numeric(raw_df.get('weekly_1', 0), errors='coerce').fillna(0).astype(int)
    df['weekly_2'] = pd.to_numeric(raw_df.get('weekly_2', 0), errors='coerce').fillna(0).astype(int)
    # 移除無效經緯度 (NaN 或超出合理範圍)
    initial_len = len(df)
    df = df.dropna(subset=['Lat', 'Lon'])  # 移除經緯度為空的資料

    # 可選：加入座標合理性檢查 (台灣範圍：北緯21-26度，東經119-122度)
    df = df[(df['Lat'] >= 21) & (df['Lat'] <= 26) &
            (df['Lon'] >= 119) & (df['Lon'] <= 122)]

    removed_count = initial_len - len(df)
    if removed_count > 0:
        print(f"    ✓ 已剔除 {removed_count} 筆無效座標資料")

    # 若清洗後已無有效座標，提早結束避免後續除以 0
    if len(df) == 0:
        print("    ⚠︎ 清洗後沒有可用資料，請檢查經緯度欄位或欄位對應設定")
        return pd.DataFrame()

    # ═══════════════════════════════════════════════════════
    # 【核心演算法 I】：節點濃縮 (Super-Node Aggregation)
    # ═══════════════════════════════════════════════════════
    # 目的：將同一地理位置的多筆工單合併為單一「超級節點」
    # 效益：
    #   1. 降低問題規模 (3311筆 -> 約1500-2000個節點)
    #   2. 反映實際作業：同地點多設備通常一次服務完成
    #   3. 加速後續路徑優化計算
    print(f"\n    {'─' * 60}")
    print(f"    執行節點濃縮 (Super-Node Aggregation)...")
    print(f"    {'─' * 60}")

    # 定義聚合規則 (Aggregation Rules)
    # 每個欄位的合併邏輯需仔細設計以保留關鍵資訊
    def join_unique_text(values, sep=","):
        cleaned = []
        for value in values:
            if pd.isna(value):
                continue
            text = str(value).strip()
            if not text or text.lower() == "nan":
                continue
            cleaned.append(text)
        return sep.join(sorted(set(cleaned)))

    agg_rules = {
        # 字串類欄位：保留所有唯一值並排序
        'Original_ID': lambda x: join_unique_text(x, ' | '),  # 客戶ID用 | 分隔
        'order_id': lambda x: join_unique_text(x, ','),       # 工單 ID
        'floor': lambda x: join_unique_text(x, ','),          # 樓層資訊

        # 數值類欄位：加總
        'S_Time_Raw': 'sum',  # 同地點服務時間累加 (例如：3台設備各10分鐘 -> 30分鐘)
        'weekly_1': 'max',    # 同地點取最高訪問頻率 (例如：某位置需要1x就是1x，不累加)
        'weekly_2': 'max',    # 同地點取最高訪問頻率

        # 保留第一筆資料
        'Address': 'first',  # 地址相同取第一筆即可
        'Depot_Raw': 'first',  # 原始倉庫歸屬
    }

    # 執行 GroupBy 聚合 (僅根據地理位置合併)
    df_agg = df.groupby(['Lat', 'Lon'], as_index=False).agg(agg_rules)

    # 重新建立 Freq 欄位：根據聚合後的 weekly_2 總和判定
    df_agg['Freq'] = 1
    df_agg.loc[df_agg['weekly_2'] > 0, 'Freq'] = 2
    df_agg['Freq'] = df_agg['Freq'].map({1: '1x', 2: '2x'})

    # 產生唯一節點 ID (格式：N_0001, N_0002, ...)
    df_agg['Node_ID'] = [f'N_{i:04d}' for i in range(len(df_agg))]

    # 統計每個節點包含的原始工單數量
    df_agg['Order_Count'] = df.groupby(['Lat', 'Lon']).size().values

    # 重新命名服務時間欄位
    df_agg.rename(columns={'S_Time_Raw': 'Service_Time'}, inplace=True)

    # 輸出統計資訊
    print(f"    ✓ 濃縮完成:")
    print(f"      - 原始工單數: {len(df)} 筆")
    print(f"      - 濃縮後節點數: {len(df_agg)} 個")
    print(f"      - 壓縮率: {(1 - len(df_agg) / len(df)) * 100:.1f}%")
    print(f"      - 平均每節點工單數: {df_agg['Order_Count'].mean():.2f} 筆")
    print(f"      - 最多工單的節點: {df_agg['Order_Count'].max()} 筆")

    # ═══════════════════════════════════════════════════════
    # 【核心演算法 II】：錨點吸附 (Highway Anchoring)
    # ═══════════════════════════════════════════════════════
    # 目的：為每個客戶節點找到最近的高速公路交流道
    # 應用：
    #   1. 地理分群：相同交流道的點可能在相同路徑上
    #   2. 路徑優化：優先考慮高速公路路網以降低行駛時間
    #   3. 跨區支援決策：根據交流道位置判斷支援可行性
    print(f"\n    {'─' * 60}")
    print(f"    執行錨點吸附 (Highway Anchoring)...")
    print(f"    {'─' * 60}")

    # 準備座標陣列
    coords_cust = df_agg[['Lat', 'Lon']].values  # 客戶節點座標 (N x 2)
    coords_anchor = REAL_ANCHORS[['Lat', 'Lon']].values  # 交流道座標 (M x 2)

    # 計算歐式距離矩陣 (N x M)
    # 注意：這裡使用經緯度歐式距離，僅用於相對距離比較
    # 生產環境建議使用 Haversine 公式計算真實地理距離
    dist_matrix = distance.cdist(coords_cust, coords_anchor, 'euclidean')

    # 對每個客戶節點，找到距離最小的交流道索引
    nearest_idx = dist_matrix.argmin(axis=1)

    # 將結果寫回 DataFrame
    df_agg['Nearest_Anchor'] = REAL_ANCHORS.iloc[nearest_idx]['IC_Name'].values
    df_agg['Anchor_Type'] = REAL_ANCHORS.iloc[nearest_idx]['highway_type'].values

    # 可選：計算並儲存實際距離 (公里)
    # 使用簡化公式：1度緯度 ≈ 111公里，1度經度 ≈ 111*cos(緯度)公里
    df_agg['Anchor_Distance_km'] = dist_matrix.min(axis=1) * 111  # 粗略估計

    # 輸出統計資訊
    print(f"    ✓ 錨點吸附完成:")
    anchor_stats = df_agg['Anchor_Type'].value_counts()
    for anchor_type, count in anchor_stats.items():
        print(f"      - {anchor_type}: {count} 個節點")

    print(f"\n{'=' * 80}")
    print(f">>> 資料處理完成: {len(df)} 筆原始資料 -> {len(df_agg)} 個超級節點")
    print(f"{'=' * 80}\n")

    return df_agg


# ==========================================
# 3. 地圖視覺化函式 (Visualization)
# ==========================================
def generate_html_map(df, output_file=OUTPUT_MAP_NAME):
    """
    產生高階互動地圖，支援多圖層切換

    視覺編碼設計：
        【形狀】：
            - 星號 (star)：2x 高頻率客戶
            - 驚嘆號 (info-sign)：1x 低頻率客戶

        【顏色】：
            - 頻率圖層：紅色(2x) vs 藍色(1x)
            - 倉庫圖層：紅色(五股) vs 藍色(平鎮)
            - 序號圖層：依排程序號分配不同顏色

    參數：
        df (pd.DataFrame): 處理後的節點資料
        output_file (str): 輸出 HTML 檔案路徑
    """
    print(f"\n{'=' * 80}")
    print(f">>> [Phase 2] 正在繪製互動地圖: {output_file}")
    print(f"{'=' * 80}")

    # 資料驗證
    if df.empty:
        print("    ✗ [Error] 無資料可繪圖，請檢查資料處理結果")
        return

    # ─────────────────────────────────────
    # 輔助函式：清理文字避免 HTML 注入攻擊
    # ─────────────────────────────────────
    def clean(text):
        """移除控制字元並轉義 HTML 特殊字元"""
        return html.escape(re.sub(r'[\x00-\x1f\x7f]', '', str(text))).strip()

    # ─────────────────────────────────────
    # 初始化地圖
    # ─────────────────────────────────────
    # 計算地圖中心點：所有節點的平均經緯度
    center_lat = df['Lat'].mean()
    center_lon = df['Lon'].mean()

    # 建立 Folium 地圖物件
    # tiles='OpenStreetMap': 使用開源地圖底圖
    # zoom_start=10: 初始縮放等級 (適合城市尺度)
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=10,
        tiles='OpenStreetMap'
    )
    print(f"    ✓ 地圖中心點: ({center_lat:.5f}, {center_lon:.5f})")

    # ─────────────────────────────────────
    # 建立多圖層結構
    # ─────────────────────────────────────
    # 設計理念：不同視角的資訊分層顯示，使用者可自由切換

    # 圖層 1：聚合視圖 (Cluster) - 宏觀密度分析
    marker_cluster = MarkerCluster(
        name="聚合視圖 (Cluster)",
        control=True,
        overlay=True,
        show=False  # 預設顯示
    ).add_to(m)

    # 圖層 2：頻率分佈 - 辨識高頻 vs 低頻客戶
    freq_layer = folium.FeatureGroup(
        name="頻率分佈 (2x=紅星, 1x=藍驚嘆號)",
        show=False
    ).add_to(m)

    # 圖層 3：倉庫分佈 - 查看原始歸屬
    depot_layer = folium.FeatureGroup(
        name="原始倉庫分佈 (五股=紅, 平鎮=藍)",
        show=False
    ).add_to(m)

    # 圖層 4：序號分佈 - 追蹤排程編號
    serial_layer = folium.FeatureGroup(
        name="排程序號分佈 (多彩)",
        show=True
    ).add_to(m)

    # 圖層 5：交流道錨點 - 路網參考
    anchor_layer = folium.FeatureGroup(
        name="交流道錨點",
        show=True  # 預設顯示
    ).add_to(m)

    # ─────────────────────────────────────
    # 準備序號顏色對應表
    # ─────────────────────────────────────
    # 取得所有唯一序號 (空值設為 'Unknown')
    unique_serials = sorted(list(set(df['order_id'].replace('', 'Unknown').astype(str))))

    # Folium 支援的顏色清單
    color_palette = [
        'red', 'blue', 'green', 'purple', 'orange', 'darkred',
        'lightred', 'beige', 'darkblue', 'darkgreen', 'cadetblue',
        'darkpurple', 'pink', 'lightblue', 'lightgreen', 'black', 'gray'
    ]

    # 建立序號 -> 顏色的映射字典
    serial_color_map = {
        s: color_palette[i % len(color_palette)]
        for i, s in enumerate(unique_serials)
    }

    print(f"    ✓ 序號顏色對應: {len(unique_serials)} 種序號")

    # ─────────────────────────────────────
    # 繪製交流道錨點 (黑色旗幟)
    # ─────────────────────────────────────
    print(f"    正在繪製 {len(REAL_ANCHORS)} 個交流道錨點...")
    for _, row in REAL_ANCHORS.iterrows():
        folium.Marker(
            location=[row['Lat'], row['Lon']],
            icon=folium.Icon(color='black', icon='flag'),
            popup=f"<b>{row['IC_Name']}</b><br>類型: {row['highway_type']}",
            tooltip=f"錨點: {row['IC_Name']}"
        ).add_to(anchor_layer)

    # ─────────────────────────────────────
    # 繪製倉庫總部 (大型房屋圖示)
    # ─────────────────────────────────────
    print(f"    正在繪製 {len(DEPOTS)} 個倉庫總部...")
    for depot_name, depot_info in DEPOTS.items():
        folium.Marker(
            location=[depot_info['Lat'], depot_info['Lon']],
            icon=folium.Icon(
                color=depot_info['Color'],
                icon='home',
                prefix='glyphicon'  # 使用 glyphicon 圖示庫
            ),
            popup=f"<b>{depot_name} 總部</b><br>司機數: {depot_info['Drivers']}",
            tooltip=f"總部: {depot_name}"
        ).add_to(m)

    # ─────────────────────────────────────
    # 批次繪製客戶節點標記
    # ─────────────────────────────────────
    print(f"    正在渲染 {len(df)} 個客戶節點標記...")

    for idx, row in df.iterrows():
        # 進度顯示 (每500筆顯示一次)
        if (idx + 1) % 500 == 0:
            print(f"      處理進度: {idx + 1}/{len(df)}")

        lat, lon = row['Lat'], row['Lon']

        # ═══════════════════════════════════════
        # 準備彈出視窗內容 (Popup HTML)
        # ═══════════════════════════════════════
        popup_html = f"""
        <div style="font-family: 'Microsoft JhengHei', Arial; font-size: 12px; width: 280px;">
            <h4 style="margin: 0; color: #2c3e50;">
                {clean(row['Node_ID'])} 
                <span style="color: #7f8c8d;">({clean(row['Anchor_Type'])})</span>
            </h4>
            <hr style="margin: 5px 0; border: none; border-top: 1px solid #bdc3c7;">

            <table style="width: 100%; font-size: 11px;">
                <tr>
                    <td style="font-weight: bold; width: 35%;">頻率:</td>
                    <td style="color: {'#e74c3c' if row['Freq'] == '2x' else '#3498db'};">
                        {clean(row['Freq'])}
                    </td>
                </tr>
                <tr>
                    <td style="font-weight: bold;">總工時:</td>
                    <td>{row['Service_Time']:.0f} 分鐘</td>
                </tr>
                <tr>
                    <td style="font-weight: bold;">工單數:</td>
                    <td>{row['Order_Count']} 筆</td>
                </tr>
                <tr>
                    <td style="font-weight: bold;">排程序號:</td>
                    <td>{clean(row['order_id'])}</td>
                </tr>
                <tr>
                    <td style="font-weight: bold;">最近交流道:</td>
                    <td>{clean(row['Nearest_Anchor'])}</td>
                </tr>
                <tr>
                    <td style="font-weight: bold;">原始倉庫:</td>
                    <td>{clean(row['Depot_Raw'])}</td>
                </tr>
            </table>

            <hr style="margin: 5px 0; border: none; border-top: 1px solid #ecf0f1;">
            <div style="color: #95a5a6; font-size: 10px; max-height: 40px; overflow: auto;">
                客戶ID: {clean(row['Original_ID'][:80])}...
            </div>
        </div>
        """

        # ═══════════════════════════════════════
        # 視覺編碼邏輯 (Visual Encoding)
        # ═══════════════════════════════════════

        # 【形狀編碼】：依頻率決定圖示
        icon_symbol = 'star' if row['Freq'] == '2x' else 'info-sign'

        # 【顏色編碼】：依圖層目的分別定義
        # (A) 頻率顏色
        freq_color = 'red' if row['Freq'] == '2x' else 'blue'

        # (B) 倉庫顏色
        depot_raw_str = str(row['Depot_Raw'])
        if '平鎮' in depot_raw_str:
            depot_color = 'blue'
        elif '五股' in depot_raw_str:
            depot_color = 'red'
        else:
            depot_color = 'gray'  # 未知或其他

        # (C) 序號顏色
        serial_val = str(row['order_id']) if str(row['order_id']) else 'Unknown'
        serial_color = serial_color_map.get(serial_val, 'gray')

        # ═══════════════════════════════════════
        # 加入各圖層 (Layer Assignment)
        # ═══════════════════════════════════════
        # 關鍵：每個節點重複建立4次，分別加入不同圖層但使用不同顏色

        # 輔助函式：建立新的 Popup 物件 (Folium 要求每次建立新實例)
        def create_popup():
            return folium.Popup(IFrame(popup_html, width=300, height=220), max_width=300)

        # 圖層 1：聚合視圖 (使用頻率顏色+頻率圖示)
        folium.Marker(
            location=[lat, lon],
            icon=folium.Icon(color=freq_color, icon=icon_symbol),
            popup=create_popup(),
            tooltip=f"{row['Node_ID']} ({row['Freq']})"
        ).add_to(marker_cluster)

        # 圖層 2：頻率分佈 (使用頻率顏色+頻率圖示)
        folium.Marker(
            location=[lat, lon],
            icon=folium.Icon(color=freq_color, icon=icon_symbol),
            popup=create_popup(),
            tooltip=f"Freq: {row['Freq']}"
        ).add_to(freq_layer)

        # 圖層 3：倉庫分佈 (使用倉庫顏色+頻率圖示)
        folium.Marker(
            location=[lat, lon],
            icon=folium.Icon(color=depot_color, icon=icon_symbol),
            popup=create_popup(),
            tooltip=f"Depot: {row['Depot_Raw']}"
        ).add_to(depot_layer)

        # 圖層 4：序號分佈 (使用序號顏色+頻率圖示)
        folium.Marker(
            location=[lat, lon],
            icon=folium.Icon(color=serial_color, icon=icon_symbol),
            popup=create_popup(),
            tooltip=f"Serial: {row['order_id']}"
        ).add_to(serial_layer)

    # ─────────────────────────────────────
    # 加入圖層控制器
    # ─────────────────────────────────────
    folium.LayerControl(
        collapsed=False,  # 預設展開圖層選單
        position='topright'
    ).add_to(m)

    # ─────────────────────────────────────
    # 儲存地圖
    # ─────────────────────────────────────
    m.save(output_file)
    print(f"    ✓ 地圖已成功生成: {output_file}")
    print(f"{'=' * 80}\n")


# ==========================================
# 4. 主程式執行 (Main Execution)
# ==========================================
if __name__ == "__main__":
    print("\n" + "=" * 80)
    print(" 台灣北部多場站週期性車輛路徑問題 (Multi-Depot Periodic VRP)")
    print(" Phase 1: 數位地基建置 (Digital Foundation)")
    print("=" * 80 + "\n")

    # 取得當前目錄
    current_dir = os.path.dirname(os.path.abspath(__file__))
    

    # ═══════════════════════════════════════
    # 步驟 1：資料處理
    # ═══════════════════════════════════════
    raw_df = load_from_database()
    df_nodes = load_and_process_data(raw_df)

    # 驗證處理結果
    if df_nodes.empty:
        print("✗ [失敗] 資料處理後為空，請檢查：")
        print("  1. Django 資料庫是否包含有效資料")
        print("  2. ServicePoint 模型的欄位名稱是否正確")
        print("  3. 經緯度欄位是否包含有效數值")
        exit(1)

    # ═══════════════════════════════════════
    # 步驟 2：顯示統計資訊
    # ═══════════════════════════════════════
    print("\n" + "=" * 80)
    print(" [Phase 1 結果統計報表]")
    print("=" * 80)
    print(f" 總節點數: {len(df_nodes)}")
    print(
        f" 2x 節點數: {len(df_nodes[df_nodes['Freq'] == '2x'])} ({len(df_nodes[df_nodes['Freq'] == '2x']) / len(df_nodes) * 100:.1f}%)")
    print(
        f" 1x 節點數: {len(df_nodes[df_nodes['Freq'] == '1x'])} ({len(df_nodes[df_nodes['Freq'] == '1x']) / len(df_nodes) * 100:.1f}%)")
    print("-" * 80)
    print(f" 平均服務時間: {df_nodes['Service_Time'].mean():.1f} 分鐘")
    print(f" 最大服務時間: {df_nodes['Service_Time'].max():.1f} 分鐘")
    print(f" 總服務時間: {df_nodes['Service_Time'].sum():.1f} 分鐘 ({df_nodes['Service_Time'].sum() / 60:.1f} 小時)")
    print("-" * 80)
    print(f" 路網分佈:")
    for anchor_type, count in df_nodes['Anchor_Type'].value_counts().items():
        print(f"   {anchor_type}: {count} 個節點 ({count / len(df_nodes) * 100:.1f}%)")
    print("=" * 80 + "\n")

    # ═══════════════════════════════════════
    # 步驟 3：匯出 CSV (供 Phase 2 使用)
    # ═══════════════════════════════════════
    output_csv_path = os.path.join(current_dir, OUTPUT_CSV_NAME)
    os.makedirs(os.path.dirname(output_csv_path), exist_ok=True)
    df_nodes.to_csv(output_csv_path, index=False, encoding='utf-8-sig')
    print(f"✓ CSV 已匯出: {output_csv_path}")

    # ═══════════════════════════════════════
    # 步驟 4：生成互動地圖
    # ═══════════════════════════════════════
    output_map_path = os.path.join(current_dir, OUTPUT_MAP_NAME)
    os.makedirs(os.path.dirname(output_map_path), exist_ok=True)
    generate_html_map(df_nodes, output_map_path)

    # ═══════════════════════════════════════
    # 完成訊息
    # ═══════════════════════════════════════
    print("\n" + "=" * 80)
    print(" [Phase 1 完成]")
    print("=" * 80)
    print(f" 輸出檔案:")
    print(f"   1. 節點資料: {OUTPUT_CSV_NAME}")
    print(f"   2. 互動地圖: {OUTPUT_MAP_NAME}")
    print(f"\n 下一步:")
    print(f"   - 開啟 {OUTPUT_MAP_NAME} 檢視地理分佈")
    print(f"   - 使用 {OUTPUT_CSV_NAME} 進行 Phase 2 路徑優化")
    print("=" * 80 + "\n")
