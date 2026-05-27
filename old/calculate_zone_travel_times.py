import pandas as pd
from math import radians, cos, sin, asin, sqrt

# Constants
INPUT_FILE = 'maintenance_data_zoned.csv'
OUTPUT_FILE = 'zone_travel_times.txt'
SPEED_KMH = 40.0
SPEED_KMM = SPEED_KMH / 60.0

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
    r = 6371 # Radius of earth in kilometers
    return c * r

def calculate_and_save():
    print(f"Reading {INPUT_FILE}...")
    try:
        df = pd.read_csv(INPUT_FILE)
    except FileNotFoundError:
        print("File not found.")
        return

    if 'zone' in df.columns:
        zone_col = 'zone'
    elif 'zone_id' in df.columns:
        zone_col = 'zone_id'
    else:
        print("Error: Zone column not found.")
        return

    zones = sorted(df[zone_col].unique())
    
    results = []
    total_all_zones = 0
    
    header = f"Zone Travel Time Estimates (Based on Lat/Lon Distance, Speed: {SPEED_KMH} km/h)\n"
    header += "=" * 60 + "\n"
    results.append(header)
    
    for zone in zones:
        zone_df = df[df[zone_col] == zone].copy()
        
        # Sort by Latitude then Longitude to approximate a path
        zone_df = zone_df.sort_values(by=['緯度', '經度'])
        
        total_dist_km = 0
        prev_row = None
        
        for index, row in zone_df.iterrows():
            if prev_row is not None:
                dist = haversine(prev_row['經度'], prev_row['緯度'], row['經度'], row['緯度'])
                total_dist_km += dist
            prev_row = row
            
        total_time_min = total_dist_km / SPEED_KMM
        total_all_zones += total_time_min
        
        line = f"Zone {zone}: {total_time_min:.2f} minutes ({total_dist_km:.2f} km) - {len(zone_df)} locations\n"
        results.append(line)
        print(line.strip())

    results.append("=" * 60 + "\n")
    results.append(f"Total Travel Time (All Zones): {total_all_zones:.2f} minutes\n")

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.writelines(results)
    
    print(f"\nResults saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    calculate_and_save()
