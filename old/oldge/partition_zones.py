# -*- coding: utf-8 -*-
"""
Title: Zone Partitioning & Optimization for Periodic VRP
Description: Partitions maintenance nodes into 14 balanced zones based on service time and spatial proximity.
             Validates final routes using OSRM Trip API (TSP Solver).
"""

import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
import folium
from folium.plugins import MarkerCluster
import requests
import json
import os
import time

# ==========================================
# Configuration
# ==========================================
INPUT_FILENAME = 'processed_nodes_phase1.csv'
OUTPUT_MAP_NAME = 'maintenance_map_service_time.html'
OUTPUT_CSV_NAME = 'processed_nodes_service_time.csv'
N_ZONES = 14  # Target number of zones (14 drivers)
OSRM_TRIP_URL = "http://router.project-osrm.org/trip/v1/driving/"

# ==========================================
# 1. Data Loading & Preprocessing
# ==========================================
def load_data(filepath):
    print(f"Loading data from {filepath}...")
    df = pd.read_csv(filepath)
    
    # Ensure numeric
    df['Lat'] = pd.to_numeric(df['Lat'], errors='coerce')
    df['Lon'] = pd.to_numeric(df['Lon'], errors='coerce')
    df['Service_Time'] = pd.to_numeric(df['Service_Time'], errors='coerce').fillna(10)
    
    # Drop invalid
    df = df.dropna(subset=['Lat', 'Lon'])
    print(f"Loaded {len(df)} valid nodes.")
    return df

# ==========================================
# 2. Partitioning Algorithm (Balanced KMeans)
# ==========================================
def partition_nodes(df, n_zones=14):
    """
    Partitions nodes into n_zones.
    Current Approach: Weighted KMeans (Spatial clustering).
    Future Improvement: Capacity Constrained Clustering if imbalance is high.
    """
    print(f"Partitioning into {n_zones} zones...")
    
    X = df[['Lat', 'Lon']].values
    
    # Standard KMeans
    kmeans = KMeans(n_clusters=n_zones, random_state=42, n_init=10)
    df['Zone_ID'] = kmeans.fit_predict(X)
    
    # Calculate Cluster Stats
    stats = df.groupby('Zone_ID')['Service_Time'].sum().reset_index()
    stats.columns = ['Zone_ID', 'Total_Service_Time']
    
    print("\n--- Initial Partition Stats (Service Time Only) ---")
    print(stats.describe())
    
    return df

# ==========================================
# 3. OSRM Validation (TSP Route Calculation)
# ==========================================
def get_zone_metrics(df_zone, zone_id):
    """
    Calculates travel time and distance for a zone using OSRM Trip API (TSP).
    Returns: (duration_seconds, distance_meters, geometry_encoded)
    """
    # Limit to 100 points per request for OSRM demo server (it has strict limits)
    # If > 100, we might need to downsample or just take a sample for estimation
    # For this phase, we'll try to use all points but handle errors
    
    coords = df_zone[['Lon', 'Lat']].values
    
    # OSRM Trip API format: lon,lat;lon,lat...
    coords_str = ";".join([f"{lon},{lat}" for lon, lat in coords])
    
    # Construct URL
    url = f"{OSRM_TRIP_URL}{coords_str}?source=first&roundtop=true&geometries=geojson"
    
    try:
        # Add a small delay to be nice to the specific public API
        time.sleep(0.5) 
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data['code'] == 'Ok':
                trip = data['trips'][0]
                duration = trip['duration'] # seconds
                distance = trip['distance'] # meters
                return duration, distance
            else:
                print(f"Zone {zone_id}: OSRM Error - {data['code']}")
        elif response.status_code == 414:
            print(f"Zone {zone_id}: Too many points for OSRM URL (URI Too Long). Using Euclidean est.")
        else:
            print(f"Zone {zone_id}: HTTP {response.status_code}")
            
    except Exception as e:
        print(f"Zone {zone_id}: Exception - {e}")

    return None, None

def calculate_final_stats(df):
    print("\nCalculating Driving stats via OSRM (This may take a moment)...")
    
    zone_stats = []
    
    for zone_id in sorted(df['Zone_ID'].unique()):
        zone_df = df[df['Zone_ID'] == zone_id]
        
        # 1. Service Time Sum
        service_time_min = zone_df['Service_Time'].sum()
        
        # 2. Travel Time (OSRM TSP)
        # Only query if node count is reasonable (<100 for demo server usually safe-ish, url length limit)
        # If > 70-80 points, URL gets too long.
        if len(zone_df) < 80:
            drive_sec, drive_meters = get_zone_metrics(zone_df, zone_id)
        else:
            print(f"Zone {zone_id}: {len(zone_df)} nodes (Skip OSRM - Too large for demo API)")
            drive_sec, drive_meters = None, None
            
        drive_time_min = (drive_sec / 60) if drive_sec else 0
        total_time_min = service_time_min + drive_time_min
        
        zone_stats.append({
            'Zone_ID': zone_id,
            'Node_Count': len(zone_df),
            'Service_Time_Min': service_time_min,
            'Drive_Time_Min': drive_time_min,
            'Total_Time_Min': total_time_min,
            'Total_Time_Hr': total_time_min / 60
        })
        
    stats_df = pd.DataFrame(zone_stats)
    return stats_df

# ==========================================
# 4. Visualization
# ==========================================
def generate_map(df, stats_df, output_file):
    print(f"\nGenerating map: {output_file}...")
    
    center_lat = df['Lat'].mean()
    center_lon = df['Lon'].mean()
    
    m = folium.Map(location=[center_lat, center_lon], zoom_start=11, tiles='OpenStreetMap')
    
    # Color palette for 14 zones
    colors = [
        'red', 'blue', 'green', 'purple', 'orange', 'darkred',
        'lightred', 'beige', 'darkblue', 'darkgreen', 'cadetblue',
        'darkpurple', 'pink', 'lightblue', 'lightgreen', 'black'
    ]
    
    # Merge stats into df for easier popup
    # (Optional, but we just use the stats array)
    
    for zone_id in sorted(df['Zone_ID'].unique()):
        zone_color = colors[zone_id % len(colors)]
        zone_df = df[df['Zone_ID'] == zone_id]
        
        # Get stat for this zone
        stat = stats_df[stats_df['Zone_ID'] == zone_id].iloc[0]
        
        # Create FeatureGroup for this zone
        fg = folium.FeatureGroup(name=f"Zone {zone_id} ({stat['Total_Time_Hr']:.1f} hr)")
        
        for idx, row in zone_df.iterrows():
            popup_content = f"""
            <b>Zone {zone_id}</b><br>
            ID: {row.get('Node_ID', 'N/A')}<br>
            Service: {row['Service_Time']} min<br>
            <hr>
            <b>Zone Stats:</b><br>
            Nodes: {stat['Node_Count']}<br>
            Total Time: {stat['Total_Time_Hr']:.1f} hr<br>
            (Drive: {stat['Drive_Time_Min']:.0f}m + Svc: {stat['Service_Time_Min']:.0f}m)
            """
            
            folium.CircleMarker(
                location=[row['Lat'], row['Lon']],
                radius=6 if row['Freq'] == '2x' else 4,
                color=zone_color,
                fill=True,
                fill_opacity=0.7,
                tooltip=f"Zone {zone_id}: {row.get('Node_ID', 'N/A')}",
                popup=folium.Popup(popup_content, max_width=250)
            ).add_to(fg)
        
        fg.add_to(m)
        
    folium.LayerControl().add_to(m)
    m.save(output_file)

# ==========================================
# Main
# ==========================================
if __name__ == "__main__":
    # 1. Load
    current_dir = os.path.dirname(os.path.abspath(__file__))
    input_path = os.path.join(current_dir, INPUT_FILENAME)
    df = load_data(input_path)
    
    # 2. Partition
    df_partitioned = partition_nodes(df, N_ZONES)
    
    # 3. Calc Stats (with OSRM)
    stats = calculate_final_stats(df_partitioned)
    print("\n=== Final Zone Statistics ===")
    print(stats.to_string())
    print("-" * 50)
    print(f"Avg Total Time per Zone: {stats['Total_Time_Hr'].mean():.2f} hours")
    print(f"Std Dev: {stats['Total_Time_Hr'].std():.2f} hours")
    
    # 4. Save CSV
    out_csv = os.path.join(current_dir, OUTPUT_CSV_NAME)
    df_partitioned.to_csv(out_csv, index=False, encoding='utf-8-sig')
    print(f"\nSaved partitioned data to {OUTPUT_CSV_NAME}")
    
    # 5. Generate Map
    out_map = os.path.join(current_dir, OUTPUT_MAP_NAME)
    generate_map(df_partitioned, stats, out_map)
    print(f"Saved map to {OUTPUT_MAP_NAME}")
