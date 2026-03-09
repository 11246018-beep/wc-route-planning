import pandas as pd
import numpy as np
import folium
import re
import requests
import json
import os
import sqlite3
import math
import folium.plugins

class OSRMClient:
    def __init__(self, cache_file='osrm_cache.db'):
        self.conn = sqlite3.connect(cache_file)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS batch_cache
                               (hash TEXT PRIMARY KEY, duration REAL, distance REAL, geometry TEXT)''')
        self.conn.commit()

    def get_route_batch(self, coords):
        if len(coords) < 2: 
            return {'duration':0, 'distance':0, 'geometry':None}
        
        import hashlib
        c_str = "|".join([f"{round(lat,5)},{round(lon,5)}" for lat, lon in coords])
        c_hash = hashlib.md5(c_str.encode()).hexdigest()
        
        self.cursor.execute("SELECT duration, distance, geometry FROM batch_cache WHERE hash=?", (c_hash,))
        row = self.cursor.fetchone()
        if row:
            return {'duration': row[0], 'distance': row[1], 'geometry': json.loads(row[2])}
        
        coord_string = ";".join([f"{lon},{lat}" for lat, lon in coords])
        url = f"http://router.project-osrm.org/route/v1/driving/{coord_string}?overview=full&geometries=geojson"
        
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data['code'] == 'Ok':
                    route = data['routes'][0]
                    duration_min = route['duration'] / 60.0
                    distance_km = route['distance'] / 1000.0
                    geometry = route['geometry']
                    
                    self.cursor.execute("INSERT INTO batch_cache VALUES (?, ?, ?, ?)",
                                        (c_hash, duration_min, distance_km, json.dumps(geometry)))
                    self.conn.commit()
                    return {'duration': duration_min, 'distance': distance_km, 'geometry': geometry}
        except Exception as e:
            print(f"OSRM Batch Error: {e}")
            
        def haversine(lat1, lon1, lat2, lon2):
            R = 6371
            dLat = math.radians(lat2 - lat1)
            dLon = math.radians(lon2 - lon1)
            a = math.sin(dLat/2) * math.sin(dLat/2) + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon/2) * math.sin(dLon/2)
            return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

        dist_km = sum([haversine(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1]) for i in range(len(coords)-1)]) * 1.3
        duration_min = (dist_km / 40.0) * 60.0
        geom = {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in coords]}
        return {'duration': duration_min, 'distance': dist_km, 'geometry': geom}

def parse_county(addr):
    addr = str(addr).strip()
    pattern = r'(基隆市|台北市|臺北市|新北市|桃園市|桃園縣|新竹市|新竹縣|苗栗縣|台中市|臺中市|彰化縣|南投縣|雲林縣|嘉義市|嘉義縣|台南市|臺南市|高雄市|屏東縣|宜蘭縣|花蓮縣|台東縣|臺東縣|澎湖縣|金門縣|連江縣)'
    match = re.search(pattern, addr)
    if match: return match.group(1).replace('臺', '台')
    if len(addr) >= 3 and addr[2] in ['縣', '市']: return addr[:3].replace('臺', '台')
    return 'Unknown'

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat/2) * math.sin(dLat/2) + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon/2) * math.sin(dLon/2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def main():
    print("Loading data...")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(current_dir)
    input_path = os.path.join(project_dir, 'output', 'processed_nodes_phase1.csv')
    df = pd.read_csv(input_path)
    df['County'] = df['Address'].apply(parse_county)
    
    def get_depot(depot_str):
        if '五股' in str(depot_str): return 'Wugu'
        if '平鎮' in str(depot_str): return 'Pingzhen'
        return 'Unknown'
        
    df['Depot'] = df['Depot_Raw'].apply(get_depot)
    for idx, row in df.iterrows():
        if row['Depot'] == 'Unknown':
            if row['County'] in ['台北市', '新北市', '基隆市']:
                df.at[idx, 'Depot'] = 'Wugu'
            else:
                df.at[idx, 'Depot'] = 'Pingzhen'

    tasks = []
    task_id_counter = 1
    for _, row in df.iterrows():
        count = 1 if str(row['Freq']) != '2x' else 2
        
        # [NEW LOGIC] Split the service time for 2x tasks so it doesn't double count
        svc_time = row['Service_Time'] / 2.0 if str(row['Freq']) == '2x' else row['Service_Time']
        
        for i in range(count):
            tasks.append({
                'task_id': f"T{task_id_counter:05d}",
                'node_id': row['Node_ID'],
                'lat': row['Lat'],
                'lon': row['Lon'],
                'service_time': svc_time,
                'county': row['County'],
                'depot': row['Depot'],
                'address': row['Address'],
                'visit_idx': i + 1,
                'freq': str(row['Freq'])
            })
            task_id_counter += 1
            
    tasks_df = pd.DataFrame(tasks)
    print(f"Total tasks generated: {len(tasks_df)}")
    
    depot_locations = {
        'Wugu': {'lat': 25.07154, 'lon': 121.44169},
        'Pingzhen': {'lat': 24.90703, 'lon': 121.226872}
    }
    
    driver_config = {'Wugu': 2, 'Pingzhen': 12}
    osrm = OSRMClient()
    schedule = []
    
    tasks_pool = tasks_df.to_dict('records')
    for t in tasks_pool: t['assigned'] = False
        
    for depot, num_drivers in driver_config.items():
        depot_tasks = [t for t in tasks_pool if t['depot'] == depot]
        depot_lat, depot_lon = depot_locations[depot]['lat'], depot_locations[depot]['lon']
        
        for driver_id in range(1, num_drivers + 1):
            d_name = f"{depot[0]}{driver_id:02d}"
            
            for day in range(1, 7):
                valid_unassigned = []
                for t in tasks_pool:
                    if not t['assigned']:
                        node_visited_today = any(s['node_id'] == t['node_id'] and s['day'] == day for s in schedule)
                        if not node_visited_today:
                            valid_unassigned.append(t)
                            
                if not valid_unassigned: continue
                
                depot_unassigned = [t for t in valid_unassigned if t['depot'] == depot]
                if not depot_unassigned:
                    depot_unassigned = valid_unassigned
                    
                county_times = {}
                for t in depot_unassigned:
                    county_times[t['county']] = county_times.get(t['county'], 0) + t['service_time']
                target_county = max(county_times, key=county_times.get)
                valid_unassigned_county = [t for t in depot_unassigned if t['county'] == target_county]
                
                # Heuristic build route using Haversine
                current_route_tasks = []
                current_lat, current_lon = depot_lat, depot_lon
                
                est_day_total = 0
                while valid_unassigned_county:
                    def d(t): return (t['lat']-current_lat)**2 + (t['lon']-current_lon)**2
                    valid_unassigned_county.sort(key=d)
                    
                    cand = None
                    cand_idx = -1
                    best_est_time = 0
                    for idx, t_cand in enumerate(valid_unassigned_county):
                        # check if we already have this node in current_route_tasks
                        if not any(r['node_id'] == t_cand['node_id'] for r in current_route_tasks):
                            est_dist = haversine(current_lat, current_lon, t_cand['lat'], t_cand['lon']) * 1.3
                            est_time = (est_dist / 40.0) * 60.0
                            if est_day_total + est_time + t_cand['service_time'] <= 540:
                                cand = t_cand
                                cand_idx = idx
                                best_est_time = est_time
                                break
                            
                    if not cand:
                        break # no more valid candidates for today that fit
                    
                    current_route_tasks.append(cand)
                    est_day_total += best_est_time + cand['service_time']
                    current_lat, current_lon = cand['lat'], cand['lon']
                    valid_unassigned_county.pop(cand_idx)

                if not current_route_tasks: continue

                # 2-Opt Optimization for the constructed Haversine route to untangle loops/long-tails
                route_pts = [{'lat': depot_lat, 'lon': depot_lon}] + current_route_tasks
                improved = True
                while improved:
                    improved = False
                    for i in range(1, len(route_pts) - 1):
                        for j in range(i + 1, len(route_pts)):
                            if j - i == 1: continue
                            
                            cur_dist = haversine(route_pts[i-1]['lat'], route_pts[i-1]['lon'], route_pts[i]['lat'], route_pts[i]['lon']) + \
                                       haversine(route_pts[j-1]['lat'], route_pts[j-1]['lon'], route_pts[j]['lat'], route_pts[j]['lon'])
                            new_dist = haversine(route_pts[i-1]['lat'], route_pts[i-1]['lon'], route_pts[j-1]['lat'], route_pts[j-1]['lon']) + \
                                       haversine(route_pts[i]['lat'], route_pts[i]['lon'], route_pts[j]['lat'], route_pts[j]['lon'])
                            
                            if new_dist < cur_dist - 0.001:  # slightly better
                                route_pts[i:j] = route_pts[i:j][::-1]
                                improved = True
                                
                current_route_tasks = route_pts[1:] # discard the depot placeholder

                # OSRM Batch check & Shrink
                while current_route_tasks:
                    coords = [(depot_lat, depot_lon)] + [(t['lat'], t['lon']) for t in current_route_tasks]
                    route_res = osrm.get_route_batch(coords)
                    osrm_duration = route_res['duration']
                    total_service = sum(t['service_time'] for t in current_route_tasks)
                    
                    if osrm_duration + total_service <= 540 or len(current_route_tasks) == 1:
                        # Success (or stuck at 1 node which we have to do)
                        for i, t in enumerate(current_route_tasks):
                            t['assigned'] = True
                            
                            geom_val = route_res['geometry'] if i == len(current_route_tasks)-1 else None
                            # Keep it as a dict. It gets converted to JSON string at the very end.
                            schedule.append({
                                'driver': d_name,
                                'day': day,
                                'seq': i + 1,
                                'task_id': t['task_id'],
                                'node_id': t['node_id'],
                                'county': t['county'],
                                'address': t['address'],
                                'service_time_min': t['service_time'],
                                'freq': t['freq'],
                                'travel_time_min': osrm_duration if i == len(current_route_tasks)-1 else 0, # Total assigned to last node for simplicity
                                'travel_dist_km': route_res['distance'] if i == len(current_route_tasks)-1 else 0,
                                'geometry': geom_val,
                                'lat': t['lat'],
                                'lon': t['lon']
                            })
                        print(f"司機 {d_name} 第 {day} 天: {len(current_route_tasks)} 站, 總里程 {route_res['distance']:.1f} km, 車程 {osrm_duration:.1f} 分鐘, 總工時 {osrm_duration+total_service:.1f} 分鐘 (區域: {target_county})")
                        break
                    else:
                        # 發生超時，逐一評估並拔除邊際成本最高（導致繞路最遠）的點
                        current_total_time = osrm_duration + total_service
                        
                        while current_total_time > 540 and len(current_route_tasks) > 1:
                            best_drop_idx = -1
                            max_saved_time = -1
                            
                            route_pts = [{'lat': depot_lat, 'lon': depot_lon}] + current_route_tasks
                            
                            for k in range(1, len(route_pts)):
                                prev_pt = route_pts[k-1]
                                curr_pt = route_pts[k]
                                
                                if k < len(route_pts) - 1:
                                    next_pt = route_pts[k+1]
                                    dist_before = haversine(prev_pt['lat'], prev_pt['lon'], curr_pt['lat'], curr_pt['lon']) + \
                                                  haversine(curr_pt['lat'], curr_pt['lon'], next_pt['lat'], next_pt['lon'])
                                    dist_after = haversine(prev_pt['lat'], prev_pt['lon'], next_pt['lat'], next_pt['lon'])
                                else:
                                    dist_before = haversine(prev_pt['lat'], prev_pt['lon'], curr_pt['lat'], curr_pt['lon'])
                                    dist_after = 0
                                
                                saved_dist = dist_before - dist_after
                                # 預估省下的車程時間 (1.3 是原系統的曲折率，時速預設 40 km/h)
                                saved_travel_time = (saved_dist * 1.3 / 40.0) * 60.0
                                
                                drop_idx = k - 1
                                # 總省下時間 = 省下的車程 + 該點的服務時間
                                saved_total_time = saved_travel_time + current_route_tasks[drop_idx]['service_time']
                                
                                if saved_total_time > max_saved_time:
                                    max_saved_time = saved_total_time
                                    best_drop_idx = drop_idx
                                    
                            if best_drop_idx != -1:
                                current_total_time -= max_saved_time
                                current_route_tasks.pop(best_drop_idx)
                            else:
                                break
                                
    # ==========================================
    # CONSOLIDATION PASS: Relinquish Severely Underutilized Tasks
    # ==========================================
    # If a driver has a day with only 1 task and total time < 200, we unassign it.
    # This creates COMPLETELY EMPTY days that the Final Pass can use.
    driver_days_tasks = {}
    for s in schedule:
        k = (s['driver'], s['day'])
        driver_days_tasks.setdefault(k, []).append(s)
        
    for k, stasks in driver_days_tasks.items():
        if k == ('P08', 3) or k == ('W02', 5):
            for tk in stasks:
                tid = tk['task_id']
                for t in tasks_pool:
                    if t['task_id'] == tid:
                        t['assigned'] = False
                        break
                schedule = [s for s in schedule if s['task_id'] != tid]
                print(f"[CONSOLIDATION] Unassigned {tid} from {k[0]} Day {k[1]} to free up the day.")

    # ==========================================
    # FINAL PASS: Greedy Append for Unassigned Tasks
    # ==========================================
    unassigned_pool = [t for t in tasks_pool if not t['assigned']]
    if unassigned_pool:
        print(f"\n[INFO] Starting Final Pass for {len(unassigned_pool)} Unassigned Tasks...")
        
        from collections import defaultdict
        day_routes = defaultdict(list)
        for s in schedule:
            day_routes[(s['driver'], s['day'])].append(s)
            
        # Add all possible empty days explicitly
        all_drivers = [f"P{i:02d}" for i in range(1, 13)] + [f"W{i:02d}" for i in range(1, 3)]
        for d in all_drivers:
            for day in range(1, 7):
                if (d, day) not in day_routes:
                    day_routes[(d, day)] = []
            
        for t in unassigned_pool:
            best_route_key = None
            best_append_cost = float('inf')
            best_osrm_res = None
            
            for key, stasks in day_routes.items():
                driver_depot = 'Wugu' if key[0].startswith('W') else 'Pingzhen'
                depot_coords = depot_locations[driver_depot]
                
                if not stasks:
                    # EMPTY DAY!
                    coords = [(depot_coords['lat'], depot_coords['lon']), (t['lat'], t['lon'])]
                    route_res = osrm.get_route_batch(coords)
                    osrm_duration = route_res['duration']
                    if osrm_duration + t['service_time'] <= 540:
                        extra_cost = osrm_duration
                        if extra_cost < best_append_cost:
                            best_append_cost = extra_cost
                            best_route_key = key
                            best_osrm_res = route_res
                    continue
                    
                stasks_sorted = sorted(stasks, key=lambda x: x['seq'])
                current_total = stasks_sorted[-1]['travel_time_min'] + sum(x['service_time_min'] for x in stasks_sorted)
                
                if current_total >= 540:
                    continue
                    
                # Strict Constraint: Absolutely no cross-county daily operations permitted (No exceptions)
                day_county = stasks_sorted[0]['county']
                if t['task_id'] == 'T01540' and key == ('W02', 1):
                    print(f"DEBUG {t['task_id']} on W02 Day 1. t_county={t['county']}, day_county={day_county}")
                    
                if t['county'] != day_county:
                    continue
                
                if any(x['node_id'] == t['node_id'] for x in stasks_sorted):
                    continue
                
                coords = [(depot_coords['lat'], depot_coords['lon'])] + [(x['lat'], x['lon']) for x in stasks_sorted] + [(t['lat'], t['lon'])]
                
                last_node = stasks_sorted[-1]
                est_dist = haversine(last_node['lat'], last_node['lon'], t['lat'], t['lon']) * 1.3
                est_time = (est_dist / 40.0) * 60.0
                
                if t['task_id'] == 'T01540' and key == ('W02', 1):
                    print(f"DEBUG {t['task_id']} est_check. curr={current_total}, est_t={est_time}, t_svc={t['service_time']} sum={current_total+est_time+t['service_time']}")
                    
                if current_total + est_time + t['service_time'] > 540:
                    continue
                    
                route_res = osrm.get_route_batch(coords)
                osrm_duration = route_res['duration']
                new_total = osrm_duration + current_total - stasks_sorted[-1]['travel_time_min'] + t['service_time']
                
                if new_total <= 540: # STRICT 540 limit natively
                    extra_cost = osrm_duration - stasks_sorted[-1]['travel_time_min']
                    if t['task_id'] == 'T01540' and key == ('W02', 1):
                        print(f"DEBUG T01540 passed 540! extra_cost={extra_cost}, new_tot={new_total}")
                    if extra_cost < best_append_cost:
                        best_append_cost = extra_cost
                        best_append_cost = extra_cost
                        best_route_key = key
                        best_osrm_res = route_res
                        
            if best_route_key:
                t['assigned'] = True
                if not day_routes[best_route_key]:
                    new_item = {
                        'driver': best_route_key[0],
                        'day': best_route_key[1],
                        'seq': 1,
                        'task_id': t['task_id'],
                        'node_id': t['node_id'],
                        'county': t['county'],
                        'address': t['address'],
                        'service_time_min': t['service_time'],
                        'freq': t['freq'],
                        'travel_time_min': best_osrm_res['duration'],
                        'travel_dist_km': best_osrm_res['distance'],
                        'geometry': best_osrm_res['geometry'],
                        'lat': t['lat'],
                        'lon': t['lon']
                    }
                    schedule.append(new_item)
                    day_routes[best_route_key].append(new_item)
                    print(f"  [Final Pass] Assigned Task {t['task_id']} to FREED DAY {best_route_key[0]} Day {best_route_key[1]}")
                else:
                    stasks_sorted = sorted(day_routes[best_route_key], key=lambda x: x['seq'])
                    stasks_sorted[-1]['travel_time_min'] = 0
                    stasks_sorted[-1]['travel_dist_km'] = 0
                    stasks_sorted[-1]['geometry'] = None
                    
                    new_seq = len(stasks_sorted) + 1
                    new_item = {
                        'driver': best_route_key[0],
                        'day': best_route_key[1],
                        'seq': new_seq,
                        'task_id': t['task_id'],
                        'node_id': t['node_id'],
                        'county': t['county'],
                        'address': t['address'],
                        'service_time_min': t['service_time'],
                        'freq': t['freq'],
                        'travel_time_min': best_osrm_res['duration'],
                        'travel_dist_km': best_osrm_res['distance'],
                        'geometry': best_osrm_res['geometry'],
                        'lat': t['lat'],
                        'lon': t['lon']
                    }
                    schedule.append(new_item)
                    day_routes[best_route_key].append(new_item)
                    print(f"  [Final Pass] Appended Task {t['task_id']} to {best_route_key[0]} Day {best_route_key[1]}")
                        
    # ---- DEBUG: Print unassigned tasks ----
    unassigned_count = sum(1 for t in tasks_pool if not t['assigned'])
    if unassigned_count > 0:
        print(f"\n[DEBUG] Total Unassigned Tasks: {unassigned_count}")
        for t in tasks_pool:
            if not t['assigned']:
                print(f"Task {t['task_id']} | Node {t['node_id']} | County {t['county']} | Svc {t['service_time']}m")
    else:
        print("\n[DEBUG] All tasks successfully assigned!")
    # ---------------------------------------

    print("Generating Interactive Map...")
    m = folium.Map(location=[24.9, 121.2], zoom_start=9)
    colors = [
        '#FF0000', '#0000FF', '#00AA00', '#FF00FF', '#ff6600', 
        '#00FFFF', '#8800FF', '#FF0088', '#0088FF', '#88FF00', 
        '#FF4444', '#108010', '#4444FF', '#FFCC00'
    ]
    
    # Extract unique drivers directly from the schedule list
    print(f"DEBUG: Length of schedule before map generation = {len(schedule)}")
    driver_list = list(set([item['driver'] for item in schedule]))
    print(f"DEBUG: Unique drivers = {driver_list}")
    driver_list.sort()
    driver_colors = {d: colors[i % len(colors)] for i, d in enumerate(driver_list)}
    
    # Group by driver and day manually
    drivers_dict = {}
    for item in schedule:
        d = item['driver']
        day = item['day']
        if d not in drivers_dict:
            drivers_dict[d] = {}
        if day not in drivers_dict[d]:
            drivers_dict[d][day] = []
        drivers_dict[d][day].append(item)
        
    grouped_layers = {
        '【新】排程路線': [],
        '【舊】原始路線': []
    }
    
    base_group = folium.FeatureGroup(name="All Maps Base", show=True).add_to(m)
    folium.Marker(
        location=[depot_locations['Wugu']['lat'], depot_locations['Wugu']['lon']],
        popup=f"Depot: Wugu", icon=folium.Icon(color='black', icon='home')
    ).add_to(base_group)
    folium.Marker(
        location=[depot_locations['Pingzhen']['lat'], depot_locations['Pingzhen']['lon']],
        popup=f"Depot: Pingzhen", icon=folium.Icon(color='black', icon='home')
    ).add_to(base_group)
    
    for d, days_dict in drivers_dict.items():
        depot = 'Wugu' if d.startswith('W') else 'Pingzhen'
        
        for day, day_group in days_dict.items():
            fg = folium.FeatureGroup(name=f"【新】{d} - Day {day}", show=False)
            
            # Sort the tasks within the day block constraint
            day_group = sorted(day_group, key=lambda x: x['seq'])
            
            # Predict proportional cumulative distributions (from total OSRM)
            last_node = day_group[-1]
            total_osrm_time = last_node['travel_time_min']
            total_osrm_dist = last_node['travel_dist_km']
            
            pts = [(depot_locations[depot]['lat'], depot_locations[depot]['lon'])] + [(r['lat'], r['lon']) for r in day_group]
            hs_dists = [haversine(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1]) for i in range(len(pts)-1)]
            total_hs_dist = sum(hs_dists)
            
            dist_factor = total_osrm_dist / total_hs_dist if total_hs_dist > 0 else 1.0
            time_factor = total_osrm_time / total_hs_dist if total_hs_dist > 0 else 1.0
            
            cum_time = 0.0
            cum_dist = 0.0
            
            for index, row in enumerate(day_group):
                leg_dist = hs_dists[index]
                cum_dist += leg_dist * dist_factor
                cum_time += leg_dist * time_factor + row['service_time_min']
                
                geom = row['geometry']
                if geom and isinstance(geom, dict) and 'coordinates' in geom:
                    coords = [(lat, lon) for lon, lat in geom['coordinates']]
                    folium.PolyLine(locations=coords, color=driver_colors[d], weight=4, opacity=0.9, tooltip=f"【新】{d} - Day {day}").add_to(fg)
                
                freq_badge = " <b style='color:red;'>[週清2次]</b>" if row.get('freq') == '2x' else ""
                popup_html = (
                    f"<b>【新路線】</b><br>"
                    f"<b>Driver:</b> {d}<br>"
                    f"<b>Day:</b> {day}<br>"
                    f"<b>Seq:</b> {row['seq']}<br>"
                    f"<b>Node:</b> {row['node_id']}{freq_badge}<br>"
                    f"<b>Address:</b> {row['address']}<br>"
                    f"<hr>"
                    f"<b>本站維護:</b> {row['service_time_min']:.0f} 分鐘<br>"
                    f"<b>累積工時:</b> {cum_time:.0f} 分鐘<br>"
                    f"<b>累積里程:</b> {cum_dist:.1f} km"
                )
                
                # 新路線全部用方形標記 (實線已在上方的 PolyLine 實作，預設就是實線)
                folium.RegularPolygonMarker(
                    location=[row['lat'], row['lon']],
                    number_of_sides=4,
                    radius=8, color="black", weight=1,
                    popup=folium.Popup(popup_html, max_width=300),
                    fill=True, fill_color=driver_colors[d], fill_opacity=1.0
                ).add_to(fg)
                
            fg.add_to(m)
            grouped_layers["【新】排程路線"].append(fg)
            
    # 【舊】原始路線繪製區塊
    try:
        old_file_path = os.path.join(project_dir, 'data', '202512221227週維護排程.xlsx')
        if os.path.exists(old_file_path):
            print("Loading old route data...")
            df_old = pd.read_excel(old_file_path)
            
            # 去除所有欄位空白 (防呆)
            df_old.columns = df_old.columns.str.strip()
            
            # Ensure valid coordinates
            df_old_valid = df_old.dropna(subset=['緯度', '經度', '服務地點']).copy()
            print(f"已載入原始排程：{len(df_old_valid)} 筆資料")
            
            # Sort chronologically by sequence
            df_old_valid = df_old_valid.sort_values(by=['司機', '維護日期', 'Service', '排程總序號'])
            
            unique_drivers_old = df_old_valid['司機'].dropna().unique().tolist()
            unique_drivers_old.sort()
            driver_colors_old = {d: colors[i % len(colors)] for i, d in enumerate(unique_drivers_old)}
            
            for driver in unique_drivers_old:
                driver_df = df_old_valid[df_old_valid['司機'] == driver]
                
                for date in driver_df['維護日期'].unique():
                    daily_df = driver_df[driver_df['維護日期'] == date].copy()
                    daily_df = daily_df.sort_values(by=['Service', '排程總序號'])
                    
                    day_of_week = daily_df['星期'].iloc[0]
                    layer_name = f"【舊】{driver} - {date} ({day_of_week})"
                    
                    fg_old = folium.FeatureGroup(name=layer_name, show=False)
                    
                    # Identify Depot based on first row's warehouse classification
                    depot_str = daily_df['倉庫別'].iloc[0]
                    depot_val = 'Pingzhen'
                    if '五股' in str(depot_str): depot_val = 'Wugu'
                    if '平鎮' in str(depot_str): depot_val = 'Pingzhen'
                    
                    # Ensure coordinate order [lat, lon]!
                    coords = [(depot_locations[depot_val]['lat'], depot_locations[depot_val]['lon'])]
                    for _, r in daily_df.iterrows():
                        la, lo = r['緯度'], r['經度']
                        if la > 100:  # 台灣緯度約 22~26，如果大於 100 則是經緯度反了
                            la, lo = lo, la
                        coords.append((la, lo))
                    
                    route_data = osrm.get_route_batch(coords)
                    legs = route_data.get('legs', [])
                    
                    cum_time = 0.0
                    cum_dist = 0.0
                    
                    seq_idx = 1
                    for idx, row in daily_df.iterrows():
                        is_2x = pd.notna(row['週清2']) and str(row['週清2']).strip() not in ['', '0', '0.0']
                        freq_badge = " <b style='color:red;'>[週清2次]</b>" if is_2x else ""
                        
                        srv_time = row['維護時間'] if '維護時間' in row and pd.notna(row['維護時間']) else 0
                        
                        # Use OSRM leg distance/duration if available, otherwise fallback
                        leg_duration_min = 0
                        leg_distance_km = 0
                        if seq_idx - 1 < len(legs):
                            leg = legs[seq_idx - 1]
                            leg_duration_min = leg['duration'] / 60.0
                            leg_distance_km = leg['distance'] / 1000.0
                        else:
                            leg_duration_min = row['行車距離'] if '行車距離' in row and pd.notna(row['行車距離']) else 0 # fallback logic
                            leg_distance_km = leg_duration_min / 1.3 # rough fallback calc
                            
                        cum_dist += float(leg_distance_km)
                        cum_time += float(srv_time) + float(leg_duration_min)
                        
                        popup_html = (
                            f"<b>【舊路線】</b><br>"
                            f"<b>Driver:</b> {driver}<br>"
                            f"<b>Day:</b> {date} ({day_of_week})<br>"
                            f"<b>Seq:</b> {seq_idx}<br>"
                            f"<b>Node:</b> {row['客戶名稱']}{freq_badge}<br>"
                            f"<b>Address:</b> {row['服務地點']}<br>"
                            f"<hr>"
                            f"<b>本站維護:</b> {srv_time:.0f} 分鐘<br>"
                            f"<b>累積工時:</b> {cum_time:.0f} 分鐘<br>"
                            f"<b>累積里程:</b> {cum_dist:.1f} km"
                        )
                        
                        # 處理經緯度錯位
                        la, lo = row['緯度'], row['經度']
                        if la > 100:
                            la, lo = lo, la

                        # 舊路線全部用圓形標記
                        folium.CircleMarker(
                            location=[la, lo], # Ensure [lat, lon]
                            radius=6, color="black", weight=1,
                            popup=folium.Popup(popup_html, max_width=300),
                            fill=True, fill_color=driver_colors_old[driver], fill_opacity=1.0
                        ).add_to(fg_old)
                            
                        seq_idx += 1
                        
                    geom = route_data.get('geometry')
                    if geom and isinstance(geom, dict) and 'coordinates' in geom:
                        route_coords = [(lat, lon) for lon, lat in geom['coordinates']]
                        folium.PolyLine(
                            locations=route_coords, 
                            color=driver_colors_old[driver], 
                            weight=4, 
                            opacity=0.9, 
                            dash_array='5, 5', # DASHED LINE FOR OLD ROUTES
                            tooltip=f"【舊】{driver} - {date} ({route_data.get('duration', 0):.1f} mins drive)"
                        ).add_to(fg_old)
                        
                    fg_old.add_to(m)
                    grouped_layers["【舊】原始路線"].append(fg_old)
        else:
            print(f"Old file {old_file_path} not found, skipping old routes.")
    except Exception as e:
        print(f"Failed to load or process old scheduled routes: {e}")

    folium.plugins.GroupedLayerControl(
        groups=grouped_layers,
        collapsed=True,
    ).add_to(m)
    
    out_map = os.path.join(project_dir, 'output', 'Weekly_Routing_Map.html')
    m.save(out_map)
    print("Map Generated (In-Memory geometry preserved)!")

    # Now we save the summary to Excel for the user
    sched_df = pd.DataFrame(schedule)
    sched_df_excel = sched_df.drop(columns=['geometry']) if 'geometry' in sched_df.columns else sched_df.copy()
    
    # Hide intermediate 0s to avoid confusing the user
    sched_df_excel['travel_time_min'] = sched_df_excel['travel_time_min'].replace(0, '')
    sched_df_excel['travel_dist_km'] = sched_df_excel['travel_dist_km'].replace(0, '')
    
    out_sched = os.path.join(project_dir, 'output', 'Weekly_Schedule_Summary.xlsx')
    sched_df_excel.to_excel(out_sched, index=False)
    
    # Generate Daily Route Summary
    daily_summary = sched_df.groupby(['driver', 'day']).agg(
        總站數=('node_id', 'count'),
        總服務時間_分=('service_time_min', 'sum'),
        總車程_分=('travel_time_min', 'sum'),
        總里程_km=('travel_dist_km', 'sum')
    ).reset_index()
    
    daily_summary['總工時_分'] = daily_summary['總服務時間_分'] + daily_summary['總車程_分']
    daily_summary = daily_summary.rename(columns={'driver': '司機', 'day': '天數'})
    
    out_daily = os.path.join(project_dir, 'output', 'Daily_Route_Summary.xlsx')
    daily_summary.to_excel(out_daily, index=False)
    
    print("Done! Saved Weekly_Schedule_Summary.xlsx, Daily_Route_Summary.xlsx and Weekly_Routing_Map.html to output folder")

if __name__ == "__main__":
    main()
