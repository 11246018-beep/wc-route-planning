
import pandas as pd
from math import radians, cos, sin, asin, sqrt

# Load Coordinates
base_coords = {'PZ': (121.22683, 24.90679), 'WG': (121.44141, 25.07055)}

STAFF_TEAM_MAP = {
    'S01': 'PZ', 'S02': 'PZ', 'S03': 'PZ', 'S04': 'PZ',
    'S05': 'PZ', 'S06': 'PZ', 'S07': 'PZ', 'S08': 'PZ', 'S09': 'PZ', 'S10': 'PZ',
    'S11': 'PZ', 'S12': 'PZ',
    'S13': 'WG', 'S14': 'WG'
}

def haversine(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1 
    dlat = lat2 - lat1 
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a)) 
    r = 6371 
    return c * r

def estimate_travel_time(km):
    if km < 0.1: return 2
    hours = km / 25
    mins = hours * 60
    return max(mins, 3)

df = pd.read_excel('Weekly_Schedule.xlsx')
print(f"Columns: {df.columns}")

errors = 0

# Check Per Staff Per Day
grouped = df.groupby(['員工代號', '日程(Day)'])

for (staff, day), group in grouped:
    group = group.sort_values('順序')
    
    # 1. Check First Task Travel Time
    first_task = group.iloc[0]
    
    # Get Task Coords (Need to lookup from original file? 
    # Or just rely on Travel Time being present and check consistency if I had coords?
    # The output doesn't have Lat/Lon? 
    # Wait, the output unfortunately dropped Lat/Lon.
    # I should have kept them for verification.
    # However, I can check the Total Time Limit Constraint logic.
    
    last_task = group.iloc[-1]
    total_time_log = last_task['累計工時(分)']
    
    if total_time_log > 540:
        print(f"[FAIL] Staff {staff} Day {day}: Total Time {total_time_log} > 540")
        errors += 1
        
    # Ideally I should verify the buffer. 
    # But without Lat/Lon in output, I can't calculate return distance easily here.
    # But I can check if the script *allowed* the last task.
    # If the logic worked, (Total_Time + Return) <= 540.
    # So Total_Time <= 540 is a necessary condition, but not sufficient.
    # Sufficient would be Total_Time <= 540 - Return_Time.
    
    # Let's inspect S13 Day 1 (Wugu) specifically if possible.
    pass

if errors == 0:
    print("Basic constraints passed (Total Time <= 540).")
    
# To properly verify first travel time, I need Lat/Lon.
# I'll check if I can modify generate_schedule to output Lat/Lon or just trust the logic if basic checks pass.
# Or better, just inspect the logs?
