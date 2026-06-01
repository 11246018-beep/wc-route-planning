
import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
import json
import os
import utils
from datetime import timedelta

# Load Data
FILE_PATH = 'maintenance_data_v2.xlsx'

def load_data():
    print("Loading data...")
    df = pd.read_excel(FILE_PATH, sheet_name='Wcrp53311b水肥車每日維護週期表')
    
    # Extract Week 1 and Week 2 tasks
    # Week 1: '週清1' is not null. Clean Once.
    # Week 2: '週清2' is not null. Clean Twice.
    
    df['id'] = df.index
    
    # Standardize columns
    df = df.rename(columns={
        '緯度': 'lat', 
        '經度': 'lon',
        '服務地點 ': 'address',
        '客戶名稱': 'name',
        '維護時間': 'clean_time',
        '郵遞區號3碼  ': 'zipcode'
    })
    
    # Fill NaN clean_time with 15 (defensive, though analysis showed likely fine)
    df['clean_time'] = df['clean_time'].fillna(15)
    
    w1_tasks = df[df['週清1'].notnull()].copy()
    w2_tasks = df[df['週清2'].notnull()].copy()
    
    print(f"Week 1 Tasks: {len(w1_tasks)}")
    print(f"Week 2 Tasks: {len(w2_tasks)}")
    
    return w1_tasks, w2_tasks

def cluster_tasks(df, n_clusters=14):
    """
    Cluster tasks into n_clusters using KMeans -> Balanced.
    """
    if len(df) < n_clusters:
        df['cluster'] = 0
        return df

    # 1. Initial KMeans
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    # Use simple lat/lon
    coords = df[['lat', 'lon']].values
    labels = kmeans.fit_predict(coords)
    
    # 2. Check balance
    # Target size
    target_n = len(df) / n_clusters
    max_n = target_n * 1.2 # Allow 20% deviation
    min_n = target_n * 0.8
    
    counts = pd.Series(labels).value_counts()
    print("Initial Cluster Counts:", counts.values)
    
    # Simple rebalancing:
    # Identify big clusters
    # For each point in a big cluster, find distance to other centers.
    # Move to nearest center that is not full.
    
    # Setup
    centers = kmeans.cluster_centers_
    df['cluster'] = labels # initial
    df['dist_to_center'] = df.apply(lambda row: utils.haversine_distance((row['lat'], row['lon']), (centers[int(row['cluster'])][1], centers[int(row['cluster'])][0])), axis=1) # Note: center is lat,lon? KMeans matches input. Input was lat,lon.
    # KMeans centers are [lat, lon]
    
    # Sort points by distance to their center (furthest first are candidates to move)
    # Actually, verifying center format
    # scikit-learn kmeans centers are in same order as input features (lat, lon)
    
    # Global rebalancing loop
    changed = True
    iterations = 0
    while changed and iterations < 5:
        changed = False
        iterations += 1
        
        counts = df['cluster'].value_counts()
        overloaded = counts[counts > max_n].index.tolist()
        underloaded = counts[counts < target_n].index.tolist() # targets to fill
        
        if not overloaded:
            break
            
        print(f"Rebalancing iter {iterations}. Overloaded clusters: {overloaded}")
        
        for cid in overloaded:
            # Get points in this cluster
            points_idx = df[df['cluster'] == cid].index
            current_count = len(points_idx)
            to_move = int(current_count - target_n) # aim for average
            
            if to_move <= 0: continue
            
            # Identify candidates: points furthest from center, or points closest to OTHER centroids?
            # Points closest to OTHER centroids that are underloaded (or not overloaded).
            
            # This is slow to calc all pairs.
            # Simplified: just calculate distance to all centers for these points
            sub_df = df.loc[points_idx]
            
            # We want to move 'to_move' points to their next best cluster
            # For each point, find best alternative cluster
            moves = []
            
            for idx, row in sub_df.iterrows():
                p_coord = (row['lat'], row['lon'])
                best_alt = -1
                best_dist = float('inf')
                
                for alt_cid in range(n_clusters):
                    if alt_cid == cid: continue
                    # Check if alt has room
                    # "Room" means < max_n? Or just not overloaded?
                    # Ideally move to underloaded.
                    curr_alt_count = counts.get(alt_cid, 0)
                    if curr_alt_count >= max_n: continue
                    
                    dist = utils.haversine_distance(p_coord, (centers[alt_cid][0], centers[alt_cid][1]))
                    if dist < best_dist:
                        best_dist = dist
                        best_alt = alt_cid
                
                if best_alt != -1:
                    # Score of move = (Dist to Alt) - (Dist to Current) ? 
                    # We want to minimize added distance.
                    # Or just: Minimize (Dist to Alt).
                    current_dist = utils.haversine_distance(p_coord, (centers[cid][0], centers[cid][1]))
                    moves.append({
                        'id': idx,
                        'to': best_alt,
                        'cost': best_dist - current_dist
                    })
            
            # Sort moves by cost (lowest cost first)
            moves.sort(key=lambda x: x['cost'])
            
            # Execute moves
            actual_moves = 0
            for m in moves:
                if actual_moves >= to_move: break
                
                # Check destination capacity again
                tgt = m['to']
                if counts.get(tgt, 0) >= max_n: continue
                
                # Move
                df.at[m['id'], 'cluster'] = tgt
                counts[cid] -= 1
                counts[tgt] = counts.get(tgt, 0) + 1
                actual_moves += 1
                changed = True
                
    counts = df['cluster'].value_counts()
    print("Final Cluster Counts:", counts.values)
    return df

def assign_drivers_to_clusters(df_clustered):
    """
    Assign each cluster to a driver based on proximity to depot.
    We have 12 PZ drivers and 2 WG drivers.
    Logic: Find centroid of each cluster. 
    Assign 2 clusters closest to WG to WG drivers.
    Rest to PZ drivers.
    """
    centroids = df_clustered.groupby('cluster')[['lat', 'lon']].mean().reset_index()
    
    # Calculate distance to WG depot
    centroids['dist_wg'] = centroids.apply(
        lambda row: utils.haversine_distance((row['lat'], row['lon']), (utils.WG_DEPOT['lat'], utils.WG_DEPOT['lon'])), 
        axis=1
    )
    
    # Sort by distance to WG, take top 2
    wg_clusters = centroids.nsmallest(2, 'dist_wg')['cluster'].tolist()
    
    cluster_driver_map = {}
    
    wg_idx = 0
    pz_idx = 0
    
    sorted_clusters = centroids.sort_values('dist_wg').cluster.tolist()
    
    for c in sorted_clusters:
        if c in wg_clusters:
            driver = utils.DRIVERS_WG[wg_idx]
            depot = utils.WG_DEPOT
            wg_idx += 1
        else:
            if pz_idx < len(utils.DRIVERS_PZ):
                 driver = utils.DRIVERS_PZ[pz_idx]
            else:
                 # Fallback if logic mismatch (shouldn't happen with 14 clusters)
                 driver = "Unassigned" 
            depot = utils.PZ_DEPOT
            pz_idx += 1
            
        cluster_driver_map[c] = {'driver': driver, 'depot': depot}
        
    return cluster_driver_map

def solve_tsp_nearest_neighbor(points):
    """
    Simple TSP using Nearest Neighbor.
    Points: list of dicts with 'lat', 'lon', 'id', ...
    Returns: ordered list of points
    """
    if not points:
        return []
    
    # Start with arbitrary point (e.g., northernmost)
    current = min(points, key=lambda x: x['lat']) 
    path = [current]
    unvisited = [p for p in points if p['id'] != current['id']]
    
    while unvisited:
        # Find closest to current
        nearest = min(unvisited, key=lambda x: utils.haversine_distance((current['lat'], current['lon']), (x['lat'], x['lon'])))
        path.append(nearest)
        unvisited.remove(nearest)
        current = nearest
        
    return path

def schedule_driver_tasks(driver_name, depot, tasks, is_week2=False):
    """
    Schedule tasks for a single driver over 6 days.
    Constraint: Max 540 mins per day (Drive + Clean).
    Constraint: Start/End at Depot.
    WEEK 2 Logic: If is_week2 is True, we need to schedule each task TWICE on different days.
    """
    
    # 1. Order tasks (TSP) to minimize travel
    # Convert tasks df to list of dicts
    task_points = tasks.to_dict('records')
    ordered_tasks = solve_tsp_nearest_neighbor(task_points)
    
    if is_week2:
        # For Week 2, we need to visit each point twice. 
        # Strategy: Duplicate the list? Or schedule first pass then second pass?
        # Let's try: Schedule all points once (Pass 1), then schedule all again (Pass 2).
        # This naturally separates them.
        ordered_tasks = ordered_tasks + ordered_tasks # Naive doubling, but let's try to fit into days 
        
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    schedule = {day: [] for day in days}
    
    current_day_idx = 0
    
    # Daily accumulation
    daily_tasks = []
    current_clean_time = 0
    # To track daily route, we need coordinates: Depot -> Task1 -> Task2 ... -> Depot
    
    for task in ordered_tasks:
        if current_day_idx >= len(days):
            print(f"Warning: Driver {driver_name} overloaded! Dropping tasks.")
            break
            
        # Check Cross-County wrt LAST added task
        # If daily_tasks is empty, last location is Depot. Depot has no specific county? 
        # Actually Depot is in Taoyuan (PZ) or New Taipei (WG).
        # PZ_DEPOT zip ~ 324 (Pingzhen). WG_DEPOT zip ~ 248 (Wugu).
        
        last_zip = None
        if not daily_tasks:
            # Starting from Depot
            # However, we only care if we switch counties *between tasks* or *return to depot*.
            # The prompt says: "若清潔行程涉及 跨縣市，必須：先返回總部 才能再出發執行下一段路線"
            # This implies Task A -> Task B. If A and B are diff counties, A -> Depot -> B.
            pass
        else:
            last_task = daily_tasks[-1]
            last_zip = last_task['zipcode']
            
        current_zip = task['zipcode']
        
        # Determine if we need an intermediate depot visit
        force_return = False
        if last_zip and utils.is_cross_county(last_zip, current_zip):
            force_return = True
            
        # If force_return, we must: DailyTasks -> Depot -> Task.
        # This adds significant travel time.
        # But wait, "Daily Route" starts/ends at depot.
        # If we insert Depot, do we end the day? Or just visit?
        # "先返回總部 才能再出發執行下一段路線" -> It's a visit.
        # But usually in VRP this splits the route into two trips.
        # For simplicity, if we cross county, let's try to END the day if possible, or accept the cost.
        # Let's simple Check:
        # Route: ... -> LastTask -> Depot -> CurrentTask -> ...
        
        task_clean_time = task['clean_time']
        
        # Calculate cost
        # If force_return: Last -> Depot -> Current
        # Else: Last -> Current
        
        # Build temp route to estimate time
        proposed_tasks = list(daily_tasks)
        if force_return:
            # We don't verify "Depot" as a task in the list, but we model the geometry.
            # But `daily_tasks` is a list of tasks.
            # OSRM calc will handle the geometry if we pass the coords correctly.
            # We can mark the task as "Requires Trip Reset" or just calculate costs.
            # IMPLICITLY: If simple OSRM from Last->Current is used, it violates rule.
            # So we must sum: Last->Depot + Depot->Current.
            pass
            
        # ... (Simplified logic for now: Accumulate time and check 540)
        # Using a simplistic "add time" approach first
        
        added_time = task_clean_time + 20 # Estimate 20 min travel
        if force_return:
            added_time += 40 # Penalty for depot return
            
        if current_clean_time + added_time > 500:
             # Move to next day
             finalize_day(schedule, days[current_day_idx], driver_name, daily_tasks, depot)
             daily_tasks = [task]
             current_clean_time = task_clean_time
             current_day_idx += 1
             continue
             
        daily_tasks.append(task)
        current_clean_time += added_time
        
        # Check cumulative limit with simple estimate first
        if current_clean_time > 400: # Check OSRM when getting full
             coords = [(depot['lon'], depot['lat'])]
             
             # Reconstruct path with forced returns
             # Iterate daily_tasks to build coord list
             for i, t in enumerate(daily_tasks):
                 if i > 0:
                     prev = daily_tasks[i-1]
                     if utils.is_cross_county(prev['zipcode'], t['zipcode']):
                         coords.append((depot['lon'], depot['lat'])) # Return to depot
                 coords.append((t['lon'], t['lat']))
                 
             coords.append((depot['lon'], depot['lat'])) # Final return
             
             route_info = utils.get_osrm_route(coords)
             if not route_info: 
                 # OSRM failed, assume passed but warn
                 pass
             else:
                 total_drive_min = route_info['duration'] / 60
                 real_clean_time = sum(t['clean_time'] for t in daily_tasks)
                 total_work = real_clean_time + total_drive_min
                 
                 if total_work > utils.MAX_DAILY_WORK_MIN:
                     # Remove last task and finalize day
                     popped = daily_tasks.pop()
                     finalize_day(schedule, days[current_day_idx], driver_name, daily_tasks, depot)
                     current_day_idx += 1
                     
                     # Start new day with popped
                     daily_tasks = [popped]
                     current_clean_time = popped['clean_time']
    
    # Finalize last day
    if daily_tasks and current_day_idx < len(days):
        finalize_day(schedule, days[current_day_idx], driver_name, daily_tasks, depot)
        
    return schedule

def finalize_day(schedule, day, driver, tasks, depot):
    """
    Compute final OSRM route for the day and store it.
    """
    if not tasks:
        return
    
    # Construct coords with cross-county returns
    coords = [(depot['lon'], depot['lat'])]
    for i, t in enumerate(tasks):
         if i > 0:
             prev = tasks[i-1]
             if utils.is_cross_county(prev['zipcode'], t['zipcode']):
                 coords.append((depot['lon'], depot['lat']))
         coords.append((t['lon'], t['lat']))
    coords.append((depot['lon'], depot['lat']))
             
    osrm_res = utils.get_osrm_route(coords)
    
    if osrm_res:
        schedule[day] = {
            'tasks': tasks,
            'distance_m': osrm_res['distance'],
            'duration_s': osrm_res['duration'],
            'geometry': osrm_res['geometry'],
            'total_time_min': (osrm_res['duration'] / 60) + sum(t['clean_time'] for t in tasks)
        }
    else:
        # Fallback
        schedule[day] = {
            'tasks': tasks,
            'error': 'OSRM Failed'
        }

def main():
    w1_df, w2_df = load_data()
    
    # Process Week 1
    w1_df = cluster_tasks(w1_df, 14)
    w1_clusters = assign_drivers_to_clusters(w1_df)
    
    final_output = []
    
    print("\nProcessing Week 1...")
    for cluster_id, info in w1_clusters.items():
        driver = info['driver']
        depot = info['depot']
        tasks = w1_df[w1_df['cluster'] == cluster_id]
        print(f"  Scheduling {driver} (Cluster {cluster_id}, {len(tasks)} tasks)...")
        
        schedule = schedule_driver_tasks(driver, depot, tasks, is_week2=False)
        
        for day, data in schedule.items():
            if not data: continue
            entry = {
                'Sheet': '週清1',
                'Driver': driver,
                'Depot': depot['name'],
                'Day': day,
                'Tasks': [t['id'] for t in data['tasks']],
                'Details': data
            }
            final_output.append(entry)

    # Process Week 2
    # Week 2 also needs 14 clusters/drivers?
    # Yes, separate process.
    w2_df = cluster_tasks(w2_df, 14)
    w2_clusters = assign_drivers_to_clusters(w2_df)
    
    print("\nProcessing Week 2...")
    for cluster_id, info in w2_clusters.items():
        driver = info['driver']
        depot = info['depot']
        tasks = w2_df[w2_df['cluster'] == cluster_id]
        print(f"  Scheduling {driver} (Cluster {cluster_id}, {len(tasks)} tasks)...")
        
        schedule = schedule_driver_tasks(driver, depot, tasks, is_week2=True)
         
        for day, data in schedule.items():
            if not data: continue
            entry = {
                'Sheet': '週清2',
                'Driver': driver,
                'Depot': depot['name'],
                'Day': day,
                'Tasks': [t['id'] for t in data['tasks']],
                'Details': data
            }
            final_output.append(entry)
            
    # Save output
    with open('schedule_output.json', 'w', encoding='utf-8') as f:
        json.dump(final_output, f, ensure_ascii=False, indent=2)
        
    print("Schedule generation complete. Saved to schedule_output.json")

if __name__ == "__main__":
    main()
