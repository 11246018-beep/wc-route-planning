# -*- coding: utf-8 -*-
"""
Generate Maintenance Map from Aggregated Data
Adapts visualization logic from phase1.py to maintenance_data_aggregated.csv
"""

import pandas as pd
import folium
from folium import IFrame
from folium.plugins import MarkerCluster
import os
import html
import re
from dataclasses import dataclass
from typing import List
from scipy.spatial import distance

# --- Configuration ---
INPUT_FILENAME = 'maintenance_data_aggregated.csv'
OUTPUT_MAP_NAME = 'maintenance_map_aggregated.html'

# --- Anchors Data ---
@dataclass
class Anchor:
    name: str
    highway_type: str
    lat: float
    lon: float

ANCHORS: List[Anchor] = [
    Anchor("國1-基隆", "N1", 25.1210, 121.7400),
    Anchor("國1-八堵", "N1", 25.1034, 121.7215),
    Anchor("國1-五堵", "N1", 25.0820, 121.6900),
    Anchor("國1-汐止", "N1", 25.0660, 121.6500),
    Anchor("國1-內湖", "N1", 25.0780, 121.5900),
    Anchor("國1-圓山", "N1", 25.0730, 121.5200),
    Anchor("國1-台北", "N1", 25.0580, 121.5100),
    Anchor("國1-三重", "N1", 25.0620, 121.4700),
    Anchor("國1-五股", "N1", 25.0900, 121.4400),
    Anchor("國1-林口", "N1", 25.0700, 121.3600),
    Anchor("國1-南崁", "N1", 25.0550, 121.2900),
    Anchor("國1-桃園", "N1", 25.0200, 121.2500),
    Anchor("國1-中壢", "N1", 24.9800, 121.2300),
    Anchor("國1-平鎮系統", "N1", 24.9400, 121.2100),
    Anchor("國1-楊梅", "N1", 24.9100, 121.1500),
    Anchor("國1-湖口", "N1", 24.8800, 121.0500),
    Anchor("國1-竹北", "N1", 24.8300, 121.0100),
    Anchor("國1-新竹", "N1", 24.8000, 120.9800),
    Anchor("國1-竹南", "N1", 24.7000, 120.8800),
    Anchor("國1-頭份", "N1", 24.6800, 120.9000),
    Anchor("國1-苗栗", "N1", 24.5700, 120.8200),
    Anchor("國3-汐止系統", "N3", 25.0700, 121.6200),
    Anchor("國3-深坑", "N3", 25.0000, 121.6200),
    Anchor("國3-樹林", "N3", 24.9900, 121.4000),
    Anchor("國3-三鶯", "N3", 24.9400, 121.3200),
    Anchor("國3-大溪", "N3", 24.8800, 121.2900),
    Anchor("國3-龍潭", "N3", 24.8400, 121.2100),
    Anchor("國3-關西", "N3", 24.7800, 121.1800),
    Anchor("國3-竹南", "N3", 24.6900, 120.8900),
    Anchor("國3-通霄", "N3", 24.4900, 120.6900),
    Anchor("台61-八里", "TH61", 25.1500, 121.4000),
    Anchor("台61-林口", "TH61", 25.0800, 121.3300),
    Anchor("台61-觀音", "TH61", 25.0400, 121.0800),
    Anchor("台61-新豐", "TH61", 24.8800, 120.9900),
    Anchor("台61-新竹", "TH61", 24.8100, 120.9300),
    Anchor("台61-竹南", "TH61", 24.6900, 120.8500),
    Anchor("台64-板橋", "TH64", 25.0100, 121.4600),
    Anchor("台65-新莊", "TH65", 25.0600, 121.4300),
    Anchor("台66-平鎮", "TH66", 24.9000, 121.2000),
    Anchor("台68-竹東", "TH68", 24.7400, 121.0700),
]

REAL_ANCHORS = pd.DataFrame([vars(a) for a in ANCHORS])
REAL_ANCHORS = REAL_ANCHORS.rename(columns={'name': 'IC_Name', 'lat': 'Lat', 'lon': 'Lon'})

DEPOTS = {
    'Wugu': {'Lat': 25.07154, 'Lon': 121.44169, 'Color': 'red'},
    'Pingzhen': {'Lat': 24.90703, 'Lon': 121.226872, 'Color': 'blue'}
}

def clean_text(text):
    return html.escape(re.sub(r'[\x00-\x1f\x7f]', '', str(text))).strip()

def get_zone_color(zone_id):
    colors = [
        'red', 'blue', 'green', 'purple', 'orange', 'darkred',
        'lightred', 'beige', 'darkblue', 'darkgreen', 'cadetblue',
        'darkpurple', 'white', 'pink', 'lightblue', 'lightgreen',
        'gray', 'black', 'lightgray'
    ]
    try:
        idx = int(zone_id)
        return colors[idx % len(colors)]
    except:
        return 'gray'

def generate_map():
    print(f"Reading {INPUT_FILENAME}...")
    try:
        df = pd.read_csv(INPUT_FILENAME, encoding='utf-8-sig')
    except UnicodeDecodeError:
        df = pd.read_csv(INPUT_FILENAME, encoding='big5')
    
    # Standardize column names based on the aggregated file
    # Expected columns: 服務地點,倉庫別,客戶名稱,維護時間,行車距離,郵遞區號3碼,郵遞區號,樓層,出租單號,週清1,週清2,緯度,經度,排程總序號,count,zone
    
    # Filter 
    df = df.dropna(subset=['緯度', '經度'])
    
    # --- Anchor Logic ---
    print("Calculating nearest anchors...")
    coords_cust = df[['緯度', '經度']].values
    coords_anchor = REAL_ANCHORS[['Lat', 'Lon']].values
    dist_matrix = distance.cdist(coords_cust, coords_anchor, 'euclidean')
    nearest_idx = dist_matrix.argmin(axis=1)
    df['Nearest_Anchor'] = REAL_ANCHORS.iloc[nearest_idx]['IC_Name'].values
    df['Anchor_Type'] = REAL_ANCHORS.iloc[nearest_idx]['highway_type'].values
    
    # --- Map Initialization ---
    center_lat = df['緯度'].mean()
    center_lon = df['經度'].mean()
    m = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles='OpenStreetMap')
    
    # Layers
    marker_cluster = MarkerCluster(name="聚合視圖 (Cluster)", control=True, overlay=True, show=False).add_to(m)
    zone_layer = folium.FeatureGroup(name="分區檢視 (Zone)", show=True).add_to(m)
    depot_layer = folium.FeatureGroup(name="倉庫分佈", show=False).add_to(m)
    anchor_layer = folium.FeatureGroup(name="交流道錨點", show=True).add_to(m)

    # Add Anchors
    for _, row in REAL_ANCHORS.iterrows():
        folium.Marker(
            location=[row['Lat'], row['Lon']],
            icon=folium.Icon(color='black', icon='flag'),
            popup=f"<b>{row['IC_Name']}</b><br>類型: {row['highway_type']}",
            tooltip=f"錨點: {row['IC_Name']}"
        ).add_to(anchor_layer)
        
    # Add Depots
    for name, info in DEPOTS.items():
        folium.Marker(
            location=[info['Lat'], info['Lon']],
            icon=folium.Icon(color=info['Color'], icon='home', prefix='glyphicon'),
            popup=f"<b>{name}</b>",
            tooltip=f"{name}"
        ).add_to(m)
        
    # Add Nodes
    print(f"Plotting {len(df)} nodes...")
    for idx, row in df.iterrows():
        lat = row['緯度']
        lon = row['經度']
        
        zone = row.get('zone', 'Unknown')
        color = get_zone_color(zone)
        
        popup_html = f"""
        <div style="font-family: 'Microsoft JhengHei', Arial; font-size: 12px; width: 280px;">
            <h4 style="margin: 0; color: #2c3e50;">
                {clean_text(row.get('服務地點', ''))} 
            </h4>
            <hr style="margin: 5px 0;">
            <b>Zone:</b> {zone}<br>
            <b>客戶:</b> {clean_text(row.get('客戶名稱', ''))}<br>
            <b>維護時間:</b> {row.get('維護時間', 0)}<br>
            <b>樓層:</b> {clean_text(row.get('樓層', ''))}<br>
            <b>倉庫:</b> {clean_text(row.get('倉庫別', ''))}<br>
            <b>最近交流道:</b> {clean_text(row['Nearest_Anchor'])}<br>
            <b>週清1:</b> {clean_text(row.get('週清1', ''))}<br>
            <b>週清2:</b> {clean_text(row.get('週清2', ''))}
        </div>
        """
        
        iframe = IFrame(popup_html, width=300, height=200)
        popup = folium.Popup(iframe, max_width=300)
        
        # Zone Layer
        folium.CircleMarker(
            location=[lat, lon],
            radius=6,
            popup=popup,
            tooltip=f"Zone {zone}: {clean_text(row.get('客戶名稱', ''))}",
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7
        ).add_to(zone_layer)
        
        # Cluster Layer
        folium.Marker(
            location=[lat, lon],
            popup=popup,
            icon=folium.Icon(color=color)
        ).add_to(marker_cluster)
        
        # Depot Layer (Color by Stock)
        stock_color = 'red' if '五股' in str(row.get('倉庫別', '')) else 'blue'
        folium.CircleMarker(
            location=[lat, lon],
            radius=4,
            popup=popup,
            color=stock_color,
            fill=True,
            fill_color=stock_color,
            fill_opacity=0.6
        ).add_to(depot_layer)

    folium.LayerControl(collapsed=False).add_to(m)
    m.save(OUTPUT_MAP_NAME)
    print(f"Map saved to {OUTPUT_MAP_NAME}")

if __name__ == "__main__":
    generate_map()
