import pandas as pd
import googlemaps
import json
import os
import time
import sys

print("Script starting...", flush=True)

# Configuration
INPUT_FILE = 'Weekly_Schedule.xlsx'
OUTPUT_REPORT = 'daily_hours_report.txt'
CACHE_FILE = 'time_cache.json'
GOOGLE_MAPS_API_KEY = 'AIzaSyCJbc6sUbEMxNao23NM3V1W9iKw1V7Xc3Q'

# Base Coordinates
# PZ: Pingzhen, WG: Wugu
BASE_COORDS = {'PZ': (24.90679, 121.22683), 'WG': (25.07055, 121.44141)}
STAFF_TEAM_MAP = {
    'S01': 'PZ', 'S02': 'PZ', 'S03': 'PZ', 'S04': 'PZ', 'S05': 'PZ', 'S06': 'PZ', 'S07': 'PZ', 
    'S08': 'PZ', 'S09': 'PZ', 'S10': 'PZ', 'S11': 'PZ', 'S12': 'PZ', 'S13': 'WG', 'S14': 'WG'
}

# Add S13/S14 if they are missing in map but present in data
# Assuming default assignment if not in map (though map covers all known)

# Initialize Client
try:
    print("Initializing Google Maps Client...", flush=True)
    gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)
    print("Google Maps Client Initialized.", flush=True)
except Exception as e:
    print(f"Error initializing Google Maps Client: {e}", flush=True)
    sys.exit(1)

# Cache Handling
time_cache = {}

def load_cache():
    global time_cache
    if os.path.exists(CACHE_FILE):
        print(f"Loading cache from {CACHE_FILE}...", flush=True)
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            time_cache = json.load(f)
        print(f"Loaded {len(time_cache)} entries from cache.", flush=True)

def save_cache():
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(time_cache, f, ensure_ascii=False, indent=2)

def get_google_duration(start_lat, start_lon, end_lat, end_lon):
    """
    Returns duration in minutes between two points using Google Directions API.
    Uses caching.
    """
    # Create key
    key = f"{start_lat},{start_lon}|{end_lat},{end_lon}"
    
    if key in time_cache:
        return time_cache[key]
    
    try:
        # Request
        directions = gmaps.directions(
            origin=(start_lat, start_lon),
            destination=(end_lat, end_lon),
            mode='driving'
        )
        
        if directions:
            # Get duration in seconds
            duration_sec = directions[0]['legs'][0]['duration']['value']
            duration_mins = duration_sec / 60.0
            
            # Update cache
            time_cache[key] = duration_mins
            return duration_mins
        else:
            print(f"No directions found for {key}")
            return 0
            
    except Exception as e:
        print(f"API Error for {key}: {e}")
        return 0

def calculate_hours():
    load_cache()
    
    print("Loading schedule...")
    try:
        df = pd.read_excel(INPUT_FILE)
    except Exception as e:
        print(f"Error loading {INPUT_FILE}: {e}")
        return

    # Ensure columns exist
    # Mapping based on previous file content:
    # '員工代號', '日程(Day)', '順序', '緯度', '經度', '維護時間(分)', '任務屬性'
    
    print("Calculating hours...")
    
    report_lines = []
    report_lines.append("詳細工時報表 (含實際行車與維護時間)")
    report_lines.append("=============================================")
    
    total_api_calls = 0
    
    # Group by Staff and Day
    grouped = df.groupby(['員工代號', '日程(Day)'])
    
    # Sort keys to be orderly
    sorted_keys = sorted(grouped.groups.keys())
    
    for (staff_id, day) in sorted_keys:
        group = grouped.get_group((staff_id, day)).sort_values('順序')
        
        team = STAFF_TEAM_MAP.get(staff_id, 'PZ')
        base_lat, base_lon = BASE_COORDS[team]
        
        current_lat, current_lon = base_lat, base_lon # Start at base
        
        daily_drive_mins = 0
        daily_work_mins = 0
        daily_dist_log = []
        
        for idx, row in group.iterrows():
            dest_lat = row['緯度']
            dest_lon = row['經度']
            work_mins = row['維護時間(分)']
            job_type = str(row['任務屬性'])
            
            # Determine path type
            if 'Via Base' in job_type:
                # 1. Curr -> Base
                leg1 = get_google_duration(current_lat, current_lon, base_lat, base_lon)
                # 2. Base -> Dest
                leg2 = get_google_duration(base_lat, base_lon, dest_lat, dest_lon)
                drive_mins = leg1 + leg2
            else:
                # Direct
                drive_mins = get_google_duration(current_lat, current_lon, dest_lat, dest_lon)
            
            daily_drive_mins += drive_mins
            daily_work_mins += work_mins
            
            # Update position
            current_lat, current_lon = dest_lat, dest_lon
            
        # Return to base at end of day
        return_mins = get_google_duration(current_lat, current_lon, base_lat, base_lon)
        daily_drive_mins += return_mins
        
        total_mins = daily_drive_mins + daily_work_mins
        total_hours = total_mins / 60.0
        
        # Formatting
        line = f"員工: {staff_id} | Day: {day} | 維護: {daily_work_mins:.1f}分 | 行車: {daily_drive_mins:.1f}分 | 總計: {total_mins:.1f}分 ({total_hours:.2f}小時)"
        print(line)
        report_lines.append(line)
        
        # Periodic save
        if len(time_cache) % 10 == 0:
            save_cache()

    # Final Save
    save_cache()
    
    # Write Report
    with open(OUTPUT_REPORT, 'w', encoding='utf-8') as f:
        f.write("\n".join(report_lines))
    
    print(f"Report generated: {OUTPUT_REPORT}")

if __name__ == "__main__":
    calculate_hours()
