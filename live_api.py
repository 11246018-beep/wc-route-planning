from pathlib import Path
from datetime import datetime
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import Driver


BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
LIVE_FILE = OUTPUT_DIR / "driver_live_status.json"
PROFILES_FILE = OUTPUT_DIR / "driver_profiles.json"


def cors_json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Headers"] = "Content-Type"
    response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


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


def load_profiles():
    data = load_json_file(PROFILES_FILE, {})
    return data if isinstance(data, dict) else {}


def parse_iso_datetime(text):
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def get_driver_label(code):
    code = str(code or "").upper()
    if code.startswith("P") and code[1:].isdigit():
        return f"{code}｜平鎮{code[1:].lstrip('0') or '0'}"
    if code.startswith("W") and code[1:].isdigit():
        return f"{code}｜五股{code[1:].lstrip('0') or '0'}"
    return code


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

        lat = to_float(data.get("lat"))
        lon = to_float(data.get("lon"))

        if lat is None or lon is None:
            return cors_json({"ok": False, "message": "缺少有效的 lat / lon"}, status=400)

        now = datetime.now()
        live_data = load_live_data()

        live_data[driver_code] = {
            "driver_code": driver_code,
            "day": to_int(data.get("day"), 0),
            "route_id": (data.get("route_id") or "").strip(),
            "lat": lat,
            "lon": lon,
            "current_stop_seq": to_int(data.get("current_stop_seq"), 0),
            "completed_count": to_int(data.get("completed_count"), 0),
            "total_count": to_int(data.get("total_count"), 0),
            "status": (data.get("status") or "working").strip(),
            "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at_iso": now.isoformat(),
        }

        save_live_data(live_data)

        return cors_json({
            "ok": True,
            "message": "位置已更新",
            "live": live_data[driver_code],
        })

    except Exception as e:
        return cors_json({"ok": False, "message": f"更新即時位置失敗：{str(e)}"}, status=500)


def admin_live_overview_api(request):
    if request.method != "GET":
        return cors_json({"ok": False, "message": "只允許 GET"}, status=405)

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
        total_count = to_int(live.get("total_count"), 0)
        progress_pct = 0

        if total_count > 0:
            progress_pct = round((completed_count / total_count) * 100, 1)

        items.append({
            "driver_code": code,
            "driver_label": get_driver_label(code),
            "display_name": profile.get("display_name", ""),
            "phone": profile.get("phone", ""),
            "note": profile.get("note", ""),
            "depot_id": driver.depot_id,
            "max_minutes": driver.max_minutes,
            "lat": live.get("lat"),
            "lon": live.get("lon"),
            "day": live.get("day", 0),
            "route_id": live.get("route_id", ""),
            "current_stop_seq": live.get("current_stop_seq", 0),
            "completed_count": completed_count,
            "total_count": total_count,
            "progress_pct": progress_pct,
            "status": live.get("status", "idle"),
            "updated_at": live.get("updated_at", ""),
            "updated_at_iso": live.get("updated_at_iso", ""),
            "seconds_ago": seconds_ago,
            "online": online,
        })

    return cors_json({
        "ok": True,
        "count": len(items),
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "drivers": items,
    })