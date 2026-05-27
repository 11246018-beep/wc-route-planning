
import pandas as pd
from math import radians, cos, sin, asin, sqrt

# Config
base_coords = {'PZ': (121.22683, 24.90679), 'WG': (121.44141, 25.07055)}
STAFF_TEAM_MAP = {
    'S01': 'PZ', 'S02': 'PZ', 'S03': 'PZ', 'S04': 'PZ',
    'S05': 'PZ', 'S06': 'PZ', 'S07': 'PZ', 'S08': 'PZ', 'S09': 'PZ', 'S10': 'PZ',
    'S11': 'PZ', 'S12': 'PZ',
    'S13': 'WG', 'S14': 'WG'
}

def haversine(lon1, lat1, lon2, lat2):
    try:
        lon1, lat1, lon2, lat2 = map(radians, [float(lon1), float(lat1), float(lon2), float(lat2)])
        dlon = lon2 - lon1 
        dlat = lat2 - lat1 
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a)) 
        return c * 6371 
    except:
        return 0

def estimate_travel_time(km):
    if km < 0.1: return 2
    speed = 25
    if km > 10: speed = 60
    hours = km / speed
    return max(hours * 60, 3)

# Load Schedule
# Note: The output excel doesn't have Lat/Lon columns unless we added them?
# Verify columns first.
df = pd.read_excel('Weekly_Schedule.xlsx')

# If Lat/Lon missing in output, we need to merge back from maintenance_data?
# Or we can rely on `generate_schedule.py` internal state?
# Actually, I can just load the raw data again... or quicker:
# Just modify `generate_schedule.py` to dump the lat/lon?
# No, let's just try to map by address or ID if needed, OR:
# Wait, `generate_schedule.py` calculated the schedule.
# It verified constraints.
# I want to sum the `cumulative time` + `return time`.
# But I don't know the return distance without coordinates.

# Solution: Load task master again to get coordinates for the LAST task of each day.
print("Loading Task Master for Coordinates...")
all_tasks = []
# Load from backup to be sure
raw_file = 'maintenance_data_v2_backup.xlsx'
# We need to build a lookup dict: Address -> Lat, Lon
addr_map = {}

for sheet in ['平鎮', '五股', '平鎮週清2', '五股週清2']:
    try:
        d = pd.read_excel(raw_file, sheet_name=sheet)
        # Find cols
        addr_col = next((c for c in d.columns if '地址' in str(c)), None)
        lat_col = next((c for c in d.columns if '緯度' in str(c)), None)
        lon_col = next((c for c in d.columns if '經度' in str(c)), None)
        
        if addr_col and lat_col and lon_col:
            d = d.dropna(subset=[lat_col, lon_col])
            for _, r in d.iterrows():
                addr_map[str(r[addr_col])] = (r[lon_col], r[lat_col])
    except: pass

print(f"Loaded {len(addr_map)} locations.")

# Calculate Weekly Hours
staff_stats = {}

grouped = df.groupby(['員工代號', '日程(Day)'])

for (staff, day), group in grouped:
    # Get last task
    last_task = group.sort_values('順序').iloc[-1]
    
    # Cumulative time at end of last task
    end_time = last_task['累計工時(分)']
    
    # Calculate Return Trip
    addr = str(last_task['地址'])
    if addr in addr_map:
        lon, lat = addr_map[addr]
        base_lon, base_lat = base_coords[STAFF_TEAM_MAP[staff]]
        dist = haversine(lon, lat, base_lon, base_lat)
        return_time = estimate_travel_time(dist)
    else:
        # Fallback if address mismatch (should be rare)
        print(f"Warning: Address not found {addr}")
        return_time = 30 # Default penalty
        
    total_day_time = end_time + return_time
    
    if staff not in staff_stats: staff_stats[staff] = 0
    staff_stats[staff] += total_day_time

# Convert to DataFrame
res = []
for s, mins in staff_stats.items():
    res.append({'Staff': s, 'Weekly_Mins': mins, 'Weekly_Hours': mins/60})
    
df_res = pd.DataFrame(res).sort_values('Staff')
print(df_res)
print("\n--- Summary ---")
print(df_res['Weekly_Hours'].describe())

# Calculate Gap
min_h = df_res['Weekly_Hours'].min()
max_h = df_res['Weekly_Hours'].max()
diff_percent = (max_h - min_h) / max_h
print(f"\nMax Diff: {max_h - min_h:.1f} hours ({diff_percent:.1%})")
