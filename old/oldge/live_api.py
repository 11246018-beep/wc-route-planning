from pathlib import Path
from datetime import datetime
import json
import math
import os
import threading

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import Driver
from .services.driver_roster import format_schedule_driver_label, load_profiles, normalize_schedule_slot
from .security import authenticate_driver_token, is_manager_user
from .tenant import company_output_dir, current_company_output_dir, get_driver_company, get_user_company, tenant_file_path


BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
LIVE_FILE = OUTPUT_DIR / "driver_live_status.json"
PROFILES_FILE = OUTPUT_DIR / "driver_profiles.json"
LIVE_DATA_LOCK = threading.RLock()


def cors_json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Driver-Token"
    response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def manager_required_response(request):
    if is_manager_user(getattr(request, "user", None)):
        return None
    return cors_json({"ok": False, "message": "需要管理員權限"}, status=403)



def to_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default



def to_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def to_int_list(value):
    if not isinstance(value, list):
        return []

    result = []
    seen = set()
    for item in value:
        seq = to_int(item, 0)
        if seq > 0 and seq not in seen:
            result.append(seq)
            seen.add(seq)
    return result



def load_json_file(path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return default



def save_json_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    temp_path.replace(path)



def live_file_for_company(company):
    return tenant_file_path(OUTPUT_DIR, company, "driver_live_status.json", fallback=False)


def profiles_for_company(company):
    path = company_output_dir(OUTPUT_DIR, company) / "driver_profiles.json"
    return load_profiles(path)


def route_driver_codes_for_company(company):
    output_dir = company_output_dir(OUTPUT_DIR, company)
    path = output_dir / "routes_normal.json"
    if not path.exists():
        return []


def route_index_for_company(company):
    output_dir = company_output_dir(OUTPUT_DIR, company)
    path = output_dir / "routes_normal.json"
    if not path.exists():
        return {}, {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        routes = raw.get("routes", []) if isinstance(raw, dict) else []
    except Exception:
        routes = []

    by_driver_day = {}
    first_by_driver = {}
    for route in routes:
        if not isinstance(route, dict):
            continue
        code = str(route.get("driver") or "").strip().upper()
        day = to_int(route.get("day"), 0)
        if not code or day <= 0:
            continue
        stop_count = to_int(route.get("stop_count"), len(route.get("stops") or []))
        info = {
            "driver_code": code,
            "driver_label": str(route.get("driver_label") or code),
            "day": day,
            "route_id": str(route.get("route_id") or ""),
            "stop_count": stop_count,
        }
        by_driver_day[(code, day)] = info
        current = first_by_driver.get(code)
        if current is None or day < current["day"]:
            first_by_driver[code] = info
    return by_driver_day, first_by_driver
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        routes = raw.get("routes", []) if isinstance(raw, dict) else []
        codes = sorted({
            str(route.get("driver") or "").strip().upper()
            for route in routes
            if str(route.get("driver") or "").strip()
        })
        return codes
    except Exception:
        return []


def load_live_data(path=None):
    data = load_json_file(path or LIVE_FILE, {})
    return data if isinstance(data, dict) else {}



def save_live_data(data, path=None):
    save_json_file(path or LIVE_FILE, data)


def update_driver_live(path, driver_code, updater):
    """Atomically update one driver so concurrent location posts do not collide."""
    with LIVE_DATA_LOCK:
        live_data = load_live_data(path)
        previous = live_data.get(driver_code, {})
        previous = previous if isinstance(previous, dict) else {}
        current = updater(dict(previous))
        live_data[driver_code] = current
        save_live_data(live_data, path)
        return current


def distance_meters(lat1, lon1, lat2, lon2):
    values = [to_float(lat1), to_float(lon1), to_float(lat2), to_float(lon2)]
    if any(value is None for value in values):
        return None
    lat1, lon1, lat2, lon2 = values
    radius = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    a = min(max(a, 0.0), 1.0)
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def motion_state(previous, lat, lon, speed_mps, now, status):
    """Classify slow traffic after 3 minutes and a 50 m stop after 10 minutes."""
    result = {
        "traffic_state": "normal",
        "slow_since_iso": "",
        "stationary_since_iso": "",
        "stationary_anchor_lat": None,
        "stationary_anchor_lon": None,
        "low_speed_seconds": 0,
        "stationary_seconds": 0,
    }
    if status not in {"navigating"}:
        return result

    speed_kmh = max(to_float(speed_mps, 0) or 0, 0) * 3.6
    slow_since = parse_iso_datetime(previous.get("slow_since_iso"))
    stationary_since = parse_iso_datetime(previous.get("stationary_since_iso"))
    anchor_lat = to_float(previous.get("stationary_anchor_lat"))
    anchor_lon = to_float(previous.get("stationary_anchor_lon"))

    if speed_kmh < 10:
        slow_since = slow_since or now
    else:
        slow_since = None

    if speed_kmh < 1:
        if stationary_since is None or anchor_lat is None or anchor_lon is None:
            stationary_since = now
            anchor_lat, anchor_lon = lat, lon
        else:
            anchor_distance = distance_meters(anchor_lat, anchor_lon, lat, lon)
            if anchor_distance is not None and anchor_distance > 50:
                stationary_since = now
                anchor_lat, anchor_lon = lat, lon
    else:
        stationary_since = None
        anchor_lat = anchor_lon = None

    low_speed_seconds = max(int((now - slow_since).total_seconds()), 0) if slow_since else 0
    stationary_seconds = max(int((now - stationary_since).total_seconds()), 0) if stationary_since else 0
    traffic_state = "normal"
    if stationary_seconds >= 600:
        traffic_state = "abnormal_stop"
    elif low_speed_seconds >= 180:
        traffic_state = "congested"

    return {
        "traffic_state": traffic_state,
        "slow_since_iso": slow_since.isoformat() if slow_since else "",
        "stationary_since_iso": stationary_since.isoformat() if stationary_since else "",
        "stationary_anchor_lat": anchor_lat,
        "stationary_anchor_lon": anchor_lon,
        "low_speed_seconds": low_speed_seconds,
        "stationary_seconds": stationary_seconds,
    }


def build_reset_response(live, progress_reset_ack=""):
    reset_progress_at = str(live.get("reset_progress_at") or "")
    return {
        "reset_progress_at": reset_progress_at,
        "reset_required": bool(reset_progress_at and progress_reset_ack != reset_progress_at),
    }



def parse_iso_datetime(text):
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None



def get_driver_label(code, profiles=None):
    profiles = profiles if isinstance(profiles, dict) else load_profiles()
    driver_code = str(code or "").strip().upper()
    profile = profiles.get(driver_code, {})
    display_name = str(profile.get("display_name") or "").strip()
    schedule_slot = normalize_schedule_slot(profile.get("schedule_slot")) or (driver_code if normalize_schedule_slot(driver_code) else "")
    return format_schedule_driver_label(driver_code, display_name, schedule_slot)


@csrf_exempt
def driver_live_update_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "POST":
        return cors_json({"ok": False, "message": "只允許 POST"}, status=405)

    try:
        data = json.loads(request.body or "{}")

        driver_code = (data.get("driver_code") or "").strip().upper()
        if not driver_code:
            return cors_json({"ok": False, "message": "缺少 driver_code"}, status=400)

        driver, auth_error = authenticate_driver_token(request, driver_code)
        if auth_error:
            return cors_json({"ok": False, "message": auth_error}, status=403)

        lat = to_float(data.get("lat"))
        lon = to_float(data.get("lon"))

        if lat is None or lon is None:
            return cors_json({"ok": False, "message": "缺少正確的 lat / lon"}, status=400)

        now = datetime.now()
        company = get_driver_company(driver or driver_code)
        live_file = live_file_for_company(company)
        progress_reset_ack = str(data.get("progress_reset_ack") or "")
        location_only = data.get("location_only") is True
        reset_result = {"reset_required": False, "reset_progress_at": ""}

        def build_live(previous_live):
            reset_info = build_reset_response(previous_live, progress_reset_ack)
            reset_result.update(reset_info)
            reset_required = reset_info["reset_required"]
            speed_mps = max(to_float(data.get("speed_mps"), 0) or 0, 0)

            if location_only:
                current = dict(previous_live)
                session_started = data.get("session_started") is True
                status = str(current.get("status") or "idle").strip()
                requested_status = str(data.get("activity_status") or "").strip()
                if requested_status in {"idle", "navigating", "working", "paused", "finished"}:
                    status = requested_status
                if status == "idle" and speed_mps * 3.6 >= 3:
                    status = "navigating"
                use_depot_position = (
                    bool(current.get("use_depot_position"))
                    and status == "idle"
                    and not session_started
                )
                current.update({
                    "driver_code": driver_code,
                    "lat": lat,
                    "lon": lon,
                    "use_depot_position": use_depot_position,
                    "speed_mps": speed_mps,
                    "speed_kmh": round(speed_mps * 3.6, 1),
                    "accuracy_m": to_float(data.get("accuracy_m")),
                    "heading": to_float(data.get("heading")),
                    "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "updated_at_iso": now.isoformat(),
                    "status": status,
                    "reset_progress_at": reset_info["reset_progress_at"],
                })
            else:
                status = (data.get("status") or "working").strip()
                current = {
                    "driver_code": driver_code,
                    "day": to_int(data.get("day"), 0),
                    "route_id": (data.get("route_id") or "").strip(),
                    "lat": lat,
                    "lon": lon,
                    "use_depot_position": False,
                    "current_stop_seq": 1 if reset_required and to_int(data.get("total_count"), 0) > 0 else to_int(data.get("current_stop_seq"), 0),
                    "completed_count": 0 if reset_required else to_int(data.get("completed_count"), 0),
                    "completed_stop_seqs": [] if reset_required else to_int_list(data.get("completed_stop_seqs")),
                    "skipped_stop_seqs": [] if reset_required else to_int_list(data.get("skipped_stop_seqs")),
                    "total_count": to_int(data.get("total_count"), 0),
                    "status": status,
                    "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "updated_at_iso": now.isoformat(),
                    "reset_progress_at": reset_info["reset_progress_at"],
                    "speed_mps": speed_mps,
                    "speed_kmh": round(speed_mps * 3.6, 1),
                }

            current.update(motion_state(previous_live, lat, lon, speed_mps, now, status))
            return current

        current_live = update_driver_live(live_file, driver_code, build_live)

        return cors_json(
            {
                "ok": True,
                "message": "定位上傳成功",
                "live": current_live,
                "reset_required": reset_result["reset_required"],
                "reset_progress_at": reset_result["reset_progress_at"],
            }
        )

    except Exception as e:
        return cors_json({"ok": False, "message": f"更新即時定位失敗：{str(e)}"}, status=500)





@csrf_exempt
def admin_live_reset_progress_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "POST":
        return cors_json({"ok": False, "message": "只允許 POST"}, status=405)

    permission_response = manager_required_response(request)
    if permission_response is not None:
        return permission_response

    try:
        data = json.loads(request.body or "{}")
        driver_code = (data.get("driver_code") or "").strip().upper()
        if not driver_code:
            return cors_json({"ok": False, "message": "缺少 driver_code"}, status=400)

        company = get_user_company(getattr(request, "user", None))
        company_driver_codes = {
            str(item.driver_code or "").upper()
            for item in company.driver_profiles.all()
        } if getattr(company, "id", None) else set()
        if company_driver_codes and driver_code not in company_driver_codes:
            return cors_json({"ok": False, "message": "不能清除其他公司的司機進度"}, status=403)

        live_file = live_file_for_company(company)
        now = datetime.now()
        reset_progress_at = now.isoformat()

        def reset_live(live):
            live["driver_code"] = driver_code
            live["current_stop_seq"] = 1 if to_int(live.get("total_count"), 0) > 0 else 0
            live["completed_count"] = 0
            live["completed_stop_seqs"] = []
            live["skipped_stop_seqs"] = []
            live["status"] = "idle"
            live["use_depot_position"] = True
            live["lat"] = None
            live["lon"] = None
            live["speed_mps"] = 0
            live["speed_kmh"] = 0
            live["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
            live["updated_at_iso"] = now.isoformat()
            live["reset_progress_at"] = reset_progress_at
            live.update(motion_state({}, None, None, 0, now, "idle"))
            return live

        live = update_driver_live(live_file, driver_code, reset_live)

        return cors_json({"ok": True, "message": "已清除目前進度", "live": live})

    except Exception as e:
        return cors_json({"ok": False, "message": f"清除目前進度失敗：{str(e)}"}, status=500)


def driver_live_state_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "GET":
        return cors_json({"ok": False, "message": "只允許 GET"}, status=405)

    driver_code = (request.GET.get("driver_code") or "").strip().upper()
    if not driver_code:
        return cors_json({"ok": False, "message": "缺少 driver_code"}, status=400)

    if not is_manager_user(getattr(request, "user", None)):
        driver, auth_error = authenticate_driver_token(request, driver_code)
        if auth_error:
            return cors_json({"ok": False, "message": auth_error}, status=403)
    else:
        driver = None

    company = get_driver_company(driver or driver_code)
    live_data = load_live_data(live_file_for_company(company))
    live = live_data.get(driver_code, {})
    live = live if isinstance(live, dict) else {}
    reset_info = build_reset_response(live, str(request.GET.get("progress_reset_ack") or ""))

    return cors_json(
        {
            "ok": True,
            "live": live,
            "reset_required": reset_info["reset_required"],
            "reset_progress_at": reset_info["reset_progress_at"],
        }
    )


def admin_live_overview_api(request):
    if request.method != "GET":
        return cors_json({"ok": False, "message": "只允許 GET"}, status=405)

    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return cors_json({"ok": False, "message": "請先登入後台"}, status=403)

    company = get_user_company(getattr(request, "user", None))
    live_data = load_live_data(live_file_for_company(company))
    profiles = profiles_for_company(company)
    route_by_driver_day, first_route_by_driver = route_index_for_company(company)
    now = datetime.now()

    drivers = Driver.objects.all().order_by("driver_code")
    try:
        company_profiles = list(company.driver_profiles.all())
        company_driver_ids = {
            item.driver_id
            for item in company_profiles
            if item.driver_id
        }
        company_driver_codes = {
            str(item.driver_code or "").upper()
            for item in company_profiles
            if str(item.driver_code or "").strip()
        }
    except Exception:
        company_driver_ids = set()
        company_driver_codes = set()
    if company_driver_ids:
        drivers = drivers.filter(id__in=company_driver_ids)
    elif company_driver_codes:
        drivers = drivers.filter(driver_code__in=company_driver_codes)
    elif getattr(company, "id", None):
        route_codes = route_driver_codes_for_company(company)
        if route_codes:
            virtual_drivers = []
            for code in route_codes:
                virtual = type("VirtualDriver", (), {})()
                virtual.driver_code = code
                virtual.depot_id = 0
                virtual.max_minutes = 0
                virtual_drivers.append(virtual)
            drivers = virtual_drivers
        else:
            drivers = Driver.objects.none()
    items = []

    for driver in drivers:
        code = str(driver.driver_code).upper()
        live = live_data.get(code, {})
        profile = profiles.get(code, {})

        last_dt = parse_iso_datetime(live.get("updated_at_iso"))
        online = False
        seconds_ago = None

        if last_dt:
            seconds_ago = int((now - last_dt).total_seconds())
            online = seconds_ago <= 120

        completed_count = to_int(live.get("completed_count"), 0)
        completed_stop_seqs = to_int_list(live.get("completed_stop_seqs"))
        skipped_stop_seqs = to_int_list(live.get("skipped_stop_seqs"))
        live_day = to_int(live.get("day"), 0)
        route_info = route_by_driver_day.get((code, live_day)) if live_day > 0 else None
        if route_info is None:
            route_info = first_route_by_driver.get(code)
        route_total_count = to_int((route_info or {}).get("stop_count"), 0)
        total_count = route_total_count or to_int(live.get("total_count"), 0)
        completed_stop_seqs = [seq for seq in completed_stop_seqs if total_count <= 0 or seq <= total_count]
        skipped_stop_seqs = [seq for seq in skipped_stop_seqs if total_count <= 0 or seq <= total_count]
        if completed_stop_seqs:
            completed_count = len(completed_stop_seqs)
        completed_count = min(completed_count, total_count) if total_count > 0 else completed_count
        progress_pct = 0

        if total_count > 0:
            progress_pct = round((completed_count / total_count) * 100, 1)

        schedule_slot = normalize_schedule_slot(profile.get("schedule_slot")) or (code if normalize_schedule_slot(code) else "")

        items.append(
            {
                "driver_code": code,
                "driver_label": profile.get("display_name") or (route_info or {}).get("driver_label") or get_driver_label(code, profiles),
                "display_name": profile.get("display_name", ""),
                "phone": profile.get("phone", ""),
                "note": profile.get("note", ""),
                "schedule_slot": schedule_slot,
                "depot_id": driver.depot_id,
                "max_minutes": driver.max_minutes,
                "lat": live.get("lat"),
                "lon": live.get("lon"),
                "use_depot_position": bool(live.get("use_depot_position")),
                "day": (route_info or {}).get("day") or live.get("day", 0),
                "route_id": (route_info or {}).get("route_id") or live.get("route_id", ""),
                "current_stop_seq": min(to_int(live.get("current_stop_seq"), 0), total_count) if total_count > 0 else live.get("current_stop_seq", 0),
                "completed_count": completed_count,
                "completed_stop_seqs": completed_stop_seqs,
                "skipped_stop_seqs": skipped_stop_seqs,
                "total_count": total_count,
                "progress_pct": progress_pct,
                "status": live.get("status", "idle"),
                "traffic_state": live.get("traffic_state", "normal"),
                "speed_kmh": round(to_float(live.get("speed_kmh"), 0) or 0, 1),
                "low_speed_seconds": to_int(live.get("low_speed_seconds"), 0),
                "stationary_seconds": to_int(live.get("stationary_seconds"), 0),
                "updated_at": live.get("updated_at", ""),
                "updated_at_iso": live.get("updated_at_iso", ""),
                "seconds_ago": seconds_ago,
                "online": online,
            }
        )

    return cors_json(
        {
            "ok": True,
            "count": len(items),
            "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "drivers": items,
        }
    )
