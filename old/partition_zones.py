import pandas as pd
from sklearn.cluster import KMeans
from math import radians, cos, sin, asin, sqrt

# Constants
INPUT_FILE = 'maintenance_data_aggregated.csv'
OUTPUT_FILE = 'maintenance_data_zoned.csv'
NUM_ZONES = 14
SPEED_KMH = 40  # Assumed speed
SPEED_KMM = SPEED_KMH / 60.0 # km per minute
DAILY_LIMIT = 540 # minutes

def haversine(lon1, lat1, lon2, lat2):
    """
    Calculate the great circle distance in kilometers between two points 
    on the earth (specified in decimal degrees)
    """
    # convert decimal degrees to radians 
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

    # haversine formula 
    dlon = lon2 - lon1 
    dlat = lat2 - lat1 
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a)) 
    r = 6371 # Radius of earth in kilometers. Use 3956 for miles. Determines return value units.
    return c * r

def process_zones():
    print(f"Reading {INPUT_FILE}...")
    try:
        df = pd.read_csv(INPUT_FILE)
    except FileNotFoundError:
        print("File not found.")
        return

    # Check columns
    if '緯度' not in df.columns or '經度' not in df.columns:
        print("Error: Missing coordinates columns.")
        return

    # Filter valid coords
    df = df.dropna(subset=['緯度', '經度']).copy()
    
    # K-Means Clustering
    print(f"Partitioning into {NUM_ZONES} zones...")
    kmeans = KMeans(n_clusters=NUM_ZONES, random_state=42, n_init=10)
    df['zone_id'] = kmeans.fit_predict(df[['緯度', '經度']])
    
    # Calculate Workload per Zone
    zone_stats = []
    
    for zone in range(NUM_ZONES):
        zone_df = df[df['zone_id'] == zone].copy()
        
        # 1. Maintenance Time
        # Assume 1 visit per location as requested by user ("都先當作每周清理一次")
        # '維護時間' is SUM. count is Visits. So per visit = Sum / Count.
        if 'count' in zone_df.columns:
            zone_df['per_visit_maint'] = zone_df['維護時間'] / zone_df['count']
        else:
            # Fallback if count missing (should exist)
            zone_df['per_visit_maint'] = zone_df['維護時間']
            
        total_maint_time = zone_df['per_visit_maint'].sum()
        
        # 2. Travel Time Calculation
        # Heuristic: Sort by Latitude (simple path)
        zone_df_sorted = zone_df.sort_values(by=['緯度', '經度'])
        
        travel_dist_km = 0
        prev_row = None
        
        for index, row in zone_df_sorted.iterrows():
            if prev_row is not None:
                dist = haversine(prev_row['經度'], prev_row['緯度'], row['經度'], row['緯度'])
                travel_dist_km += dist
            prev_row = row
            
        travel_time_min = travel_dist_km / SPEED_KMM
        
        total_time = total_maint_time + travel_time_min
        days_needed = total_time / DAILY_LIMIT
        
        zone_stats.append({
            'Zone ID': zone,
            'Locations': len(zone_df),
            'Maint Time (min)': round(total_maint_time, 2),
            'Travel Dist (km)': round(travel_dist_km, 2),
            'Travel Time (min)': round(travel_time_min, 2),
            'Total Time (min)': round(total_time, 2),
            'Days Needed (540m)': round(days_needed, 2)
        })
        
    # Save Output
    df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
    print(f"Saved zoned data to {OUTPUT_FILE}")
    
    # Print Summary
    stats_df = pd.DataFrame(zone_stats)
    print("\n--- Zone Partition Summary ---")
    print(stats_df.to_string(index=False))
    
    print(f"\nTotal Time All Zones: {stats_df['Total Time (min)'].sum():.2f}")
    print(f"Total Days All Zones: {stats_df['Days Needed (540m)'].sum():.2f}")

if __name__ == "__main__":
    process_zones()
