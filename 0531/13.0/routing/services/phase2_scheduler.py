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
from pathlib import Path
import sys

try:
    from .driver_roster import build_schedule_driver_slots, schedule_sort_key
except ImportError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from routing.services.driver_roster import build_schedule_driver_slots, schedule_sort_key


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

def driver_sort_key(driver_code, schedule_slot):
    """用來排序司機與排程席位的函數"""
    slot_priority = {'P': 1, 'W': 0}  # 優先順序，'W' 優先於 'P'
    return (slot_priority.get(schedule_slot[0], 9), driver_code)

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
            cur_pt = route_pts[k]
            next_pt = route_pts[k + 1] if k + 1 < len(route_pts) else None

            saved_dist = haversine(prev_pt['lat'], prev_pt['lon'], cur_pt['lat'], cur_pt['lon']) * 1.3
            if next_pt:
                saved_dist += haversine(cur_pt['lat'], cur_pt['lon'], next_pt['lat'], next_pt['lon']) * 1.3
                saved_dist -= haversine(prev_pt['lat'], prev_pt['lon'], next_pt['lat'], next_pt['lon']) * 1.3

            saved_time = (saved_dist / 40.0) * 60.0 + cur_pt['service_time']
            if saved_time > max_saved_time:
                max_saved_time = saved_time
                best_drop_idx = k - 1

        if best_drop_idx >= 0:
            current_route_tasks.pop(best_drop_idx)
        else:
            return None

    return None


def build_unassigned_points_export(df, unassigned_tasks, output_dir, export_name, variant_key, variant_label, download_key):
    node_lookup = {}
    total_db_points = 0

    for _, row in df.iterrows():
        node_id = clean_text(row.get('Node_ID'))
        if not node_id:
            continue
        node_lookup[node_id] = row
        order_count = max(1, to_int(row.get('Order_Count'), 1))
        total_db_points += order_count

    unassigned_node_ids = sorted({clean_text(task.get('node_id')) for task in unassigned_tasks if clean_text(task.get('node_id'))})
    export_rows = []
    unassigned_db_points = 0

    for node_id in unassigned_node_ids:
        row = node_lookup.get(node_id)
        if row is None:
            continue

        order_count = max(1, to_int(row.get('Order_Count'), 1))
        original_names = [name.strip() for name in str(row.get('Original_ID') or '').split(' | ') if name.strip()]
        if not original_names:
            original_names = [node_id]

        missing_visit_count = sum(1 for task in unassigned_tasks if clean_text(task.get('node_id')) == node_id)
        weekly_1 = to_int(row.get('weekly_1'), 1)
        weekly_2 = to_int(row.get('weekly_2'), 0)
        required_visits = max(1, weekly_1 + weekly_2)

        unassigned_db_points += max(order_count, len(original_names))

        for idx, original_name in enumerate(original_names, start=1):
            export_rows.append(
                {
                    'node_id': node_id,
                    'original_point_seq': idx,
                    'original_point_name': original_name,
                    'address': clean_text(row.get('Address')),
                    'depot_raw': clean_text(row.get('Depot_Raw')),
                    'freq': clean_text(row.get('Freq')),
                    'required_visits': required_visits,
                    'missing_visit_count': missing_visit_count,
                    'order_count_in_node': order_count,
                    'lat': to_float(row.get('Lat'), None),
                    'lon': to_float(row.get('Lon'), None),
                }
            )

    unassigned_df = pd.DataFrame(export_rows)
    export_path = output_dir / export_name
    unassigned_df.to_excel(export_path, index=False)

    summary = {
        'variant': variant_key,
        'label': variant_label,
        'total_db_points': total_db_points,
        'scheduled_db_points': max(total_db_points - unassigned_db_points, 0),
        'unassigned_db_points': unassigned_db_points,
        'unassigned_node_count': len(unassigned_node_ids),
        'assigned_task_count': 0,
        'unassigned_task_count': len(unassigned_tasks),
        'unassigned_download_key': download_key,
        'unassigned_download_filename': export_name,
        'summary_message': (
            f"重排完成：已排入 {max(total_db_points - unassigned_db_points, 0)} 個點位，"
            f"未排入 {unassigned_db_points} 個點位。"
        ),
    }
    return summary, unassigned_df


def build_output_payload(routes, variant_key, variant_label, summary_meta, note):
    return {
        'meta': {
            'variant': variant_key,
            'label': variant_label,
            'note': note,
            **summary_meta,
        },
        'routes': routes,
    }


def generate_dashboard_excel(routes, output_path):
    route_summary = []
    route_stops = []

    for route in routes:
        metrics = route.get('metrics', {})
        route_summary.append(
            {
                'route_id': route.get('route_id'),
                'driver': route.get('driver'),
                'driver_label': route.get('driver_label'),
                'schedule_slot': route.get('schedule_slot'),
                'day': route.get('day'),
                'depot_name': route.get('depot', {}).get('name'),
                'stop_count': route.get('stop_count'),
                'counties': " / ".join(route.get('counties', [])),
                'cross_county': route.get('cross_county'),
                'service_min': metrics.get('service_min'),
                'drive_min': metrics.get('drive_min'),
                'dist_km': metrics.get('dist_km'),
                'total_min': metrics.get('total_min'),
                'overtime_min': metrics.get('overtime_min'),
            }
        )

        for stop in route.get('stops', []):
            route_stops.append(
                {
                    'route_id': route.get('route_id'),
                    'driver': route.get('driver'),
                    'driver_label': route.get('driver_label'),
                    'schedule_slot': route.get('schedule_slot'),
                    'day': route.get('day'),
                    'seq': stop.get('seq'),
                    'task_id': stop.get('task_id'),
                    'node_id': stop.get('node_id'),
                    'county': stop.get('county'),
                    'address': stop.get('address'),
                    'service_min': stop.get('service_min'),
                    'travel_time_min': stop.get('travel_time_min'),
                    'travel_dist_km': stop.get('travel_dist_km'),
                    'lat': stop.get('lat'),
                    'lon': stop.get('lon'),
                }
            )

    summary_df = pd.DataFrame(route_summary)
    stops_df = pd.DataFrame(route_stops)

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        summary_df.to_excel(writer, sheet_name='route_summary', index=False)
        stops_df.to_excel(writer, sheet_name='route_stops', index=False)


def create_map(routes, file_name, title_prefix):
    if not routes:
        return

    all_coords = []
    for route in routes:
        depot = route.get('depot', {})
        if depot.get('lat') is not None and depot.get('lon') is not None:
            all_coords.append((depot['lat'], depot['lon']))
        for stop in route.get('stops', []):
            if stop.get('lat') is not None and stop.get('lon') is not None:
                all_coords.append((stop['lat'], stop['lon']))

    if not all_coords:
        return

    center_lat = sum([c[0] for c in all_coords]) / len(all_coords)
    center_lon = sum([c[1] for c in all_coords]) / len(all_coords)

    m = folium.Map(location=[center_lat, center_lon], zoom_start=10)
    colors = [
        'red', 'blue', 'green', 'purple', 'orange', 'darkred', 'lightred', 'beige',
        'darkblue', 'darkgreen', 'cadetblue', 'darkpurple', 'pink', 'lightblue',
        'lightgreen', 'gray', 'black'
    ]

    for idx, route in enumerate(routes):
        color = colors[idx % len(colors)]
        driver = route.get('driver')
        day = route.get('day')
        driver_text = route.get('driver_label') or driver_label(driver)

        depot = route.get('depot', {})
        depot_lat = depot.get('lat')
        depot_lon = depot.get('lon')
        if depot_lat is None or depot_lon is None:
            continue

        folium.Marker(
            [depot_lat, depot_lon],
            tooltip=f"{driver_text} Day {day} 出發",
            icon=folium.Icon(color='black', icon='home')
        ).add_to(m)

        route_coords = [(depot_lat, depot_lon)]

        for stop in route.get('stops', []):
            lat = stop.get('lat')
            lon = stop.get('lon')
            if lat is None or lon is None:
                continue

            route_coords.append((lat, lon))
            popup = (
                f"<b>{driver_text} Day {day}</b><br>"
                f"Seq: {stop.get('seq')}<br>"
                f"Node: {stop.get('node_id')}<br>"
                f"County: {stop.get('county')}<br>"
                f"Address: {stop.get('address')}<br>"
                f"Service: {stop.get('service_min')} min<br>"
                f"Drive: {stop.get('travel_time_min')} min"
            )
            folium.CircleMarker(
                [lat, lon],
                radius=6,
                color=color,
                fill=True,
                fill_opacity=0.85,
                popup=popup,
            ).add_to(m)

        line = folium.PolyLine(route_coords, color=color, weight=4, opacity=0.8)
        line.add_to(m)

    title_html = f'''
         <div style="position: fixed;
                     top: 8px; left: 50px; z-index:9999;
                     font-size:18px; background: rgba(255,255,255,0.9);
                     padding: 8px 12px; border-radius: 8px; font-weight: bold;">
             {title_prefix}
         </div>
    '''
    m.get_root().html.add_child(folium.Element(title_html))
    m.save(file_name)


def main():
    base_dir = Path(__file__).resolve().parent.parent.parent
    output_dir = base_dir / 'output'
    output_dir.mkdir(exist_ok=True)

    csv_path = output_dir / 'processed_nodes_phase1.csv'
    if not csv_path.exists():
        raise FileNotFoundError(f'找不到 {csv_path}')

    df = pd.read_csv(csv_path)
    osrm = OSRMClient()

    tasks = []
    for _, row in df.iterrows():
        node_id = clean_text(row.get('Node_ID'))
        if not node_id:
            continue

        county = parse_county(row.get('Address', ''))
        depot = get_depot(row.get('Depot_Raw'))
        lat = to_float(row.get('Lat'))
        lon = to_float(row.get('Lon'))
        if lat is None or lon is None:
            continue

        weekly_1 = to_int(row.get('weekly_1'), 1)
        weekly_2 = to_int(row.get('weekly_2'), 0)
        visits = max(1, weekly_1 + weekly_2)

        total_service_time = to_float(row.get('Service_Time'), 0.0)
        service_time_each = total_service_time / visits if visits else total_service_time

        for visit_idx in range(1, visits + 1):
            tasks.append(
                {
                    'task_id': f"{node_id}-V{visit_idx}",
                    'node_id': node_id,
                    'lat': lat,
                    'lon': lon,
                    'service_time': round(service_time_each, 2),
                    'county': county,
                    'depot': depot,
                    'address': clean_text(row.get('Address')),
                    'freq': clean_text(row.get('Freq')),
                }
            )

    print(f"Total tasks generated: {len(tasks)}")

    depots = {
        'Wugu': {'lat': 25.07154, 'lon': 121.44169, 'name': '五股總部'},
        'Pingzhen': {'lat': 24.90703, 'lon': 121.226872, 'name': '平鎮總部'},
    }

    route_slots = []
    for roster_item in build_schedule_driver_slots():
        for day in range(1, DAY_COUNT + 1):
            route_slots.append(
                {
                    'driver': roster_item['driver_code'],
                    'driver_label': roster_item['driver_label'],
                    'schedule_slot': roster_item['slot_code'],
                    'day': day,
                    'depot': roster_item['depot_code'],
                    'tasks': [],
                    'route_geometry': None,
                    'metrics': {
                        'service_min': 0,
                        'drive_min': 0,
                        'dist_km': 0,
                        'total_min': 0,
                        'overtime_min': 0,
                    },
                    'counties': set(),
                    'cross_county': False,
                }
            )

    unassigned_tasks = []
    assigned_node_per_day = defaultdict(set)

    counties_by_depot = defaultdict(list)
    for task in tasks:
        counties_by_depot[(task['depot'], task['county'])].append(task)

    for (depot_code, county), task_list in sorted(counties_by_depot.items(), key=lambda x: (x[0][0], x[0][1])):
        depot_info = depots.get(depot_code)
        if not depot_info:
            unassigned_tasks.extend(task_list)
            continue

        remaining_tasks = sorted(task_list, key=lambda x: (-x['service_time'], x['task_id']))
        available_routes = [r for r in route_slots if r['depot'] == depot_code]

        while remaining_tasks:
            best_route_idx = None
            best_candidate = None

            available_routes_sorted = sorted(
                available_routes,
                key=lambda r: (driver_sort_key(r.get('driver'), r.get('schedule_slot')), r.get('day', 0))
            )

            for route in available_routes_sorted:
                filtered_candidates = [
                    t for t in remaining_tasks
                    if t['node_id'] not in assigned_node_per_day[route['day']]
                ]

                if not filtered_candidates:
                    continue

                candidate = build_candidate_route_for_county(
                    filtered_candidates,
                    depot_info['lat'],
                    depot_info['lon'],
                    osrm
                )

                if candidate is None:
                    continue

                if best_candidate is None or candidate['total_time'] < best_candidate['total_time']:
                    best_candidate = candidate
                    best_route_idx = route

            if best_candidate is None:
                unassigned_tasks.extend(remaining_tasks)
                break

            chosen_route = best_route_idx
            chosen_route['tasks'].extend(best_candidate['tasks'])
            chosen_route['route_geometry'] = best_candidate['route_res']['geometry']
            chosen_route['metrics'] = {
                'service_min': round(best_candidate['total_service'], 2),
                'drive_min': round(best_candidate['osrm_duration'], 2),
                'dist_km': round(best_candidate['route_res']['distance'], 2),
                'total_min': round(best_candidate['total_time'], 2),
                'overtime_min': round(max(0, best_candidate['total_time'] - MAX_MINUTES), 2),
            }
            chosen_route['counties'].add(county)
            chosen_route['cross_county'] = len(chosen_route['counties']) > 1

            assigned_task_ids = {t['task_id'] for t in best_candidate['tasks']}
            for task in best_candidate['tasks']:
                assigned_node_per_day[chosen_route['day']].add(task['node_id'])

            remaining_tasks = [t for t in remaining_tasks if t['task_id'] not in assigned_task_ids]

    routes = []
    flat_rows = []
    for route in sorted(route_slots, key=lambda r: (driver_sort_key(r.get('driver'), r.get('schedule_slot')), r.get('day', 0))):
        if not route['tasks']:
            continue

        depot_info = depots[route['depot']]
        coords = [(depot_info['lat'], depot_info['lon'])] + [(t['lat'], t['lon']) for t in route['tasks']]
        route_res = osrm.get_route_batch(coords)

        prev_lat, prev_lon = depot_info['lat'], depot_info['lon']
        stops = []

        for seq, task in enumerate(route['tasks'], start=1):
            leg_dist = haversine(prev_lat, prev_lon, task['lat'], task['lon']) * 1.3
            leg_min = (leg_dist / 40.0) * 60.0

            stop = {
                'seq': seq,
                'task_id': task['task_id'],
                'node_id': task['node_id'],
                'county': task['county'],
                'address': task['address'],
                'lat': task['lat'],
                'lon': task['lon'],
                'service_min': round(task['service_time'], 2),
                'travel_time_min': round(leg_min, 2),
                'travel_dist_km': round(leg_dist, 2),
            }
            stops.append(stop)

            flat_rows.append(
                {
                    'driver': route['driver'],
                    'driver_label': route.get('driver_label') or driver_label(route['driver']),
                    'schedule_slot': route.get('schedule_slot'),
                    'day': route['day'],
                    'seq': seq,
                    'task_id': task['task_id'],
                    'node_id': task['node_id'],
                    'county': task['county'],
                    'address': task['address'],
                    'service_time_min': round(task['service_time'], 2),
                    'travel_time_min': round(leg_min, 2),
                    'travel_dist_km': round(leg_dist, 2),
                    'lat': task['lat'],
                    'lon': task['lon'],
                    'depot_code': route['depot'],
                }
            )

            prev_lat, prev_lon = task['lat'], task['lon']

        route_metrics = route['metrics']
        route_payload = {
            'route_id': f"{route['driver']}-D{route['day']}",
            'driver': route['driver'],
            'driver_label': route.get('driver_label') or driver_label(route['driver']),
            'schedule_slot': route.get('schedule_slot'),
            'day': route['day'],
            'depot': {
                'code': route['depot'],
                'name': depot_info['name'],
                'lat': depot_info['lat'],
                'lon': depot_info['lon'],
            },
            'stop_count': len(stops),
            'counties': sorted(route['counties']),
            'cross_county': route['cross_county'],
            'metrics': route_metrics,
            'stops': stops,
            'geometry': route_res.get('geometry'),
        }
        routes.append(route_payload)

    routes_normal_path = output_dir / 'routes_normal.json'
    normal_summary_path = output_dir / 'normal_schedule_summary.json'

    unassigned_summary, unassigned_df = build_unassigned_points_export(
        df=df,
        unassigned_tasks=unassigned_tasks,
        output_dir=output_dir,
        export_name='Unassigned_Points_normal.xlsx',
        variant_key='normal',
        variant_label='不跨縣市',
        download_key='unassigned_normal',
    )
    unassigned_summary['assigned_task_count'] = len(flat_rows)

    payload = build_output_payload(
        routes=routes,
        variant_key='normal',
        variant_label='不跨縣市',
        summary_meta=unassigned_summary,
        note='一般版排程，由 phase2_scheduler.py 產生。',
    )
    routes_normal_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    normal_summary_path.write_text(
        json.dumps(unassigned_summary, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    summary_rows = []
    for route in routes:
        metrics = route['metrics']
        summary_rows.append(
            {
                'route_id': route['route_id'],
                'driver': route['driver'],
                'driver_label': route['driver_label'],
                'schedule_slot': route.get('schedule_slot'),
                'day': route['day'],
                'depot_name': route['depot']['name'],
                'stop_count': route['stop_count'],
                'counties': " / ".join(route['counties']),
                'cross_county': route['cross_county'],
                'service_min': metrics['service_min'],
                'drive_min': metrics['drive_min'],
                'dist_km': metrics['dist_km'],
                'total_min': metrics['total_min'],
                'overtime_min': metrics['overtime_min'],
            }
        )

    weekly_df = pd.DataFrame(summary_rows)
    daily_df = pd.DataFrame(flat_rows)
    weekly_df.to_excel(output_dir / 'Weekly_Schedule_Summary_normal.xlsx', index=False)
    daily_df.to_excel(output_dir / 'Daily_Route_Summary_normal.xlsx', index=False)

    dashboard_excel = output_dir / 'Dispatch_Report_Latest.xlsx'
    generate_dashboard_excel(routes, dashboard_excel)

    create_map(routes, str(base_dir / 'map_new.html'), '新路線總覽')

    print("=== Scheduling completed ===")
    print(f"Routes generated: {len(routes)}")
    print(f"Assigned tasks: {len(flat_rows)}")
    print(f"Unassigned tasks: {len(unassigned_tasks)}")
    print(unassigned_summary['summary_message'])


if __name__ == '__main__':
    main()