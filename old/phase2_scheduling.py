
import pandas as pd
import numpy as np
import requests
import json
import time
import math
from collections import defaultdict
import random

INPUT_FILE = r'c:/Users/owner/Desktop/專題/test4.0/phase1/phase2_clustered.csv'
OUTPUT_JSON = r'c:/Users/owner/Desktop/專題/test4.0/phase1/schedule_output.json'

MAX_DAILY_MIN = 540
HQ_PZ = {'lat': 24.90703, 'lon': 121.226872, 'id': 'PZ'}
HQ_WG = {'lat': 25.07154, 'lon': 121.44169, 'id': 'WG'}

OSRM_URL = "http://router.project-osrm.org/route/v1/driving/"

def get_osrm_trip(coords):
    """
    Get OSRM route for a sequence of points.
    coords: List of (lon, lat).
    Assume order is fixed (or use 'trip' service for optimizing? User said strict OSRM calc).
    We will use 'route' service on a sorted sequence (Nearest Neighbor).
    """
    if len(coords) < 2:
        return 0, 0, None
        
    coord_str = ";".join([f"{lon:.5f},{lat:.5f}" for lon, lat in coords])
    url = f"{OSRM_URL}{coord_str}?overview=full&steps=true&geometries=geojson"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data['code'] == 'Ok':
            r = data['routes'][0]
            return r['distance'], r['duration'], r['geometry']
    except Exception as e:
        print(f"OSRM Error: {e}")
        time.sleep(1)
    return 0, 0, None

def solve_tsp_nn(start_node, points):
    """
    Nearest Neighbor Heuristic for TSP.
    start_node: (lat, lon)
    points: list of dict {'lat', 'lon', 'id', ...}
    Returns: ordered list of points
    """
    if not points:
        return []
    
    unfinished = points.copy()
    current = start_node
    path = []
    
    while unfinished:
        # Find closest
        best_dist = float('inf')
        best_idx = -1
        
        for i, p in enumerate(unfinished):
            d = (current[0]-p['lat'])**2 + (current[1]-p['lon'])**2
            if d < best_dist:
                best_dist = d
                best_idx = i
        
        # Move
        next_p = unfinished.pop(best_idx)
        path.append(next_p)
        current = (next_p['lat'], next_p['lon'])
        
    return path

def schedule_driver(driver_id, cluster_df):
    print(f"Scheduling Driver {driver_id} ({len(cluster_df)} points)...")
    
    # 1. Identify Depot
    # Mode of DepotID in cluster? Or pre-assigned?
    # Majority vote
    depot_id = cluster_df['DepotID'].mode()[0]
    hq = HQ_PZ if depot_id == 'PZ' else HQ_WG
    hq_coords = (hq['lat'], hq['lon'])
    
    # 2. Expand W2 points
    # Create "Tasks"
    tasks = []
    
    for idx, row in cluster_df.iterrows():
        p = row.to_dict()
        freq = 2 if row['Type'] == 'W2' else 1
        
        for i in range(freq):
            tasks.append({
                'point': p,
                'visit_idx': i, # 0 or 1
                'county': row['County'],
                'service_time': row['Service_Time'],
                'lat': row['Lat'],
                'lon': row['Lon'],
                'type': row['Type'],
                'pid': row['PointID']
            })
            
    # 3. Group by County
    county_tasks = defaultdict(list)
    for t in tasks:
        county_tasks[t['county']].append(t)
        
    # 4. Create "Day Batches"
    # We have 6 days.
    # We must assign each County-Batch to days.
    # W2 constraint: P_visit0 and P_visit1 must be on different days.
    
    # Heuristic:
    # Separate W2-pair tasks?
    # This is complex Bin Packing.
    # Simplified approach:
    #  - Construct "Day candidates" for each county.
    #  - If County A has W2 points, we MUST split County A into at least 2 Days.
    #  - Sort Counties by "Must Split" vs "Single Day".
    
    # Organize tasks:
    #  Dict: PointID -> [Task1, Task2] (if W2)
    
    day_assignments = {d: {'county': None, 'tasks': []} for d in range(1, 7)}
    
    # Priority 1: W2 Points.
    # Sort counties by number of W2 points.
    
    counties_sorted = sorted(county_tasks.keys(), 
                             key=lambda c: sum(1 for t in county_tasks[c] if t['type'] == 'W2'), 
                             reverse=True)
                             
    for county in counties_sorted:
        c_tasks = county_tasks[county]
        w2_points = {t['pid'] for t in c_tasks if t['type'] == 'W2'}
        
        # If W2 points exist, we need at least 2 days for this county IF load allows?
        # No, strict rule: "W2... different days".
        # So if we have ANY W2 point in this county, we MUST schedule this county on >= 2 days.
        # Unless the W2 point visits are in DIFFERENT counties?
        # Point P is in County A. So both visits are in County A.
        # So YES: County A must be visited on >= 2 days.
        
        needed_days = 2 if w2_points else 1
        
        # Check Total Load
        total_service = sum(t['service_time'] for t in c_tasks)
        # Estimate Travel: 20 min base + 3 min per task?
        total_est = total_service + 20 + len(c_tasks)*3
        
        # Calculate max tasks per day
        # Split c_tasks into N groups
        # N = max(needed_days, ceil(total_est / 540))
        num_splits = max(needed_days, math.ceil(total_est / 500)) # conservative
        
        if num_splits > 6:
            print(f"WARNING: Driver {driver_id} needs {num_splits} days for {county}. Cap at 6.")
            num_splits = 6
            
        # Distribute tasks into num_splits groups
        # Ensure pairs are separated
        groups = [[] for _ in range(num_splits)]
        
        # Pair handling
        processed_pids = set()
        for t in c_tasks:
            pid = t['pid']
            if pid in processed_pids:
                continue
                
            if t['type'] == 'W2':
                # Find both visits
                pair = [x for x in c_tasks if x['pid'] == pid]
                if len(pair) == 2:
                    # Assign to group k and group (k + 1)%N ?
                    # Or random distinct groups
                    g1 = 0
                    g2 = 1 % num_splits
                    # Better distribution: round robin
                    # TODO: Implement better load balancing logic within county splits
                    # For now: Just ensure distinct
                    groups[0].append(pair[0])
                    if num_splits > 1:
                        groups[1].append(pair[1])
                    else:
                        # Impossible? If W2 exists, num_splits is set to 2.
                        # Unless cap logic hit.
                        groups[0].append(pair[1]) 
                else:
                    groups[0].append(t)
                processed_pids.add(pid)
            else:
                # W1
                # Add to smallest group
                # Simple round robin for now
                target_g = len(processed_pids) % num_splits
                groups[target_g].append(t)
                processed_pids.add(pid)
                
        # Assign Groups to Days
        # Find empty days first
        # Constraint: All groups for this county must go to days?
        # Yes.
        
        assigned_indices = []
        for g_idx, g_tasks in enumerate(groups):
             # Find best day
             # Prefer empty day
             best_day = -1
             for d in range(1, 7):
                 if d in assigned_indices: continue # Don't assign multiple groups of same county to same day? (Combine them allowed?)
                 # If combine allowed, we merge. But W2 separation?
                 # If we merge G1 and G2 to Day 1, then P1 and P2 might be on Day 1. BAD.
                 # So we keep them distinct days.
                 
                 if day_assignments[d]['county'] is None:
                     best_day = d
                     break
             
             if best_day == -1:
                 # No empty day. Look for day with SAME county?
                 # If we merge, we violate W2 constraint if P1 in D1 and P2 in D1.
                 # Optimization: Only merge if no conflict.
                 # For now, put in overflow if no days.
                 print(f"  Overflow for County {county} Group {g_idx}")
                 continue
                 
             day_assignments[best_day]['county'] = county
             day_assignments[best_day]['tasks'].extend(g_tasks)
             assigned_indices.append(best_day)
             
    # 5. Route & Validate per Day
    final_schedule = []
    
    for d in range(1, 7):
        day_tasks = day_assignments[d]['tasks']
        if not day_tasks:
            continue
            
        # Optimize Route (TSP)
        points_data = [t['point'] for t in day_tasks] # raw data
        # Fix format for TSP
        tsp_points = []
        for t in day_tasks:
            p = t['point'].copy()
            # Rename for TSP function
            p['lat'] = t['lat']
            p['lon'] = t['lon']
            tsp_points.append(p)
            
        ordered_points = solve_tsp_nn(hq_coords, tsp_points)
        
        # OSRM
        # Path: HQ -> P1 -> P2 ... -> Pn -> HQ
        route_coords = [(hq['lon'], hq['lat'])] + [(p['lon'], p['lat']) for p in ordered_points] + [(hq['lon'], hq['lat'])]
        
        dist_m, dur_s, geometry = get_osrm_trip(route_coords)
        
        # Calculate Total Time
        # Dur_s is driving time.
        # Service time sum
        service_min = sum(t['service_time'] for t in day_tasks)
        drive_min = dur_s / 60
        total_min = service_min + drive_min
        
        # Check constraint
        status = 'OK'
        if total_min > MAX_DAILY_MIN:
            status = 'OVERLOAD'
            print(f"  Day {d}: {total_min:.1f} min (> {MAX_DAILY_MIN}) !!")
            
        # Construct Record
        for seq, p in enumerate(ordered_points):
            final_schedule.append({
                'DriverID': int(driver_id),
                'Depot': depot_id,
                'Day': int(d),
                'Sequence': int(seq + 1),
                'PointID': int(p['PointID']), 
                'ClientID': str(p['ClientID']),
                'Address': str(p['Address_Raw']),
                'Week1': float(p['Week1_Flag']) if pd.notnull(p['Week1_Flag']) else None,
                'Week2': float(p['Week2_Flag']) if pd.notnull(p['Week2_Flag']) else None,
                'Service_Time': float(p['Service_Time']),
                'Lat': float(p['Lat']),
                'Lon': float(p['Lon']),
                'Driving_Time_Sec': float(dur_s),
                'Distance_Meters': float(dist_m),
                'Route_Geometry': geometry, 
                'Total_Daily_Time': float(total_min)
            })
            
    return final_schedule

def main():
    try:
        df = pd.read_csv(INPUT_FILE)
    except:
        print("No input file.")
        return

    all_driver_schedules = []
    
    # Process each driver
    for driver_id in sorted(df['Cluster'].unique()):
        cluster_df = df[df['Cluster'] == driver_id]
        schedule = schedule_driver(driver_id, cluster_df)
        all_driver_schedules.extend(schedule)
        
    # Save
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        # Custom encoder just in case
        def np_encoder(object):
            if isinstance(object, np.generic):
                return object.item()
            raise TypeError
            
        json.dump(all_driver_schedules, f, ensure_ascii=False, indent=2, default=np_encoder)
        
    print(f"Schedule generated. Total records: {len(all_driver_schedules)}")

if __name__ == "__main__":
    main()
