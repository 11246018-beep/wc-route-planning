import pandas as pd
import folium
import re
import requests
import json
import os
import sqlite3
import math
import folium.plugins
from collections import defaultdict


DAY_COUNT = 6
MAX_MINUTES = 540


class OSRMClient:
    def __init__(self, cache_file='../osrm_cache.db'):
        cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), cache_file)
        self.conn = sqlite3.connect(cache_path)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS batch_cache
            (hash TEXT PRIMARY KEY, duration REAL, distance REAL, geometry TEXT)
        ''')
        self.conn.commit()

    def get_route_batch(self, coords):
        if len(coords) < 2:
            return {'duration': 0, 'distance': 0, 'geometry': None}

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
                if data.get('code') == 'Ok' and data.get('routes'):
                    route = data['routes'][0]
                    duration_min = route['duration'] / 60.0
                    distance_km = route['distance'] / 1000.0
                    geometry = route['geometry']

                    self.cursor.execute(
                        "INSERT OR REPLACE INTO batch_cache VALUES (?, ?, ?, ?)",
                        (c_hash, duration_min, distance_km, json.dumps(geometry))
                    )
                    self.conn.commit()
                    return {'duration': duration_min, 'distance': distance_km, 'geometry': geometry}
        except Exception as e:
            print(f"OSRM Batch Error: {e}")

        # fallback: haversine
        dist_km = sum([
            haversine(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1])
            for i in range(len(coords) - 1)
        ]) * 1.3
        duration_min = (dist_km / 40.0) * 60.0
        geom = {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in coords]}
        return {'duration': duration_min, 'distance': dist_km, 'geometry': geom}


def parse_county(addr):
    addr = str(addr).strip()
    pattern = r'(基隆市|台北市|臺北市|新北市|桃園市|桃園縣|新竹市|新竹縣|苗栗縣|台中市|臺中市|彰化縣|南投縣|雲林縣|嘉義市|嘉義縣|台南市|臺南市|高雄市|屏東縣|宜蘭縣|花蓮縣|台東縣|臺東縣|澎湖縣|金門縣|連江縣)'
    match = re.search(pattern, addr)
    if match:
        return match.group(1).replace('臺', '台')
    if len(addr) >= 3 and addr[2] in ['縣', '市']:
        return addr[:3].replace('臺', '台')
    return 'Unknown'


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat / 2) * math.sin(dLat / 2) + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon / 2) * math.sin(dLon / 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def to_float(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        text = str(value).strip()
        if text == '' or text.lower() == 'nan':
            return default
        return float(text)
    except Exception:
        return default


def to_int(value, default=0):
    try:
        if pd.isna(value):
            return default
        text = str(value).strip()
        if text == '' or text.lower() == 'nan':
            return default
        return int(float(text))
    except Exception:
        return default


def clean_text(value, default=''):
    try:
        if pd.isna(value):
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def get_depot(depot_str):
    text = str(depot_str)
    if '五股' in text:
        return 'Wugu'
    if '平鎮' in text:
        return 'Pingzhen'
    return 'Unknown'


def driver_label(code):
    s = str(code or '').upper()
    if s.startswith('P') and s[1:].isdigit():
        return f"{s}｜平鎮{s[1:].lstrip('0') or '0'}"
    if s.startswith('W') and s[1:].isdigit():
        return f"{s}｜五股{s[1:].lstrip('0') or '0'}"
    return s


def build_candidate_route_for_county(task_candidates, depot_lat, depot_lon, osrm):
    """
    task_candidates: 同倉、同縣市、未分配、且該 day 尚未拜訪過同 node 的點
    回傳:
      {
        'tasks': [...],
        'route_res': {...},
        'osrm_duration': ...,
        'total_service': ...,
        'total_time': ...
      }
    or None
    """
    if not task_candidates:
        return None

    # 1) 先用 haversine greedy 建初始路線
    current_route_tasks = []
    current_lat, current_lon = depot_lat, depot_lon
    est_day_total = 0.0
    remaining = list(task_candidates)

    while remaining:
        remaining.sort(key=lambda t: (t['lat'] - current_lat) ** 2 + (t['lon'] - current_lon) ** 2)

        cand = None
        cand_idx = -1
        best_est_time = 0.0

        for idx, t_cand in enumerate(remaining):
            if any(r['node_id'] == t_cand['node_id'] for r in current_route_tasks):
                continue

            est_dist = haversine(current_lat, current_lon, t_cand['lat'], t_cand['lon']) * 1.3
            est_time = (est_dist / 40.0) * 60.0

            if est_day_total + est_time + t_cand['service_time'] <= MAX_MINUTES:
                cand = t_cand
                cand_idx = idx
                best_est_time = est_time
                break

        if not cand:
            break

        current_route_tasks.append(cand)
        est_day_total += best_est_time + cand['service_time']
        current_lat, current_lon = cand['lat'], cand['lon']
        remaining.pop(cand_idx)

    if not current_route_tasks:
        return None

    # 2) 2-opt 優化
    route_pts = [{'lat': depot_lat, 'lon': depot_lon}] + current_route_tasks[:]
    improved = True
    while improved:
        improved = False
        for i in range(1, len(route_pts) - 1):
            for j in range(i + 1, len(route_pts)):
                if j - i == 1:
                    continue

                cur_dist = (
                    haversine(route_pts[i-1]['lat'], route_pts[i-1]['lon'], route_pts[i]['lat'], route_pts[i]['lon']) +
                    haversine(route_pts[j-1]['lat'], route_pts[j-1]['lon'], route_pts[j]['lat'], route_pts[j]['lon'])
                )
                new_dist = (
                    haversine(route_pts[i-1]['lat'], route_pts[i-1]['lon'], route_pts[j-1]['lat'], route_pts[j-1]['lon']) +
                    haversine(route_pts[i]['lat'], route_pts[i]['lon'], route_pts[j]['lat'], route_pts[j]['lon'])
                )

                if new_dist < cur_dist - 0.001:
                    route_pts[i:j] = route_pts[i:j][::-1]
                    improved = True

    current_route_tasks = route_pts[1:]

    # 3) 用 OSRM 精算，若超時則逐步移除邊際成本最高的點
    while current_route_tasks:
        coords = [(depot_lat, depot_lon)] + [(t['lat'], t['lon']) for t in current_route_tasks]
        route_res = osrm.get_route_batch(coords)
        osrm_duration = route_res['duration']
        total_service = sum(t['service_time'] for t in current_route_tasks)
        total_time = osrm_duration + total_service

        if total_time <= MAX_MINUTES:
            return {
                'tasks': current_route_tasks,
                'route_res': route_res,
                'osrm_duration': osrm_duration,
                'total_service': total_service,
                'total_time': total_time,
            }

        # 嚴格版：如果只剩 1 站還超時，不可硬塞
        if len(current_route_tasks) == 1:
            return None

        # 拔掉邊際成本最高的點
        best_drop_idx = -1
        max_saved_time = -1

        route_pts = [{'lat': depot_lat, 'lon': depot_lon}] + current_route_tasks

        for k in range(1, len(route_pts)):
            prev_pt = route_pts[k - 1]
            curr_pt = route_pts[k]

            if k < len(route_pts) - 1:
                next_pt = route_pts[k + 1]
                dist_before = (
                    haversine(prev_pt['lat'], prev_pt['lon'], curr_pt['lat'], curr_pt['lon']) +
                    haversine(curr_pt['lat'], curr_pt['lon'], next_pt['lat'], next_pt['lon'])
                )
                dist_after = haversine(prev_pt['lat'], prev_pt['lon'], next_pt['lat'], next_pt['lon'])
            else:
                dist_before = haversine(prev_pt['lat'], prev_pt['lon'], curr_pt['lat'], curr_pt['lon'])
                dist_after = 0

            saved_dist = dist_before - dist_after
            saved_travel_time = (saved_dist * 1.3 / 40.0) * 60.0
            drop_idx = k - 1
            saved_total_time = saved_travel_time + current_route_tasks[drop_idx]['service_time']

            if saved_total_time > max_saved_time:
                max_saved_time = saved_total_time
                best_drop_idx = drop_idx

        if best_drop_idx != -1:
            current_route_tasks.pop(best_drop_idx)
        else:
            return None

    return None


def validate_schedule_strict(schedule):
    issues = []
    grouped = defaultdict(list)

    for s in schedule:
        grouped[(s['driver'], s['day'])].append(s)

    for (driver, day), items in grouped.items():
        items_sorted = sorted(items, key=lambda x: x['seq'])
        counties = sorted(set(i['county'] for i in items_sorted))
        total_service = sum(i['service_time_min'] for i in items_sorted)
        total_drive = items_sorted[-1]['travel_time_min'] if items_sorted else 0
        total_time = total_service + total_drive

        if len(counties) > 1:
            issues.append(f"[跨縣市] {driver} Day {day} counties={counties}")

        if total_time > MAX_MINUTES + 1e-6:
            issues.append(f"[超時] {driver} Day {day} total={total_time:.2f}")

    return issues


def main():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    input_csv = os.path.join(current_dir, '../../output/processed_nodes_phase1.csv')

    if not os.path.exists(input_csv):
        raise FileNotFoundError(f"找不到輸入檔案: {input_csv}")

    df = pd.read_csv(input_csv)
    df['County'] = df['Address'].apply(parse_county)
    df['Depot'] = df['Depot_Raw'].apply(get_depot)

    # 只修 Unknown，不改動原始已指定的五股/平鎮
    for idx, row in df.iterrows():
        if row['Depot'] == 'Unknown':
            if row['County'] in ['台北市', '新北市', '基隆市']:
                df.at[idx, 'Depot'] = 'Wugu'
            else:
                df.at[idx, 'Depot'] = 'Pingzhen'

    tasks = []
    task_id_counter = 1

    for _, row in df.iterrows():
        weekly_1 = to_int(row.get('weekly_1'), 1)
        weekly_2 = to_int(row.get('weekly_2'), 0)
        visits = max(1, weekly_1 + weekly_2)

        service_time_total = to_float(row.get('Service_Time'), 0.0)
        service_time_per_visit = service_time_total / visits if visits else service_time_total

        for i in range(visits):
            tasks.append({
                'task_id': f"T{task_id_counter:05d}",
                'node_id': clean_text(row.get('Node_ID'), f"N{task_id_counter:05d}"),
                'lat': to_float(row.get('Lat'), 0.0),
                'lon': to_float(row.get('Lon'), 0.0),
                'service_time': service_time_per_visit,
                'county': clean_text(row.get('County'), 'Unknown'),
                'depot': clean_text(row.get('Depot'), 'Unknown'),
                'address': clean_text(row.get('Address'), ''),
                'visit_idx': i + 1,
                'freq': clean_text(row.get('Freq'), ''),
                'assigned': False,
            })
            task_id_counter += 1

    tasks_df = pd.DataFrame(tasks)
    print(f"Total tasks generated: {len(tasks_df)}")

    depot_locations = {
        'Wugu': {'lat': 25.07154, 'lon': 121.44169},
        'Pingzhen': {'lat': 24.90703, 'lon': 121.226872}
    }

    driver_config = {'Wugu': 2, 'Pingzhen': 12}
    driver_names = {
        'Wugu': [f'W{i:02d}' for i in range(1, driver_config['Wugu'] + 1)],
        'Pingzhen': [f'P{i:02d}' for i in range(1, driver_config['Pingzhen'] + 1)],
    }
    driver_week_load = {d: 0.0 for depot in driver_names for d in driver_names[depot]}
    driver_used_days = {d: 0 for depot in driver_names for d in driver_names[depot]}

    osrm = OSRMClient()
    schedule = []
    tasks_pool = tasks_df.to_dict('records')

    # =========================
    # 主排程：固定倉 + 嚴格不跨縣市 + 不超 540 + 盡量平衡司機週工時
    # =========================
    for depot, num_drivers in driver_config.items():
        depot_lat = depot_locations[depot]['lat']
        depot_lon = depot_locations[depot]['lon']

        for day in range(1, DAY_COUNT + 1):
            ordered_drivers = sorted(
                driver_names[depot],
                key=lambda d: (driver_week_load[d], driver_used_days[d], d)
            )

            for d_name in ordered_drivers:
                valid_unassigned = []
                for t in tasks_pool:
                    if t['assigned']:
                        continue
                    if t['depot'] != depot:
                        continue
                    if t['county'] == 'Unknown':
                        continue

                    node_visited_today = any(
                        s['node_id'] == t['node_id'] and s['day'] == day
                        for s in schedule
                    )
                    if not node_visited_today:
                        valid_unassigned.append(t)

                if not valid_unassigned:
                    continue

                county_times = {}
                for t in valid_unassigned:
                    county_times[t['county']] = county_times.get(t['county'], 0) + t['service_time']

                county_candidates = sorted(
                    county_times.items(),
                    key=lambda kv: (-kv[1], kv[0])
                )

                best_plan = None
                best_plan_score = float('inf')
                best_target_county = None

                for target_county, _ in county_candidates:
                    same_county_tasks = [t for t in valid_unassigned if t['county'] == target_county]

                    plan = build_candidate_route_for_county(
                        task_candidates=same_county_tasks,
                        depot_lat=depot_lat,
                        depot_lon=depot_lon,
                        osrm=osrm
                    )

                    if not plan:
                        continue

                    # 分數越低越好：路線總工時 + 司機目前週工時權重
                    score = plan['total_time'] + driver_week_load[d_name] * 0.20 + driver_used_days[d_name] * 3.0

                    if score < best_plan_score:
                        best_plan = plan
                        best_plan_score = score
                        best_target_county = target_county

                if not best_plan:
                    continue

                current_route_tasks = best_plan['tasks']
                route_res = best_plan['route_res']
                osrm_duration = best_plan['osrm_duration']
                total_service = best_plan['total_service']
                total_time = best_plan['total_time']

                for i, t in enumerate(current_route_tasks):
                    t['assigned'] = True
                    geom_val = route_res['geometry'] if i == len(current_route_tasks) - 1 else None

                    schedule.append({
                        'driver': d_name,
                        'driver_label': driver_label(d_name),
                        'day': day,
                        'seq': i + 1,
                        'task_id': t['task_id'],
                        'node_id': t['node_id'],
                        'county': t['county'],
                        'address': t['address'],
                        'service_time_min': round(t['service_time'], 2),
                        'freq': t['freq'],
                        'visit_idx': t['visit_idx'],
                        'travel_time_min': round(osrm_duration, 2) if i == len(current_route_tasks) - 1 else 0,
                        'travel_dist_km': round(route_res['distance'], 2) if i == len(current_route_tasks) - 1 else 0,
                        'geometry': geom_val,
                        'lat': t['lat'],
                        'lon': t['lon'],
                    })

                driver_week_load[d_name] += total_time
                driver_used_days[d_name] += 1

                print(
                    f"司機 {d_name} 第 {day} 天: {len(current_route_tasks)} 站, "
                    f"總里程 {route_res['distance']:.1f} km, 車程 {osrm_duration:.1f} 分鐘, "
                    f"總工時 {total_time:.1f} 分鐘 (區域: {best_target_county})"
                )

    # =========================
    # FINAL PASS：只允許同倉、同縣市、且不超 540
    # =========================
    unassigned_pool = [t for t in tasks_pool if not t['assigned']]
    if unassigned_pool:
        print(f"\n[INFO] Starting Strict Final Pass for {len(unassigned_pool)} Unassigned Tasks...")

        def unassigned_sort_key(t):
            depot_val = t.get('depot', 'Unknown')
            county_val = t.get('county', 'Unknown')
            svc = -t.get('service_time', 0)
            return (depot_val, county_val, svc, t.get('task_id', ''))

        unassigned_pool = sorted(unassigned_pool, key=unassigned_sort_key)

        day_routes = defaultdict(list)
        for s in schedule:
            day_routes[(s['driver'], s['day'])].append(s)

        all_drivers = driver_names['Wugu'] + driver_names['Pingzhen']
        for d in all_drivers:
            for day in range(1, DAY_COUNT + 1):
                if (d, day) not in day_routes:
                    day_routes[(d, day)] = []

        for t in unassigned_pool:
            if t['county'] == 'Unknown':
                continue
            if t['depot'] not in depot_locations:
                continue

            best_route_key = None
            best_append_score = float('inf')
            best_osrm_res = None
            best_new_total = None
            best_delta = None

            for key, stasks in day_routes.items():
                driver = key[0]
                driver_depot = 'Wugu' if driver.startswith('W') else 'Pingzhen'
                depot_coords = depot_locations[driver_depot]

                # 嚴格同倉
                if t['depot'] != driver_depot:
                    continue

                if not stasks:
                    # 空白日：單點同倉可開新日
                    coords = [(depot_coords['lat'], depot_coords['lon']), (t['lat'], t['lon'])]
                    route_res = osrm.get_route_batch(coords)
                    total_time = route_res['duration'] + t['service_time']

                    if total_time <= MAX_MINUTES:
                        score = total_time + driver_week_load[driver] * 0.20 + driver_used_days[driver] * 3.0 + 10.0
                        if score < best_append_score:
                            best_append_score = score
                            best_route_key = key
                            best_osrm_res = route_res
                            best_new_total = total_time
                            best_delta = total_time
                    continue

                stasks_sorted = sorted(stasks, key=lambda x: x['seq'])
                current_total = stasks_sorted[-1]['travel_time_min'] + sum(x['service_time_min'] for x in stasks_sorted)

                if current_total >= MAX_MINUTES:
                    continue

                day_county = stasks_sorted[0]['county']
                if t['county'] != day_county:
                    continue

                if any(x['node_id'] == t['node_id'] for x in stasks_sorted):
                    continue

                coords = (
                    [(depot_coords['lat'], depot_coords['lon'])]
                    + [(x['lat'], x['lon']) for x in stasks_sorted]
                    + [(t['lat'], t['lon'])]
                )

                last_node = stasks_sorted[-1]
                est_dist = haversine(last_node['lat'], last_node['lon'], t['lat'], t['lon']) * 1.3
                est_time = (est_dist / 40.0) * 60.0

                if current_total + est_time + t['service_time'] > MAX_MINUTES:
                    continue

                route_res = osrm.get_route_batch(coords)
                new_total = route_res['duration'] + sum(x['service_time_min'] for x in stasks_sorted) + t['service_time']

                if new_total <= MAX_MINUTES:
                    delta = new_total - current_total
                    score = delta + driver_week_load[driver] * 0.20 + driver_used_days[driver] * 3.0
                    if score < best_append_score:
                        best_append_score = score
                        best_route_key = key
                        best_osrm_res = route_res
                        best_new_total = new_total
                        best_delta = delta

            if best_route_key:
                t['assigned'] = True
                driver = best_route_key[0]

                if not day_routes[best_route_key]:
                    new_item = {
                        'driver': driver,
                        'driver_label': driver_label(driver),
                        'day': best_route_key[1],
                        'seq': 1,
                        'task_id': t['task_id'],
                        'node_id': t['node_id'],
                        'county': t['county'],
                        'address': t['address'],
                        'service_time_min': round(t['service_time'], 2),
                        'freq': t['freq'],
                        'visit_idx': t['visit_idx'],
                        'travel_time_min': round(best_osrm_res['duration'], 2),
                        'travel_dist_km': round(best_osrm_res['distance'], 2),
                        'geometry': best_osrm_res['geometry'],
                        'lat': t['lat'],
                        'lon': t['lon']
                    }
                    schedule.append(new_item)
                    day_routes[best_route_key].append(new_item)
                    driver_week_load[driver] += best_delta
                    driver_used_days[driver] += 1
                    print(f"  [Final Pass] Assigned Task {t['task_id']} to EMPTY DAY {best_route_key[0]} Day {best_route_key[1]}")
                else:
                    stasks_sorted = sorted(day_routes[best_route_key], key=lambda x: x['seq'])
                    stasks_sorted[-1]['travel_time_min'] = 0
                    stasks_sorted[-1]['travel_dist_km'] = 0
                    stasks_sorted[-1]['geometry'] = None

                    new_seq = len(stasks_sorted) + 1
                    new_item = {
                        'driver': driver,
                        'driver_label': driver_label(driver),
                        'day': best_route_key[1],
                        'seq': new_seq,
                        'task_id': t['task_id'],
                        'node_id': t['node_id'],
                        'county': t['county'],
                        'address': t['address'],
                        'service_time_min': round(t['service_time'], 2),
                        'freq': t['freq'],
                        'visit_idx': t['visit_idx'],
                        'travel_time_min': round(best_osrm_res['duration'], 2),
                        'travel_dist_km': round(best_osrm_res['distance'], 2),
                        'geometry': best_osrm_res['geometry'],
                        'lat': t['lat'],
                        'lon': t['lon']
                    }
                    schedule.append(new_item)
                    day_routes[best_route_key].append(new_item)
                    driver_week_load[driver] += best_delta
                    print(f"  [Final Pass] Appended Task {t['task_id']} to {best_route_key[0]} Day {best_route_key[1]}")

    # =========================
    # 驗證嚴格條件
    # =========================
    issues = validate_schedule_strict(schedule)
    if issues:
        print("\n[STRICT VALIDATION FAILED]")
        for msg in issues:
            print(msg)
        raise RuntimeError("固定倉嚴格版排程結果違反『不跨縣市 / 不超 540』限制，請檢查輸出。")
    else:
        print("\n[STRICT VALIDATION PASSED] 所有路線皆符合：同日不跨縣市、總工時 <= 540 分鐘")

    # ---- DEBUG: Print unassigned tasks ----
    unassigned_tasks = [t for t in tasks_pool if not t['assigned']]
    if unassigned_tasks:
        print(f"\n[DEBUG] Total Unassigned Tasks: {len(unassigned_tasks)}")
        for t in unassigned_tasks:
            print(f"Task {t['task_id']} | Node {t['node_id']} | Depot {t['depot']} | County {t['county']} | Svc {t['service_time']:.1f}m")
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

    print(f"DEBUG: Length of schedule before map generation = {len(schedule)}")
    driver_list = list(set([item['driver'] for item in schedule]))
    print(f"DEBUG: Unique drivers = {driver_list}")
    driver_list.sort()
    driver_colors = {d: colors[i % len(colors)] for i, d in enumerate(driver_list)}

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
        popup="Depot: Wugu",
        icon=folium.Icon(color='black', icon='home')
    ).add_to(base_group)
    folium.Marker(
        location=[depot_locations['Pingzhen']['lat'], depot_locations['Pingzhen']['lon']],
        popup="Depot: Pingzhen",
        icon=folium.Icon(color='black', icon='home')
    ).add_to(base_group)

    for d, days_dict in drivers_dict.items():
        depot = 'Wugu' if d.startswith('W') else 'Pingzhen'

        for day, day_group in days_dict.items():
            fg = folium.FeatureGroup(name=f"【新】{d} - Day {day}", show=False)
            day_group = sorted(day_group, key=lambda x: x['seq'])

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
                leg_dist = hs_dists[index] if index < len(hs_dists) else 0
                cum_dist += leg_dist * dist_factor
                cum_time += leg_dist * time_factor + row['service_time_min']

                geom = row['geometry']
                if geom and isinstance(geom, dict) and 'coordinates' in geom:
                    coords = [(lat, lon) for lon, lat in geom['coordinates']]
                    folium.PolyLine(
                        locations=coords,
                        color=driver_colors[d],
                        weight=4,
                        opacity=0.9,
                        tooltip=f"【新】{d} - Day {day}"
                    ).add_to(fg)

                freq_badge = " <b style='color:red;'>[週清2次]</b>" if str(row.get('freq')) == '2x' else ""
                popup_html = (
                    f"<b>【新路線】</b><br>"
                    f"<b>Driver:</b> {d}<br>"
                    f"<b>Driver Label:</b> {row.get('driver_label', driver_label(d))}<br>"
                    f"<b>Day:</b> {day}<br>"
                    f"<b>Seq:</b> {row['seq']}<br>"
                    f"<b>Node:</b> {row['node_id']}{freq_badge}<br>"
                    f"<b>Address:</b> {row['address']}<br>"
                    f"<b>County:</b> {row['county']}<br>"
                    f"<hr>"
                    f"<b>本站維護:</b> {row['service_time_min']:.0f} 分鐘<br>"
                    f"<b>累積工時:</b> {cum_time:.0f} 分鐘<br>"
                    f"<b>累積里程:</b> {cum_dist:.1f} km"
                )

                folium.RegularPolygonMarker(
                    location=[row['lat'], row['lon']],
                    number_of_sides=4,
                    radius=8,
                    color="black",
                    weight=1,
                    popup=folium.Popup(popup_html, max_width=320),
                    fill=True,
                    fill_color=driver_colors[d],
                    fill_opacity=1.0
                ).add_to(fg)

            fg.add_to(m)
            grouped_layers["【新】排程路線"].append(fg)

    # 【舊】原始路線
    try:
        old_file_path = os.path.join(current_dir, '../data/202512221227週維護排程.xlsx')
        if os.path.exists(old_file_path):
            print("Loading old route data...")
            df_old = pd.read_excel(old_file_path)
            df_old.columns = df_old.columns.str.strip()

            df_old_valid = df_old.dropna(subset=['緯度', '經度', '服務地點']).copy()
            print(f"已載入原始排程：{len(df_old_valid)} 筆資料")

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

                    depot_str = daily_df['倉庫別'].iloc[0]
                    depot_val = 'Pingzhen'
                    if '五股' in str(depot_str):
                        depot_val = 'Wugu'
                    if '平鎮' in str(depot_str):
                        depot_val = 'Pingzhen'

                    coords = [(depot_locations[depot_val]['lat'], depot_locations[depot_val]['lon'])]
                    for _, r in daily_df.iterrows():
                        la, lo = r['緯度'], r['經度']
                        if la > 100:
                            la, lo = lo, la
                        coords.append((la, lo))

                    route_data = osrm.get_route_batch(coords)
                    legs = route_data.get('legs', [])

                    cum_time = 0.0
                    cum_dist = 0.0
                    seq_idx = 1

                    for _, row in daily_df.iterrows():
                        is_2x = pd.notna(row.get('週清2')) and str(row.get('週清2')).strip() not in ['', '0', '0.0']
                        freq_badge = " <b style='color:red;'>[週清2次]</b>" if is_2x else ""

                        srv_time = row['維護時間'] if '維護時間' in row and pd.notna(row['維護時間']) else 0

                        leg_duration_min = 0
                        leg_distance_km = 0
                        if seq_idx - 1 < len(legs):
                            leg = legs[seq_idx - 1]
                            leg_duration_min = leg['duration'] / 60.0
                            leg_distance_km = leg['distance'] / 1000.0
                        else:
                            leg_duration_min = row['行車距離'] if '行車距離' in row and pd.notna(row['行車距離']) else 0
                            leg_distance_km = leg_duration_min / 1.3 if leg_duration_min else 0

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

                        la, lo = row['緯度'], row['經度']
                        if la > 100:
                            la, lo = lo, la

                        folium.CircleMarker(
                            location=[la, lo],
                            radius=6,
                            color="black",
                            weight=1,
                            popup=folium.Popup(popup_html, max_width=300),
                            fill=True,
                            fill_color=driver_colors_old[driver],
                            fill_opacity=1.0
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
                            dash_array='5, 5',
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

    map_path = os.path.join(current_dir, '../../output/Weekly_Routing_Map.html')
    m.save(map_path)
    print("Map Generated!")

    # =========================
    # 匯出 Excel / JSON
    # =========================
    sched_df = pd.DataFrame(schedule)

    if not sched_df.empty:
        sched_df = sched_df.sort_values(by=['driver', 'day', 'seq']).reset_index(drop=True)
        sched_df_excel = sched_df.drop(columns=['geometry']) if 'geometry' in sched_df.columns else sched_df.copy()

        if 'travel_time_min' in sched_df_excel.columns:
            sched_df_excel['travel_time_min'] = sched_df_excel['travel_time_min'].replace(0, '')
        if 'travel_dist_km' in sched_df_excel.columns:
            sched_df_excel['travel_dist_km'] = sched_df_excel['travel_dist_km'].replace(0, '')

        summary_excel_path = os.path.join(current_dir, '../../output/Weekly_Schedule_Summary.xlsx')
        sched_df_excel.to_excel(summary_excel_path, index=False)

        daily_summary = sched_df.groupby(['driver', 'driver_label', 'day']).agg(
            總站數=('node_id', 'count'),
            總服務時間_分=('service_time_min', 'sum'),
            總車程_分=('travel_time_min', 'sum'),
            總里程_km=('travel_dist_km', 'sum')
        ).reset_index()

        daily_summary['總工時_分'] = daily_summary['總服務時間_分'] + daily_summary['總車程_分']
        daily_summary = daily_summary.rename(columns={'driver': '司機', 'driver_label': '司機標籤', 'day': '天數'})
        daily_excel_path = os.path.join(current_dir, '../../output/Daily_Route_Summary.xlsx')
        daily_summary.to_excel(daily_excel_path, index=False)
    else:
        pd.DataFrame().to_excel(os.path.join(current_dir, '../../output/Weekly_Schedule_Summary.xlsx'), index=False)
        pd.DataFrame().to_excel(os.path.join(current_dir, '../../output/Daily_Route_Summary.xlsx'), index=False)

    # 額外輸出未排入
    unassigned_rows = []
    for t in unassigned_tasks:
        unassigned_rows.append({
            'task_id': t['task_id'],
            'node_id': t['node_id'],
            'depot': t['depot'],
            'county': t['county'],
            'address': t['address'],
            'service_time': round(t['service_time'], 2),
            'freq': t['freq'],
            'visit_idx': t['visit_idx'],
            'lat': t['lat'],
            'lon': t['lon'],
        })

    unassigned_df = pd.DataFrame(unassigned_rows)
    unassigned_excel_path = os.path.join(current_dir, '../../output/Weekly_Unassigned_Strict.xlsx')
    unassigned_df.to_excel(unassigned_excel_path, index=False)

    balance_rows = []
    for depot in ['Wugu', 'Pingzhen']:
        for d in driver_names[depot]:
            balance_rows.append({
                'driver': d,
                'driver_label': driver_label(d),
                'weekly_total_min': round(driver_week_load[d], 2),
                'used_days': driver_used_days[d],
                'avg_per_used_day_min': round(driver_week_load[d] / driver_used_days[d], 2) if driver_used_days[d] else 0,
                'depot': depot,
            })
    balance_df = pd.DataFrame(balance_rows)
    balance_excel_path = os.path.join(current_dir, '../../output/Driver_Weekly_Load_Strict.xlsx')
    balance_df.to_excel(balance_excel_path, index=False)

    print("Done! Saved Weekly_Schedule_Summary.xlsx, Daily_Route_Summary.xlsx, Weekly_Routing_Map.html")
    print("Also saved Weekly_Unassigned_Strict.xlsx and Driver_Weekly_Load_Strict.xlsx")

    print("Exporting route data to JSON...")
    json_path = os.path.abspath(os.path.join(current_dir, '../../output/routes_new.json'))
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(schedule, f, ensure_ascii=False, indent=2)
    print(f" routes_new.json generated (at {json_path})")

    unassigned_json_path = os.path.abspath(os.path.join(current_dir, '../../output/routes_unassigned_strict.json'))
    with open(unassigned_json_path, 'w', encoding='utf-8') as f:
        json.dump(unassigned_rows, f, ensure_ascii=False, indent=2)
    print(f" routes_unassigned_strict.json generated (at {unassigned_json_path})")

    cross_county_routes = 0
    overtime_routes = 0
    grouped = defaultdict(list)
    for s in schedule:
        grouped[(s['driver'], s['day'])].append(s)

    for _, items in grouped.items():
        items = sorted(items, key=lambda x: x['seq'])
        counties = sorted(set(i['county'] for i in items))
        total_service = sum(i['service_time_min'] for i in items)
        total_drive = items[-1]['travel_time_min'] if items else 0
        total_time = total_service + total_drive

        if len(counties) > 1:
            cross_county_routes += 1
        if total_time > MAX_MINUTES:
            overtime_routes += 1

    print("\n========== STRICT SUMMARY ==========")
    print(f"Routes used: {len(grouped)}")
    print(f"Assigned tasks: {len(schedule)}")
    print(f"Unassigned tasks: {len(unassigned_tasks)}")
    print(f"Cross-county routes: {cross_county_routes}")
    print(f"Overtime routes: {overtime_routes}")
    print("====================================")


def run_scheduler():
    main()


if __name__ == "__main__":
    main()