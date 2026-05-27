import pandas as pd
from math import radians, cos, sin, asin, sqrt

# Constants
INPUT_FILE = 'maintenance_data_aggregated.csv' # Using the aggregated file which has the 'zone' column
WORKERS = 14
DAILY_HOURS = 9
MINUTES_PER_DAY = DAILY_HOURS * 60 # 540 minutes
DAYS_PER_WEEK = 5 # Assuming a standard 5-day work week
SPEED_KMH = 40.0
SPEED_KMM = SPEED_KMH / 60.0

def haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1 
    dlat = lat2 - lat1 
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a)) 
    r = 6371 
    return c * r

def check_feasibility():
    print("Loading data...")
    try:
        df = pd.read_csv(INPUT_FILE)
    except FileNotFoundError:
        print(f"Error: {INPUT_FILE} not found.")
        return

    # Ensure zone column exists
    if 'zone' in df.columns:
        zone_col = 'zone'
    elif 'zone_id' in df.columns:
        zone_col = 'zone_id'
    else:
        print("Error: 'zone' column not found in dataset.")
        return

    zones = sorted(df[zone_col].unique())
    
    total_maint_time = 0
    total_travel_time = 0
    
    print(f"\nAnalyzing {len(zones)} zones...")
    print("-" * 80)
    print(f"{'Zone':<5} | {'Locs':<5} | {'Maint (min)':<12} | {'Travel (min)':<12} | {'Total (min)':<12} | {'Days (1 person)':<15}")
    print("-" * 80)
    
    zone_stats = []

    for zone in zones:
        zone_df = df[df[zone_col] == zone].copy()
        
        # 1. Maintenance Time
        # The column '維護時間' is the sum for that location.
        # We assume the dataset represents the full weekly workload.
        m_time = zone_df['維護時間'].sum()
        
        # 2. Travel Time (Heuristic Path: Sort by Lat, Lon)
        zone_df = zone_df.sort_values(by=['緯度', '經度'])
        t_dist_km = 0
        prev_row = None
        for _, row in zone_df.iterrows():
            if prev_row is not None:
                t_dist_km += haversine(prev_row['經度'], prev_row['緯度'], row['經度'], row['緯度'])
            prev_row = row
            
        t_time = t_dist_km / SPEED_KMM
        
        z_total = m_time + t_time
        days_req = z_total / MINUTES_PER_DAY
        
        total_maint_time += m_time
        total_travel_time += t_time
        
        print(f"{zone:<5} | {len(zone_df):<5} | {m_time:<12.2f} | {t_time:<12.2f} | {z_total:<12.2f} | {days_req:<15.2f}")
        zone_stats.append(z_total)

    total_system_load = total_maint_time + total_travel_time
    daily_capacity = WORKERS * MINUTES_PER_DAY
    # 5-day week capacity
    weekly_capacity = daily_capacity * DAYS_PER_WEEK 
    
    days_to_complete_system = total_system_load / daily_capacity

    print("-" * 80)
    print(f"\n--- Feasibility Summary ---")
    print(f"Total Weekly Load:       {total_system_load:.2f} minutes")
    print(f"  - Maintenance:         {total_maint_time:.2f} minutes")
    print(f"  - Travel (est):        {total_travel_time:.2f} minutes")
    print(f"\nWorkforce Capacity:")
    print(f"  - Workers:             {WORKERS}")
    print(f"  - Daily Hours:         {DAILY_HOURS} ({MINUTES_PER_DAY} min)")
    print(f"  - Total Daily Cap:     {daily_capacity} minutes")
    print(f"  - Total Weekly Cap:    {weekly_capacity} minutes (assuming 5 days)")

    print(f"\n--- Result ---")
    print(f"Days to complete all work (with 14 people): {days_to_complete_system:.2f} days")
    
    if days_to_complete_system <= 5:
        print("✅ FEASIBLE within a 5-day work week.")
    elif days_to_complete_system <= 7:
        print("⚠️ FEASIBLE but requires >5 days (approx 6-7 days).")
    else:
        print("❌ NOT FEASIBLE within a week.")

    # Bottleneck check
    max_zone_time = max(zone_stats)
    print(f"\nHeaviest Zone Load: {max_zone_time:.2f} minutes ({max_zone_time/MINUTES_PER_DAY:.2f} days for 1 person)")
    if max_zone_time > weekly_capacity:
         print("❌ Critical: The largest zone exceeds the entire team's weekly capacity (unlikely but checking).")
    else:
         print("Note: Heaviest zone fits within team capacity, but may require multiple people if > 540 mins/day.")

if __name__ == "__main__":
    check_feasibility()
