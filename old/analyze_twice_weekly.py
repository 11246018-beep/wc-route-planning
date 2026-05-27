import pandas as pd
from sklearn.cluster import KMeans
from math import radians, cos, sin, asin, sqrt
import os

# Constants
INPUT_FILE = 'maintenance_data_aggregated.csv'
OUTPUT_FILE = 'twice_weekly_analysis.txt'
SPEED_KMH = 40.0
SPEED_KMM = SPEED_KMH / 60.0

def haversine(lon1, lat1, lon2, lat2):
    """
    Calculate the great circle distance in kilometers between two points 
    on the earth (specified in decimal degrees)
    """
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1 
    dlat = lat2 - lat1 
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a)) 
    r = 6371 
    return c * r

def analyze():
    print(f"Reading {INPUT_FILE}...")
    if not os.path.exists(INPUT_FILE):
        print(f"Error: {INPUT_FILE} not found.")
        return

    df = pd.read_csv(INPUT_FILE)
    
    # Filter for count == 2
    # Ensure 'count' column exists and is numeric
    if 'count' not in df.columns:
        print("Error: 'count' column not found.")
        return
        
    df_filtered = df[df['count'] == 2].copy()
    num_samples = len(df_filtered)
    print(f"Found {num_samples} locations with cleaning frequency of 2 times/week.")
    
    if num_samples == 0:
        print("No records found with count == 2.")
        return

    # Check coordinates
    if '緯度' not in df_filtered.columns or '經度' not in df_filtered.columns:
        print("Error: Missing coordinate columns.")
        return
    
    df_filtered = df_filtered.dropna(subset=['緯度', '經度'])
    
    # Determine K for KMeans
    # Heuristic: roughly 15-20 stops per zone? or just split into a few regions?
    # If we have e.g. 50 points, maybe 3 zones.
    # Let's target ~15 locations per cluster as a reasonable route size, but at least 1.
    k = max(1, int(num_samples / 15))
    if k == 0: k = 1
    # For small sets, enforce at least 2 if possible to show clustering, but if strictly small, 1 is fine.
    if num_samples > 10 and k < 2:
        k = 2
        
    print(f"Clustering into {k} zones using K-Means...")
    
    kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
    df_filtered['cluster_id'] = kmeans.fit_predict(df_filtered[['緯度', '經度']])
    
    # Output Results
    results = []
    results.append(f"Twice Weekly Cleaning Analysis (Count=2)\n")
    results.append(f"Total Locations: {num_samples}\n")
    results.append(f"Number of Clusters (K): {k}\n")
    results.append(f"Video estimates assume speed: {SPEED_KMH} km/h\n")
    results.append("=" * 60 + "\n")
    
    total_travel_time_all = 0
    total_dist_all = 0
    
    for cluster in range(k):
        cluster_df = df_filtered[df_filtered['cluster_id'] == cluster].copy()
        
        # Sort to approximate a route
        cluster_df = cluster_df.sort_values(by=['緯度', '經度'])
        
        results.append(f"\nCluster {cluster + 1} (Locations: {len(cluster_df)})\n")
        results.append("-" * 30 + "\n")
        
        prev_row = None
        cluster_dist = 0
        
        # Iterate to calculate pairwise leg distances
        path_indices = list(cluster_df.index)
        for i in range(len(path_indices)):
            curr_idx = path_indices[i]
            curr_row = cluster_df.loc[curr_idx]
            
            name = curr_row.get('客戶名稱', 'Unknown')
            addr = curr_row.get('服務地點', 'Unknown')
            
            if prev_row is not None:
                dist = haversine(prev_row['經度'], prev_row['緯度'], curr_row['經度'], curr_row['緯度'])
                time_min = dist / SPEED_KMM
                cluster_dist += dist
                
                results.append(f"  Travel -> {dist:.2f} km ({time_min:.1f} min)\n")
                
            results.append(f"  Point {i+1}: {name} ({addr})\n")
            prev_row = curr_row
            
        cluster_time = cluster_dist / SPEED_KMM
        total_dist_all += cluster_dist
        total_travel_time_all += cluster_time
        
        results.append(f"\n  [Cluster {cluster + 1} Totals]\n")
        results.append(f"  Total Distance: {cluster_dist:.2f} km\n")
        results.append(f"  Est. Travel Time: {cluster_time:.1f} min\n")
        results.append("." * 60 + "\n")

    results.append("=" * 60 + "\n")
    results.append(f"Overall Total Distance: {total_dist_all:.2f} km\n")
    results.append(f"Overall Total Travel Time: {total_travel_time_all:.1f} min\n")
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.writelines(results)
        
    print(f"Analysis saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    analyze()
