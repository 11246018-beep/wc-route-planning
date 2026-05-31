import pandas as pd
import requests
import json
import os
import time

# Configuration
INPUT_FILE = 'Weekly_Schedule.xlsx'
OUTPUT_REPORT = 'return_trip_report.txt'
CACHE_FILE = 'osrm_cache.json'
OSRM_BASE_URL = 'http://router.project-osrm.org/route/v1/driving'

# Base Coordinates
# PZ: Pingzhen, WG: Wugu
BASE_COORDS = {'PZ': (24.90679, 121.22683), 'WG': (25.07055, 121.44141)}
STAFF_TEAM_MAP = {
    'S01': 'PZ', 'S02': 'PZ', 'S03': 'PZ', 'S04': 'PZ', 'S05': 'PZ', 'S06': 'PZ', 'S07': 'PZ', 
    'S08': 'PZ', 'S09': 'PZ', 'S10': 'PZ', 'S11': 'PZ', 'S12': 'PZ', 'S13': 'WG', 'S14': 'WG'
}

print("Script starting...", flush=True)

# Cache Handling
osrm_cache = {}

def load_cache():
    global osrm_cache
    if os.path.exists(CACHE_FILE):
        print(f"Loading cache from {CACHE_FILE}...", flush=True)
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                osrm_cache = json.load(f)
            print(f"Loaded {len(osrm_cache)} entries from cache.", flush=True)
        except Exception as e:
            print(f"Error loading cache: {e}", flush=True)

def save_cache():
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(osrm_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving cache: {e}", flush=True)

def get_osrm_duration(start_lat, start_lon, end_lat, end_lon):
    """
    Returns duration in minutes between two points using OSRM API.
    Uses caching.
    """
    # Round coordinates to improve cache hit rate
    r_start_lat, r_start_lon = round(start_lat, 4), round(start_lon, 4)
    r_end_lat, r_end_lon = round(end_lat, 4), round(end_lon, 4)

    # Create key
    key = f"{r_start_lat},{r_start_lon}|{r_end_lat},{r_end_lon}"
    
    if key in osrm_cache:
        return osrm_cache[key]
    
    try:
        # OSRM expects lon,lat
        url = f"{OSRM_BASE_URL}/{r_start_lon},{r_start_lat};{r_end_lon},{r_end_lat}?overview=false"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if 'routes' in data and len(data['routes']) > 0:
                duration_sec = data['routes'][0]['duration']
                duration_mins = duration_sec / 60.0
                
                # Update cache
                osrm_cache[key] = duration_mins
                return duration_mins
            else:
                print(f"No route found for {key}", flush=True)
                return 0
        else:
            print(f"OSRM API Error {response.status_code} for {key}", flush=True)
            return 0
            
    except Exception as e:
        print(f"Request Error for {key}: {e}", flush=True)
        return 0

def calculate_return_trips():
    load_cache()
    
    print("Loading schedule...", flush=True)
    try:
        df = pd.read_excel(INPUT_FILE)
    except Exception as e:
        print(f"Error loading {INPUT_FILE}: {e}", flush=True)
        return

    print("Calculating return trips (Last station -> Base)...", flush=True)
    
    report_lines = []
    report_lines.append("每日返程車程表 (OSRM 計算)")
    report_lines.append("=============================================")
    report_lines.append("說明：計算從當日最後一個站點返回倉庫的預估行車時間")
    report_lines.append("---------------------------------------------")
    
    # Group by Staff and Day
    grouped = df.groupby(['員工代號', '日程(Day)'])
    sorted_keys = sorted(grouped.groups.keys())
    
    print(f"Processing {len(sorted_keys)} staff-day groups...", flush=True)
    
    count = 0
    for (staff_id, day) in sorted_keys:
        group = grouped.get_group((staff_id, day)).sort_values('順序')
        
        # Determine Base
        team = STAFF_TEAM_MAP.get(staff_id, 'PZ')
        base_lat, base_lon = BASE_COORDS[team]
        
        if len(group) == 0:
            continue
            
        # Get Last Station
        last_row = group.iloc[-1]
        last_lat = last_row['緯度']
        last_lon = last_row['經度']
        last_addr = last_row['地址']
        
        # Calculate Duration
        duration_mins = get_osrm_duration(last_lat, last_lon, base_lat, base_lon)
        
        # Formatting
        line = f"員工: {staff_id} | Day: {day} | 最後地點: {last_addr} | 返回倉庫: {duration_mins:.1f}分"
        print(line, flush=True)
        report_lines.append(line)
        
        count += 1
        if count % 5 == 0:
            save_cache()

    # Final Save
    save_cache()
    
    # Write Report
    with open(OUTPUT_REPORT, 'w', encoding='utf-8') as f:
        f.write("\n".join(report_lines))
    
    print(f"Report generated: {OUTPUT_REPORT}", flush=True)

if __name__ == "__main__":
    calculate_return_trips()
