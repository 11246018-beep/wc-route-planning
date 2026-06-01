from pathlib import Path
from datetime import datetime
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import Driver
from .services.driver_roster import format_schedule_driver_label, load_profiles, normalize_schedule_slot
from .security import authenticate_driver_token, is_manager_user


BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
LIVE_FILE = OUTPUT_DIR / "driver_live_status.json"
PROFILES_FILE = OUTPUT_DIR / "driver_profiles.json"


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
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)



def load_live_data():
    data = load_json_file(LIVE_FILE, {})
    return data if isinstance(data, dict) else {}



def save_live_data(data):
    save_json_file(LIVE_FILE, data)


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

        _, auth_error = authenticate_driver_token(request, driver_code)
        if auth_error:
            return cors_json({"ok": False, "message": auth_error}, status=403)

        lat = to_float(data.get("lat"))
        lon = to_float(data.get("lon"))

        if lat is None or lon is None:
            return cors_json({"ok": False, "message": "缺少正確的 lat / lon"}, status=400)

        now = datetime.now()
        live_data = load_live_data()
        previous_live = live_data.get(driver_code, {})
        previous_live = previous_live if isinstance(previous_live, dict) else {}
        progress_reset_ack = str(data.get("progress_reset_ack") or "")
        reset_info = build_reset_response(previous_live, progress_reset_ack)
        reset_required = reset_info["reset_required"]

        live_data[driver_code] = {
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
            "status": (data.get("status") or "working").strip(),
            "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at_iso": now.isoformat(),
            "reset_progress_at": reset_info["reset_progress_at"],
        }

        save_live_data(live_data)

        return cors_json(
            {
                "ok": True,
                "message": "定位上傳成功",
                "live": live_data[driver_code],
                "reset_required": reset_required,
                "reset_progress_at": reset_info["reset_progress_at"],
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

        live_data = load_live_data()
        live = live_data.get(driver_code)
        if not isinstance(live, dict):
            live = {"driver_code": driver_code}

        now = datetime.now()
        reset_progress_at = now.isoformat()
        live["current_stop_seq"] = 1 if to_int(live.get("total_count"), 0) > 0 else 0
        live["completed_count"] = 0
        live["completed_stop_seqs"] = []
        live["skipped_stop_seqs"] = []
        live["status"] = "idle"
        live["use_depot_position"] = True
        live["lat"] = None
        live["lon"] = None
        live["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        live["updated_at_iso"] = now.isoformat()
        live["reset_progress_at"] = reset_progress_at
        live_data[driver_code] = live
        save_live_data(live_data)

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
        _, auth_error = authenticate_driver_token(request, driver_code)
        if auth_error:
            return cors_json({"ok": False, "message": auth_error}, status=403)

    live_data = load_live_data()
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

    permission_response = manager_required_response(request)
    if permission_response is not None:
        return permission_response

    live_data = load_live_data()
    profiles = load_profiles()
    now = datetime.now()

    drivers = Driver.objects.all().order_by("driver_code")
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
        if completed_stop_seqs:
            completed_count = len(completed_stop_seqs)
        total_count = to_int(live.get("total_count"), 0)
        progress_pct = 0

        if total_count > 0:
            progress_pct = round((completed_count / total_count) * 100, 1)

        schedule_slot = normalize_schedule_slot(profile.get("schedule_slot")) or (code if normalize_schedule_slot(code) else "")

        items.append(
            {
                "driver_code": code,
                "driver_label": get_driver_label(code, profiles),
                "display_name": profile.get("display_name", ""),
                "phone": profile.get("phone", ""),
                "note": profile.get("note", ""),
                "schedule_slot": schedule_slot,
                "depot_id": driver.depot_id,
                "max_minutes": driver.max_minutes,
                "lat": live.get("lat"),
                "lon": live.get("lon"),
                "use_depot_position": bool(live.get("use_depot_position")),
                "day": live.get("day", 0),
                "route_id": live.get("route_id", ""),
                "current_stop_seq": live.get("current_stop_seq", 0),
                "completed_count": completed_count,
                "completed_stop_seqs": completed_stop_seqs,
                "skipped_stop_seqs": skipped_stop_seqs,
                "total_count": total_count,
                "progress_pct": progress_pct,
                "status": live.get("status", "idle"),
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
