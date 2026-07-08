import pandas as pd
import folium
import re
import requests
import json
import os
import sqlite3
import math
import folium.plugins
import time
from collections import defaultdict

try:
    from routing.services.routing_cost_provider import RoutingCostProvider
except ImportError:
    from routing_cost_provider import RoutingCostProvider


def env_int(name, default):
    try:
        value = os.environ.get(name)
        if value is None or str(value).strip() == "":
            return default
        return max(int(float(value)), 1)
    except Exception:
        return default


def env_float(name, default=None):
    try:
        value = os.environ.get(name)
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except Exception:
        return default


DAY_COUNT = env_int("DISPATCH_SCHEDULE_DAYS", 6)
MAX_MINUTES = env_int("DISPATCH_DAILY_WORK_MINUTES", 540)
DEFAULT_SERVICE_MINUTES = env_int("DISPATCH_DEFAULT_SERVICE_MINUTES", 10)
SAMPLE_NODES = env_int("DISPATCH_SAMPLE_NODES", 0) if os.environ.get("DISPATCH_SAMPLE_NODES") else 0
OSRM_ROUTE_TIMEOUT = env_float("DISPATCH_OSRM_ROUTE_TIMEOUT", 12)
TWO_OPT_MAX_ITERATIONS = env_int("DISPATCH_TWO_OPT_MAX_ITERATIONS", 20)
TWO_OPT_MAX_SECONDS = env_float("DISPATCH_TWO_OPT_MAX_SECONDS", 15)
MEMORY_FALLBACK_CANDIDATE_THRESHOLD = env_int("DISPATCH_MEMORY_FALLBACK_CANDIDATE_THRESHOLD", 80)
NN_PREFILTER_THRESHOLD = env_int("DISPATCH_NN_PREFILTER_THRESHOLD", 200)
NN_MAX_SCAN_CANDIDATES = env_int("DISPATCH_NN_MAX_SCAN_CANDIDATES", 200)


def log(message):
    print(f"[phase2-normal] {message}", flush=True)


class OSRMClient:
    def __init__(self, cache_file='../osrm_cache.db'):
        cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), cache_file)
        self.conn = sqlite3.connect(cache_path)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS batch_cache
            (hash TEXT PRIMARY KEY, duration REAL, distance REAL, geometry TEXT)
        ''')
        self.cursor.execute("PRAGMA table_info(batch_cache)")
        columns = {row[1] for row in self.cursor.fetchall()}
        if 'legs' not in columns:
            self.cursor.execute("ALTER TABLE batch_cache ADD COLUMN legs TEXT")
        self.conn.commit()

    def get_route_batch(self, coords):
        if len(coords) < 2:
            return {'duration': 0, 'distance': 0, 'geometry': None, 'legs': [], 'source': 'Empty Route'}

        import hashlib
        c_str = "|".join([f"{round(lat,5)},{round(lon,5)}" for lat, lon in coords])
        c_hash = hashlib.md5(c_str.encode()).hexdigest()

        self.cursor.execute("SELECT duration, distance, geometry, legs FROM batch_cache WHERE hash=?", (c_hash,))
        row = self.cursor.fetchone()
        if row:
            log(f"OSRM Route cache hit: stops={len(coords) - 1}")
            return {
                'duration': row[0],
                'distance': row[1],
                'geometry': json.loads(row[2]) if row[2] else None,
                'legs': json.loads(row[3]) if row[3] else [],
                'source': 'Route Cache',
            }

        coord_string = ";".join([f"{lon},{lat}" for lat, lon in coords])
        url = f"http://router.project-osrm.org/route/v1/driving/{coord_string}?overview=full&geometries=geojson"

        try:
            log(f"OSRM Route 驗證開始: stops={len(coords) - 1}, timeout={OSRM_ROUTE_TIMEOUT}s")
            resp = requests.get(url, timeout=OSRM_ROUTE_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                if data.get('code') == 'Ok' and data.get('routes'):
                    route = data['routes'][0]
                    duration_min = route['duration'] / 60.0
                    distance_km = route['distance'] / 1000.0
                    geometry = route['geometry']
                    legs = route.get('legs', [])

                    self.cursor.execute(
                        """
                        INSERT OR REPLACE INTO batch_cache
                        (hash, duration, distance, geometry, legs)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (c_hash, duration_min, distance_km, json.dumps(geometry), json.dumps(legs))
                    )
                    self.conn.commit()
                    log(
                        f"OSRM Route 驗證完成: distance={distance_km:.2f}km, "
                        f"duration={duration_min:.2f}min, legs={len(legs)}"
                    )
                    return {
                        'duration': duration_min,
                        'distance': distance_km,
                        'geometry': geometry,
                        'legs': legs,
                        'source': 'OSRM Route',
                    }
                log(f"OSRM Route 回應不可用: code={data.get('code')} message={data.get('message', '')}")
            else:
                log(f"OSRM Route HTTP 失敗: status={resp.status_code}")
        except Exception as e:
            log(f"OSRM Route Error: {type(e).__name__}: {e}; 改用 Haversine fallback")

        # fallback: haversine
        fallback_legs = []
        dist_km = 0.0
        for i in range(len(coords) - 1):
            leg_dist = haversine(coords[i][0], coords[i][1], coords[i+1][0], coords[i+1][1]) * 1.3
            leg_duration = (leg_dist / 40.0) * 60.0
            dist_km += leg_dist
            fallback_legs.append({'distance': leg_dist * 1000.0, 'duration': leg_duration * 60.0})
        duration_min = (dist_km / 40.0) * 60.0
        geom = {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in coords]}
        return {
            'duration': duration_min,
            'distance': dist_km,
            'geometry': geom,
            'legs': fallback_legs,
            'source': 'Haversine Route Fallback',
        }


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
    custom_name = os.environ.get('DISPATCH_DEPOT_NAME') if os.environ.get('DISPATCH_DEPOT_LAT') and os.environ.get('DISPATCH_DEPOT_LON') else ''
    s = str(code or '').upper()
    if s.startswith('P') and s[1:].isdigit():
        if custom_name:
            return f"{s}｜{custom_name}{s[1:].lstrip('0') or '0'}"
        return f"{s}｜平鎮{s[1:].lstrip('0') or '0'}"
    if s.startswith('W') and s[1:].isdigit():
        return f"{s}｜五股{s[1:].lstrip('0') or '0'}"
    return s


def coord_of(item):
    return (float(item['lat']), float(item['lon']))


def coord_cache_key(coord):
    return (round(float(coord[0]), 5), round(float(coord[1]), 5))


def cheap_distance_from(coord, item):
    return haversine(coord[0], coord[1], float(item['lat']), float(item['lon']))


def make_local_cost_getter(cost_provider, persist_fallback=True):
    local_cache = {}
    stats = {"calls": 0, "local_hits": 0}

    def get_cost(origin, dest):
        key = (coord_cache_key(origin), coord_cache_key(dest))
        if key in local_cache:
            stats["local_hits"] += 1
            return local_cache[key]
        stats["calls"] += 1
        cost = cost_provider.get_cost(origin, dest, persist_fallback=persist_fallback)
        local_cache[key] = cost
        return cost

    return get_cost, stats, local_cache


def build_two_opt_cost_cache(route_pts, get_cost, max_seconds):
    started = time.time()
    pair_costs = {}
    cost_calls = 0
    precompute_timeout = False

    coords = [coord_of(pt) for pt in route_pts]
    for origin in coords:
        for dest in coords:
            if time.time() - started > max_seconds:
                precompute_timeout = True
                return pair_costs, cost_calls, precompute_timeout
            key = (origin, dest)
            if key in pair_costs:
                continue
            if origin == dest:
                pair_costs[key] = 0.0
                continue
            pair_costs[key] = get_cost(origin, dest)['duration']
            cost_calls += 1

    return pair_costs, cost_calls, precompute_timeout


def run_two_opt(route_pts, get_cost, context_label='', cost_stats=None):
    label = f" ({context_label})" if context_label else ""
    started = time.time()
    max_seconds = TWO_OPT_MAX_SECONDS if TWO_OPT_MAX_SECONDS and TWO_OPT_MAX_SECONDS > 0 else 15
    max_iterations = max(TWO_OPT_MAX_ITERATIONS, 1)

    pair_costs, two_opt_cost_calls, precompute_timeout = build_two_opt_cost_cache(
        route_pts,
        get_cost,
        max_seconds=max_seconds,
    )

    def pair_duration(a, b):
        nonlocal two_opt_cost_calls
        origin = coord_of(a)
        dest = coord_of(b)
        key = (origin, dest)
        if key not in pair_costs:
            pair_costs[key] = get_cost(origin, dest)['duration']
            two_opt_cost_calls += 1
        return pair_costs[key]

    def current_path_cost():
        return sum(pair_duration(route_pts[idx], route_pts[idx + 1]) for idx in range(len(route_pts) - 1))

    iterations = 0
    improvements = 0
    stopped_by = ''
    improved = True

    while improved:
        elapsed = time.time() - started
        if elapsed >= max_seconds:
            stopped_by = 'timeout'
            break
        if iterations >= max_iterations:
            stopped_by = 'max_iterations'
            break

        iterations += 1
        improved = False

        for i in range(1, len(route_pts) - 1):
            if time.time() - started >= max_seconds:
                stopped_by = 'timeout'
                break
            for j in range(i + 1, len(route_pts)):
                if time.time() - started >= max_seconds:
                    stopped_by = 'timeout'
                    break
                if j - i == 1:
                    continue

                cur_dist = (
                    pair_duration(route_pts[i - 1], route_pts[i]) +
                    pair_duration(route_pts[j - 1], route_pts[j])
                )
                new_dist = (
                    pair_duration(route_pts[i - 1], route_pts[j - 1]) +
                    pair_duration(route_pts[i], route_pts[j])
                )

                if new_dist < cur_dist - 0.001:
                    route_pts[i:j] = route_pts[i:j][::-1]
                    improved = True
                    improvements += 1
            if stopped_by:
                break

    final_cost = current_path_cost() if route_pts else 0.0
    elapsed = time.time() - started
    provider_calls = cost_stats["calls"] if cost_stats else two_opt_cost_calls
    local_hits = cost_stats["local_hits"] if cost_stats else 0
    log(
        f"2-Opt 完成{label}: stops={len(route_pts) - 1}, iterations={iterations}, "
        f"improvements={improvements}, elapsed={elapsed:.2f}s, "
        f"two_opt_cost_lookups={two_opt_cost_calls}, cost_provider_calls={provider_calls}, "
        f"local_cache_hits={local_hits}, final_cost={final_cost:.2f}, "
        f"stopped_by={stopped_by or 'completed'}, precompute_timeout={precompute_timeout}"
    )
    return route_pts


def build_candidate_route_for_county(task_candidates, depot_lat, depot_lon, osrm, cost_provider, context_label=''):
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

    label = f" ({context_label})" if context_label else ""
    log(f"候選路線開始{label}: candidates={len(task_candidates)}")
    depot_coord = (depot_lat, depot_lon)
    log(f"OSRM Table 預熱開始{label}: locations={len(task_candidates) + 1}")
    cost_provider.warm_costs([depot_coord] + [(t['lat'], t['lon']) for t in task_candidates])
    log(f"OSRM Table 預熱完成{label}: stats={cost_provider.stats_json()}")
    persist_fallback = len(task_candidates) + 1 <= MEMORY_FALLBACK_CANDIDATE_THRESHOLD
    get_local_cost, local_cost_stats, local_cost_cache = make_local_cost_getter(
        cost_provider,
        persist_fallback=persist_fallback,
    )
    if not persist_fallback:
        log(
            f"大型候選群組啟用記憶體 fallback{label}: candidates={len(task_candidates)}, "
            f"threshold={MEMORY_FALLBACK_CANDIDATE_THRESHOLD}"
        )

    # 1) 先用 OSRM Table/cache greedy 建初始路線
    log(f"NN/Greedy 開始{label}")
    current_route_tasks = []
    current_lat, current_lon = depot_lat, depot_lon
    est_day_total = 0.0
    remaining = list(task_candidates)
    nn_scanned_candidates = 0
    nn_prefilter_rounds = 0
    nn_full_scan_rounds = 0
    nn_started = time.time()

    while remaining:
        current_coord = (current_lat, current_lon)
        indexed_remaining = list(enumerate(remaining))
        used_prefilter = (
            NN_MAX_SCAN_CANDIDATES > 0
            and len(remaining) > NN_PREFILTER_THRESHOLD
            and len(remaining) > NN_MAX_SCAN_CANDIDATES
        )
        if used_prefilter:
            indexed_remaining = sorted(
                indexed_remaining,
                key=lambda pair: cheap_distance_from(current_coord, pair[1])
            )[:NN_MAX_SCAN_CANDIDATES]
            nn_prefilter_rounds += 1
        else:
            nn_full_scan_rounds += 1

        ordered_candidates = sorted(
            indexed_remaining,
            key=lambda pair: get_local_cost(current_coord, coord_of(pair[1]))['duration']
        )
        nn_scanned_candidates += len(ordered_candidates)

        cand = None
        cand_idx = -1
        best_est_time = 0.0

        for idx, t_cand in ordered_candidates:
            if any(r['node_id'] == t_cand['node_id'] for r in current_route_tasks):
                continue

            leg_cost = get_local_cost(current_coord, coord_of(t_cand))
            est_time = leg_cost['duration']

            if est_day_total + est_time + t_cand['service_time'] <= MAX_MINUTES:
                cand = t_cand
                cand_idx = idx
                best_est_time = est_time
                break

        if not cand and used_prefilter:
            nn_full_scan_rounds += 1
            ordered_candidates = sorted(
                enumerate(remaining),
                key=lambda pair: get_local_cost(current_coord, coord_of(pair[1]))['duration']
            )
            nn_scanned_candidates += len(ordered_candidates)
            for idx, t_cand in ordered_candidates:
                if any(r['node_id'] == t_cand['node_id'] for r in current_route_tasks):
                    continue

                leg_cost = get_local_cost(current_coord, coord_of(t_cand))
                est_time = leg_cost['duration']

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
        log(f"NN/Greedy 完成{label}: 無可行站點")
        return None
    log(
        f"NN/Greedy 完成{label}: selected={len(current_route_tasks)}, "
        f"elapsed={time.time() - nn_started:.2f}s, "
        f"scanned_candidates={nn_scanned_candidates}, "
        f"prefilter_rounds={nn_prefilter_rounds}, full_scan_rounds={nn_full_scan_rounds}, "
        f"max_scan={NN_MAX_SCAN_CANDIDATES}, "
        f"cost_provider_calls={local_cost_stats['calls']}, "
        f"local_cache_hits={local_cost_stats['local_hits']}, "
        f"local_cache_size={len(local_cost_cache)}"
    )

    # 2) 2-opt 優化：使用 OSRM Table/cache 的道路時間，不再用直線距離
    log(f"2-Opt 開始{label}: stops={len(current_route_tasks)}")
    route_pts = [{'lat': depot_lat, 'lon': depot_lon}] + current_route_tasks[:]
    route_pts = run_two_opt(route_pts, get_local_cost, context_label=context_label, cost_stats=local_cost_stats)
    current_route_tasks = route_pts[1:]

    # 3) 用 OSRM 精算，若超時則逐步移除邊際成本最高的點
    while current_route_tasks:
        coords = [(depot_lat, depot_lon)] + [(t['lat'], t['lon']) for t in current_route_tasks]
        estimated_route_cost = cost_provider.route_cost(coords)
        route_res = osrm.get_route_batch(coords)
        osrm_duration = route_res['duration']
        total_service = sum(t['service_time'] for t in current_route_tasks)
        total_time = osrm_duration + total_service

        if total_time <= MAX_MINUTES:
            log(
                f"候選路線接受{label}: stops={len(current_route_tasks)}, "
                f"estimated_drive={estimated_route_cost['duration']:.1f}, "
                f"osrm_drive={osrm_duration:.1f}, total={total_time:.1f}, "
                f"source={estimated_route_cost['source']}"
            )
            return {
                'tasks': current_route_tasks,
                'route_res': route_res,
                'osrm_duration': osrm_duration,
                'total_service': total_service,
                'total_time': total_time,
                'estimated_duration': estimated_route_cost['duration'],
                'estimated_distance': estimated_route_cost['distance'],
                'cost_source': estimated_route_cost['source'],
                'used_fallback': estimated_route_cost['used_fallback'],
            }

        # 嚴格版：如果只剩 1 站還超時，不可硬塞
        if len(current_route_tasks) == 1:
            log(f"候選路線放棄{label}: 單站仍超過 {MAX_MINUTES} 分鐘")
            return None

        # 拔掉邊際成本最高的點：使用 OSRM Table/cache 的道路時間
        log(f"候選路線超時{label}: total={total_time:.1f}; 開始移除邊際成本最高點")
        best_drop_idx = -1
        max_saved_time = -1

        route_pts = [{'lat': depot_lat, 'lon': depot_lon}] + current_route_tasks

        for k in range(1, len(route_pts)):
            prev_pt = route_pts[k - 1]
            curr_pt = route_pts[k]

            if k < len(route_pts) - 1:
                next_pt = route_pts[k + 1]
                dist_before = (
                    get_local_cost(coord_of(prev_pt), coord_of(curr_pt))['duration'] +
                    get_local_cost(coord_of(curr_pt), coord_of(next_pt))['duration']
                )
                dist_after = get_local_cost(coord_of(prev_pt), coord_of(next_pt))['duration']
            else:
                dist_before = get_local_cost(coord_of(prev_pt), coord_of(curr_pt))['duration']
                dist_after = 0

            saved_travel_time = dist_before - dist_after
            drop_idx = k - 1
            saved_total_time = saved_travel_time + current_route_tasks[drop_idx]['service_time']

            if saved_total_time > max_saved_time:
                max_saved_time = saved_total_time
                best_drop_idx = drop_idx

        if best_drop_idx != -1:
            dropped = current_route_tasks[best_drop_idx]
            log(f"移除超時點{label}: task={dropped.get('task_id')} saved_est={max_saved_time:.1f}min")
            current_route_tasks.pop(best_drop_idx)
        else:
            log(f"候選路線放棄{label}: 找不到可移除點")
            return None

    return None


def route_leg_values(route_res, stop_count):
    legs = route_res.get('legs') or []
    values = []
    for idx in range(stop_count):
        if idx < len(legs):
            leg = legs[idx]
            values.append({
                'duration': float(leg.get('duration', 0)) / 60.0,
                'distance': float(leg.get('distance', 0)) / 1000.0,
            })
        else:
            duration = route_res.get('duration', 0) / stop_count if stop_count else 0
            distance = route_res.get('distance', 0) / stop_count if stop_count else 0
            values.append({'duration': duration, 'distance': distance})
    return values


def percent_diff(estimated, actual):
    if actual == 0:
        return 0.0 if estimated == 0 else 100.0
    return ((estimated - actual) / actual) * 100.0


def build_diagnostic_row(route_id, driver, route_type, stop_count, estimated_cost, route_res, service_min, note=''):
    estimated_drive = estimated_cost.get('duration', 0.0)
    estimated_distance = estimated_cost.get('distance', 0.0)
    actual_drive = route_res.get('duration', 0.0)
    actual_distance = route_res.get('distance', 0.0)
    return {
        '路線編號': route_id,
        '司機': driver,
        '路線類型': route_type,
        '停靠點數': stop_count,
        '估算距離（公里）': round(estimated_distance, 2),
        'OSRM 真實距離（公里）': round(actual_distance, 2),
        '距離差異（%）': round(percent_diff(estimated_distance, actual_distance), 2),
        '估算行駛時間（分鐘）': round(estimated_drive, 2),
        'OSRM 真實行駛時間（分鐘）': round(actual_drive, 2),
        '行駛時間差異（%）': round(percent_diff(estimated_drive, actual_drive), 2),
        '估算總工時（分鐘）': round(estimated_drive + service_min, 2),
        'OSRM 總工時（分鐘）': round(actual_drive + service_min, 2),
        '估算是否超過540分鐘': '是' if estimated_drive + service_min > MAX_MINUTES else '否',
        'OSRM是否超過540分鐘': '是' if actual_drive + service_min > MAX_MINUTES else '否',
        '成本來源': estimated_cost.get('source', ''),
        '是否使用備援': '是' if estimated_cost.get('used_fallback') else '否',
        '備註': note,
    }


def validate_schedule_strict(schedule):
    issues = []
    grouped = defaultdict(list)

    for s in schedule:
        grouped[(s['driver'], s['day'])].append(s)

    for (driver, day), items in grouped.items():
        items_sorted = sorted(items, key=lambda x: x['seq'])
        counties = sorted(set(i['county'] for i in items_sorted))
        total_service = sum(i['service_time_min'] for i in items_sorted)
        total_drive = sum(i['travel_time_min'] for i in items_sorted)
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

    log(f"開始讀取資料: {input_csv}")
    df = pd.read_csv(input_csv)
    log(f"資料讀取完成: nodes={len(df)}")
    if SAMPLE_NODES:
        original_count = len(df)
        df = df.head(SAMPLE_NODES).copy()
        log(f"小資料測試模式啟用: DISPATCH_SAMPLE_NODES={SAMPLE_NODES}, nodes={len(df)}/{original_count}")

    log("開始解析縣市與倉庫")
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

        service_time_total = to_float(row.get('Service_Time'), DEFAULT_SERVICE_MINUTES)
        if service_time_total <= 0:
            service_time_total = DEFAULT_SERVICE_MINUTES
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
    log(f"任務建立完成: tasks={len(tasks_df)}")

    depot_locations = {
        'Wugu': {'lat': 25.07154, 'lon': 121.44169},
        'Pingzhen': {'lat': 24.90703, 'lon': 121.226872}
    }
    custom_depot_lat = env_float("DISPATCH_DEPOT_LAT")
    custom_depot_lon = env_float("DISPATCH_DEPOT_LON")
    if custom_depot_lat is not None and custom_depot_lon is not None:
        depot_locations['Pingzhen'] = {'lat': custom_depot_lat, 'lon': custom_depot_lon}

    driver_config = {'Wugu': 2, 'Pingzhen': 12}
    driver_limit = env_int("DISPATCH_DRIVER_LIMIT", sum(driver_config.values()))
    if custom_depot_lat is not None and custom_depot_lon is not None:
        driver_config = {'Wugu': 0, 'Pingzhen': driver_limit}
    elif driver_limit != sum(driver_config.values()):
        wugu_count = min(driver_config['Wugu'], driver_limit)
        driver_config = {'Wugu': wugu_count, 'Pingzhen': max(driver_limit - wugu_count, 0)}
    available_depots = {code for code, count in driver_config.items() if count > 0}
    default_depot_code = 'Pingzhen' if 'Pingzhen' in available_depots else next(iter(available_depots), 'Pingzhen')

    if custom_depot_lat is not None and custom_depot_lon is not None and 'Depot' in tasks_df.columns:
        tasks_df['Depot'] = default_depot_code
    elif 'Depot' in tasks_df.columns:
        tasks_df['Depot'] = tasks_df['Depot'].apply(lambda value: value if value in available_depots else default_depot_code)
    driver_names = {
        'Wugu': [f'W{i:02d}' for i in range(1, driver_config['Wugu'] + 1)],
        'Pingzhen': [f'P{i:02d}' for i in range(1, driver_config['Pingzhen'] + 1)],
    }
    driver_week_load = {d: 0.0 for depot in driver_names for d in driver_names[depot]}
    driver_used_days = {d: 0 for depot in driver_names for d in driver_names[depot]}

    log("開始建立 OSRMClient")
    osrm = OSRMClient()
    log("開始建立 RoutingCostProvider")
    cost_provider = RoutingCostProvider()
    schedule = []
    diagnostics = []
    tasks_pool = tasks_df.to_dict('records')
    log(f"主排程開始: depots={driver_config}, days={DAY_COUNT}, max_minutes={MAX_MINUTES}")

    # =========================
    # 主排程：固定倉 + 嚴格不跨縣市 + 不超 540 + 盡量平衡司機週工時
    # =========================
    for depot, num_drivers in driver_config.items():
        depot_lat = depot_locations[depot]['lat']
        depot_lon = depot_locations[depot]['lon']
        log(f"處理倉庫開始: depot={depot}, drivers={num_drivers}")

        for day in range(1, DAY_COUNT + 1):
            log(f"處理日期開始: depot={depot}, day={day}")
            ordered_drivers = sorted(
                driver_names[depot],
                key=lambda d: (driver_week_load[d], driver_used_days[d], d)
            )

            for d_name in ordered_drivers:
                log(f"處理司機開始: driver={d_name}, depot={depot}, day={day}")
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
                    log(f"處理司機跳過: driver={d_name}, day={day}, 無可分配任務")
                    continue
                log(f"可分配任務: driver={d_name}, day={day}, count={len(valid_unassigned)}")

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

                for county_index, (target_county, _) in enumerate(county_candidates, start=1):
                    same_county_tasks = [t for t in valid_unassigned if t['county'] == target_county]
                    log(
                        f"處理縣市候選: driver={d_name}, day={day}, "
                        f"{county_index}/{len(county_candidates)}, county={target_county}, tasks={len(same_county_tasks)}"
                    )

                    plan = build_candidate_route_for_county(
                        task_candidates=same_county_tasks,
                        depot_lat=depot_lat,
                        depot_lon=depot_lon,
                        osrm=osrm,
                        cost_provider=cost_provider,
                        context_label=f"{d_name} Day {day} {target_county}"
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
                    log(f"無可行路線: driver={d_name}, day={day}")
                    continue

                current_route_tasks = best_plan['tasks']
                route_res = best_plan['route_res']
                osrm_duration = best_plan['osrm_duration']
                total_service = best_plan['total_service']
                total_time = best_plan['total_time']
                leg_values = route_leg_values(route_res, len(current_route_tasks))
                route_id = f"{d_name}-Day{day}-{len(diagnostics) + 1}"

                diagnostics.append(build_diagnostic_row(
                    route_id=route_id,
                    driver=d_name,
                    route_type='NORMAL 主排程',
                    stop_count=len(current_route_tasks),
                    estimated_cost={
                        'duration': best_plan.get('estimated_duration', 0),
                        'distance': best_plan.get('estimated_distance', 0),
                        'source': best_plan.get('cost_source', ''),
                        'used_fallback': best_plan.get('used_fallback', False),
                    },
                    route_res=route_res,
                    service_min=total_service,
                    note=f"區域: {best_target_county}"
                ))

                for i, t in enumerate(current_route_tasks):
                    t['assigned'] = True
                    geom_val = route_res['geometry'] if i == len(current_route_tasks) - 1 else None
                    leg_val = leg_values[i] if i < len(leg_values) else {'duration': 0, 'distance': 0}

                    schedule.append({
                        'driver': d_name,
                        'driver_label': driver_label(d_name),
                        'depot_code': depot,
                        'day': day,
                        'seq': i + 1,
                        'task_id': t['task_id'],
                        'node_id': t['node_id'],
                        'county': t['county'],
                        'address': t['address'],
                        'service_time_min': round(t['service_time'], 2),
                        'freq': t['freq'],
                        'visit_idx': t['visit_idx'],
                        'travel_time_min': round(leg_val['duration'], 2),
                        'travel_dist_km': round(leg_val['distance'], 2),
                        'geometry': geom_val,
                        'lat': t['lat'],
                        'lon': t['lon'],
                    })

                driver_week_load[d_name] += total_time
                driver_used_days[d_name] += 1

                log(
                    f"司機 {d_name} 第 {day} 天: {len(current_route_tasks)} 站, "
                    f"總里程 {route_res['distance']:.1f} km, 車程 {osrm_duration:.1f} 分鐘, "
                    f"總工時 {total_time:.1f} 分鐘 (區域: {best_target_county})"
                )
                log(f"Cache 狀態: {cost_provider.stats_json()}")

    # =========================
    # FINAL PASS：只允許同倉、同縣市、且不超 540
    # =========================
    unassigned_pool = [t for t in tasks_pool if not t['assigned']]
    if unassigned_pool:
        log(f"Final Pass 開始: unassigned={len(unassigned_pool)}")

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

        for idx_unassigned, t in enumerate(unassigned_pool, start=1):
            if idx_unassigned == 1 or idx_unassigned % 25 == 0 or idx_unassigned == len(unassigned_pool):
                log(f"Final Pass 進度: {idx_unassigned}/{len(unassigned_pool)}, task={t.get('task_id')}")
            if t['county'] == 'Unknown':
                log(f"Final Pass 跳過: task={t.get('task_id')}, county=Unknown")
                continue
            if t['depot'] not in depot_locations:
                log(f"Final Pass 跳過: task={t.get('task_id')}, depot={t.get('depot')}")
                continue

            best_route_key = None
            best_append_score = float('inf')
            best_osrm_res = None
            best_new_total = None
            best_delta = None
            best_estimated_cost = None

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
                    estimated_cost = cost_provider.route_cost(coords)
                    if estimated_cost['duration'] + t['service_time'] > MAX_MINUTES:
                        continue

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
                            best_estimated_cost = estimated_cost
                    continue

                stasks_sorted = sorted(stasks, key=lambda x: x['seq'])
                current_total = sum(x['travel_time_min'] for x in stasks_sorted) + sum(x['service_time_min'] for x in stasks_sorted)

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

                estimated_cost = cost_provider.route_cost(coords)
                service_total = sum(x['service_time_min'] for x in stasks_sorted) + t['service_time']

                if estimated_cost['duration'] + service_total > MAX_MINUTES:
                    continue

                route_res = osrm.get_route_batch(coords)
                new_total = route_res['duration'] + service_total

                if new_total <= MAX_MINUTES:
                    delta = new_total - current_total
                    score = delta + driver_week_load[driver] * 0.20 + driver_used_days[driver] * 3.0
                    if score < best_append_score:
                        best_append_score = score
                        best_route_key = key
                        best_osrm_res = route_res
                        best_new_total = new_total
                        best_delta = delta
                        best_estimated_cost = estimated_cost

            if best_route_key:
                t['assigned'] = True
                driver = best_route_key[0]

                if not day_routes[best_route_key]:
                    leg_values = route_leg_values(best_osrm_res, 1)
                    leg_val = leg_values[0] if leg_values else {'duration': best_osrm_res['duration'], 'distance': best_osrm_res['distance']}
                    new_item = {
                        'driver': driver,
                        'driver_label': driver_label(driver),
                        'depot_code': 'Wugu' if driver.startswith('W') else 'Pingzhen',
                        'day': best_route_key[1],
                        'seq': 1,
                        'task_id': t['task_id'],
                        'node_id': t['node_id'],
                        'county': t['county'],
                        'address': t['address'],
                        'service_time_min': round(t['service_time'], 2),
                        'freq': t['freq'],
                        'visit_idx': t['visit_idx'],
                        'travel_time_min': round(leg_val['duration'], 2),
                        'travel_dist_km': round(leg_val['distance'], 2),
                        'geometry': best_osrm_res['geometry'],
                        'lat': t['lat'],
                        'lon': t['lon']
                    }
                    schedule.append(new_item)
                    day_routes[best_route_key].append(new_item)
                    driver_week_load[driver] += best_delta
                    driver_used_days[driver] += 1
                    diagnostics.append(build_diagnostic_row(
                        route_id=f"{driver}-Day{best_route_key[1]}-Final-{len(diagnostics) + 1}",
                        driver=driver,
                        route_type='NORMAL Final Pass 空白日',
                        stop_count=1,
                        estimated_cost=best_estimated_cost or {},
                        route_res=best_osrm_res,
                        service_min=t['service_time'],
                        note='未分配點補入'
                    ))
                    log(f"Final Pass 完成補入空白日: task={t['task_id']} -> {best_route_key[0]} Day {best_route_key[1]}")
                else:
                    stasks_sorted = sorted(day_routes[best_route_key], key=lambda x: x['seq'])
                    leg_values = route_leg_values(best_osrm_res, len(stasks_sorted) + 1)
                    for idx, old_item in enumerate(stasks_sorted):
                        old_leg = leg_values[idx] if idx < len(leg_values) else {'duration': 0, 'distance': 0}
                        old_item['travel_time_min'] = round(old_leg['duration'], 2)
                        old_item['travel_dist_km'] = round(old_leg['distance'], 2)
                        old_item['geometry'] = None

                    new_seq = len(stasks_sorted) + 1
                    new_leg = leg_values[-1] if leg_values else {'duration': 0, 'distance': 0}
                    new_item = {
                        'driver': driver,
                        'driver_label': driver_label(driver),
                        'depot_code': 'Wugu' if driver.startswith('W') else 'Pingzhen',
                        'day': best_route_key[1],
                        'seq': new_seq,
                        'task_id': t['task_id'],
                        'node_id': t['node_id'],
                        'county': t['county'],
                        'address': t['address'],
                        'service_time_min': round(t['service_time'], 2),
                        'freq': t['freq'],
                        'visit_idx': t['visit_idx'],
                        'travel_time_min': round(new_leg['duration'], 2),
                        'travel_dist_km': round(new_leg['distance'], 2),
                        'geometry': best_osrm_res['geometry'],
                        'lat': t['lat'],
                        'lon': t['lon']
                    }
                    schedule.append(new_item)
                    day_routes[best_route_key].append(new_item)
                    driver_week_load[driver] += best_delta
                    diagnostics.append(build_diagnostic_row(
                        route_id=f"{driver}-Day{best_route_key[1]}-Final-{len(diagnostics) + 1}",
                        driver=driver,
                        route_type='NORMAL Final Pass 插入既有路線',
                        stop_count=len(stasks_sorted) + 1,
                        estimated_cost=best_estimated_cost or {},
                        route_res=best_osrm_res,
                        service_min=sum(x['service_time_min'] for x in stasks_sorted) + t['service_time'],
                        note='未分配點補入'
                    ))
                    log(f"Final Pass 完成插入既有路線: task={t['task_id']} -> {best_route_key[0]} Day {best_route_key[1]}")
        log(f"Final Pass 完成: stats={cost_provider.stats_json()}")
    else:
        log("Final Pass 跳過: 沒有未分配任務")

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
    for depot_code in available_depots:
        depot_name = os.environ.get("DISPATCH_DEPOT_NAME") if custom_depot_lat is not None and custom_depot_lon is not None else depot_code
        folium.Marker(
            location=[depot_locations[depot_code]['lat'], depot_locations[depot_code]['lon']],
            popup=f"Depot: {depot_name}",
            icon=folium.Icon(color='black', icon='home')
        ).add_to(base_group)

    for d, days_dict in drivers_dict.items():
        depot = 'Wugu' if d.startswith('W') else 'Pingzhen'

        for day, day_group in days_dict.items():
            fg = folium.FeatureGroup(name=f"【新】{d} - Day {day}", show=False)
            day_group = sorted(day_group, key=lambda x: x['seq'])

            cum_time = 0.0
            cum_dist = 0.0

            for index, row in enumerate(day_group):
                cum_dist += row.get('travel_dist_km', 0)
                cum_time += row.get('travel_time_min', 0) + row['service_time_min']

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

    diagnostics.extend([
        {
            '路線編號': 'CROSS',
            '司機': '',
            '路線類型': 'CROSS',
            '停靠點數': '',
            '估算距離（公里）': '',
            'OSRM 真實距離（公里）': '',
            '距離差異（%）': '',
            '估算行駛時間（分鐘）': '',
            'OSRM 真實行駛時間（分鐘）': '',
            '行駛時間差異（%）': '',
            '估算總工時（分鐘）': '',
            'OSRM 總工時（分鐘）': '',
            '估算是否超過540分鐘': '',
            'OSRM是否超過540分鐘': '',
            '成本來源': 'Haversine',
            '是否使用備援': '否',
            '備註': 'phase2_scheduler_cross_county.py 暫未改用 OSRM Table/cache',
        },
        {
            '路線編號': 'COMPACT',
            '司機': '',
            '路線類型': 'COMPACT',
            '停靠點數': '',
            '估算距離（公里）': '',
            'OSRM 真實距離（公里）': '',
            '距離差異（%）': '',
            '估算行駛時間（分鐘）': '',
            'OSRM 真實行駛時間（分鐘）': '',
            '行駛時間差異（%）': '',
            '估算總工時（分鐘）': '',
            'OSRM 總工時（分鐘）': '',
            '估算是否超過540分鐘': '',
            'OSRM是否超過540分鐘': '',
            '成本來源': 'Haversine',
            '是否使用備援': '否',
            '備註': 'phase2_scheduler_cross_county_compact.py 暫未改用 OSRM Table/cache',
        },
    ])
    diagnostics_csv_path = os.path.join(current_dir, '../../output/Route_Cost_Diagnostics.csv')
    pd.DataFrame(diagnostics).to_csv(diagnostics_csv_path, index=False, encoding='utf-8-sig')
    log(f"診斷報表輸出完成: {diagnostics_csv_path}")

    print("Done! Saved Weekly_Schedule_Summary.xlsx, Daily_Route_Summary.xlsx, Weekly_Routing_Map.html")
    print("Also saved Weekly_Unassigned_Strict.xlsx, Driver_Weekly_Load_Strict.xlsx, and Route_Cost_Diagnostics.csv")
    log(f"Routing cost provider final stats: {cost_provider.stats_json()}")

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
        total_drive = sum(i['travel_time_min'] for i in items)
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
