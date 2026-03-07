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
    df = pd.read_csv('processed_nodes_phase1.csv')
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
        for i in range(count):
            tasks.append({
                'task_id': f"T{task_id_counter:05d}",
                'node_id': row['Node_ID'],
                'lat': row['Lat'],
                'lon': row['Lon'],
                'service_time': row['Service_Time'],
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
        'Wugu': {'lat': 25.0783391, 'lon': 121.4357565},
        'Pingzhen': {'lat': 24.9046004, 'lon': 121.2265770}
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
                for t in depot_tasks:
                    if not t['assigned']:
                        node_visited_today = any(s['node_id'] == t['node_id'] and s['day'] == day for s in schedule)
                        if not node_visited_today:
                            valid_unassigned.append(t)
                            
                if not valid_unassigned: continue
                    
                county_times = {}
                for t in valid_unassigned:
                    county_times[t['county']] = county_times.get(t['county'], 0) + t['service_time']
                target_county = max(county_times, key=county_times.get)
                valid_unassigned_county = [t for t in valid_unassigned if t['county'] == target_county]
                
                # Heuristic build route using Haversine
                current_route_tasks = []
                current_lat, current_lon = depot_lat, depot_lon
                
                est_day_total = 0
                while valid_unassigned_county:
                    def d(t): return (t['lat']-current_lat)**2 + (t['lon']-current_lon)**2
                    valid_unassigned_county.sort(key=d)
                    
                    # Prevent assigning the 2nd visit of a 2x task if the 1st visit is ALREADY in today's route
                    cand = None
                    cand_idx = -1
                    for idx, t_cand in enumerate(valid_unassigned_county):
                        # check if we already have this node in current_route_tasks
                        if not any(r['node_id'] == t_cand['node_id'] for r in current_route_tasks):
                            cand = t_cand
                            cand_idx = idx
                            break
                            
                    if not cand:
                        break # no more valid candidates for today that aren't duplicates
                    
                    est_dist = haversine(current_lat, current_lon, cand['lat'], cand['lon']) * 1.3
                    est_time = (est_dist / 40.0) * 60.0
                    
                    if est_day_total + est_time + cand['service_time'] > 550: # Slight overshoot tolerance
                        break
                        
                    current_route_tasks.append(cand)
                    est_day_total += est_time + cand['service_time']
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
                        print(f"Driver {d_name} Day {day}: {len(current_route_tasks)} tasks, OSRM duration {osrm_duration:.1f}, total {osrm_duration+total_service:.1f} mins ({target_county})")
                        break
                    else:
                        overflow = (osrm_duration + total_service) - 540
                        # Roughly 15-20 mins per task on average. Drop proportionally.
                        drop_count = max(1, math.ceil(overflow / 20.0))
                        drop_count = min(drop_count, len(current_route_tasks) - 1)
                        for _ in range(drop_count):
                            current_route_tasks.pop()
                        
    print("Generating Interactive Map...")
    m = folium.Map(location=[24.9, 121.2], zoom_start=9)
    colors = [
        '#FF0000', '#0000FF', '#00AA00', '#FF00FF', '#ff6600', 
        '#00FFFF', '#8800FF', '#FF0088', '#0088FF', '#88FF00', 
        '#FF4444', '#108010', '#4444FF', '#FFCC00'
    ]
    
    # We use the raw `schedule` list directly instead of `sched_df` from Excel
    # Since `schedule['geometry']` holds the real raw JSON dict or `None` already before stringification
    
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
        
    grouped_layers = {}
    base_group = folium.FeatureGroup(name="All Maps Base", show=True).add_to(m)
    
    for d, days_dict in drivers_dict.items():
        depot = 'Wugu' if d.startswith('W') else 'Pingzhen'
        grouped_layers[f"Driver {d}"] = []
        
        for day, day_group in days_dict.items():
            fg = folium.FeatureGroup(name=f"{d} - Day {day}", show=False)
            
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
                    folium.PolyLine(locations=coords, color=driver_colors[d], weight=4, opacity=0.9, tooltip=f"{d} - Day {day}").add_to(fg)
                
                freq_badge = " <b style='color:red;'>[週清2次]</b>" if row.get('freq') == '2x' else ""
                popup_html = (
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
                
                if row.get('freq') == '2x':
                    folium.RegularPolygonMarker(
                        location=[row['lat'], row['lon']],
                        number_of_sides=4,
                        radius=8, color="black", weight=1,
                        popup=folium.Popup(popup_html, max_width=300),
                        fill=True, fill_color=driver_colors[d], fill_opacity=1.0
                    ).add_to(fg)
                else:
                    folium.CircleMarker(
                        location=[row['lat'], row['lon']],
                        radius=6, color="black", weight=1,
                        popup=folium.Popup(popup_html, max_width=300),
                        fill=True, fill_color=driver_colors[d], fill_opacity=1.0
                    ).add_to(fg)
                
            folium.Marker(
                location=[depot_locations[depot]['lat'], depot_locations[depot]['lon']],
                popup=f"Depot: {depot}", icon=folium.Icon(color='black', icon='home')
            ).add_to(fg)
            
            fg.add_to(m)
            grouped_layers[f"Driver {d}"].append(fg)
            
    folium.plugins.GroupedLayerControl(
        groups=grouped_layers,
        collapsed=True,
    ).add_to(m)
    
    m.save('Weekly_Routing_Map.html')
    print("Map Map Generated (In-Memory geometry preserved)!")

    # Now we save the summary to Excel for the user (truncation doesn't matter for the map anymore)
    for t in schedule:
        if t['geometry']:
            t['geometry'] = json.dumps(t['geometry'])
            
    sched_df = pd.DataFrame(schedule)
    sched_df.to_excel('Weekly_Schedule_Summary.xlsx', index=False)
    print("Done! Saved Weekly_Schedule_Summary.xlsx and Weekly_Routing_Map.html")

if __name__ == "__main__":
    main()
