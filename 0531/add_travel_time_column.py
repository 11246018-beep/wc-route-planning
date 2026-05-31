import pandas as pd
import requests
import json
import os
import time

# Configuration
INPUT_FILE = 'Weekly_Schedule.xlsx'
OUTPUT_FILE = 'Weekly_Schedule_Updated.xlsx'
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
    r_start_lat, r_start_lon = round(start_lat, 4), round(start_lon, 4)
    r_end_lat, r_end_lon = round(end_lat, 4), round(end_lon, 4)

    key = f"{r_start_lat},{r_start_lon}|{r_end_lat},{r_end_lon}"
    
    if key in osrm_cache:
        return osrm_cache[key]
    
    try:
        url = f"{OSRM_BASE_URL}/{r_start_lon},{r_start_lat};{r_end_lon},{r_end_lat}?overview=false"
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if 'routes' in data and len(data['routes']) > 0:
                duration_sec = data['routes'][0]['duration']
                duration_mins = duration_sec / 60.0
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

def add_travel_time_column():
    load_cache()
    
    print("Loading schedule...", flush=True)
    try:
        df = pd.read_excel(INPUT_FILE)
    except Exception as e:
        print(f"Error loading {INPUT_FILE}: {e}", flush=True)
        return

    # Ensure we work on a copy to avoid SettingWithCopy warnings if slicing
    df = df.copy()

    # Create new column
    df['抵達所需時間'] = 0.0
    
    # Sort to ensure order: Staff -> Day -> Sequence
    # Assuming columns are: '員工代號', '日程(Day)', '順序'
    df = df.sort_values(by=['員工代號', '日程(Day)', '順序'])
    
    grouped = df.groupby(['員工代號', '日程(Day)'])
    sorted_keys = sorted(grouped.groups.keys())
    
    print(f"Processing {len(sorted_keys)} staff-day groups...", flush=True)
    
    count = 0
    
    # We will build a list of indices and values to update efficiently, or update row by row
    # Updating row by row in pandas using iterrows is slow but fine for small datasets.
    # A better way is to iterate groups and use indices.
    
    for (staff_id, day) in sorted_keys:
        group_indices = grouped.get_group((staff_id, day)).sort_values('順序').index
        
        team = STAFF_TEAM_MAP.get(staff_id, 'PZ')
        base_lat, base_lon = BASE_COORDS[team]
        
        prev_lat, prev_lon = base_lat, base_lon
        
        for idx in group_indices:
            curr_lat = df.at[idx, '緯度']
            curr_lon = df.at[idx, '經度']
            
            # Calculate duration from Prev -> Current
            duration = get_osrm_duration(prev_lat, prev_lon, curr_lat, curr_lon)
            
            # Update DataFrame
            df.at[idx, '抵達所需時間'] = round(duration, 1)
            
            # Update Prev
            prev_lat, prev_lon = curr_lat, curr_lon
        
        count += 1
        if count % 10 == 0:
            save_cache()
            print(f"Processed {count} groups...", flush=True)
            
    save_cache()
    
    print(f"Saving to {OUTPUT_FILE}...", flush=True)
    df.to_excel(OUTPUT_FILE, index=False)
    print("Done.", flush=True)

if __name__ == "__main__":
    add_travel_time_column()
