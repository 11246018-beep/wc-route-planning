from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone

from pathlib import Path
import json

from .models import Driver


BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
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
        return int(value)
    except Exception:
        return default


def to_bool(value, default=True):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ["true", "1", "yes", "y", "on"]:
        return True
    if text in ["false", "0", "no", "n", "off"]:
        return False
    return default


def load_profiles():
    if not PROFILES_FILE.exists():
        return {}

    try:
        with PROFILES_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except Exception:
        return {}


def save_profiles(profiles):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with PROFILES_FILE.open("w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)


def serialize_driver(driver, profiles):
    profile = profiles.get(str(driver.driver_code).upper(), {})
    return {
        "id": driver.id,
        "driver_code": driver.driver_code,
        "depot_id": driver.depot_id,
        "max_minutes": driver.max_minutes,
        "password": driver.password,
        "display_name": profile.get("display_name", ""),
        "phone": profile.get("phone", ""),
        "note": profile.get("note", ""),
        "is_active": profile.get("is_active", True),
        "created_at": driver.created_at.strftime("%Y-%m-%d %H:%M:%S") if driver.created_at else "",
    }


def get_driver_by_code(driver_code):
    return Driver.objects.filter(driver_code__iexact=driver_code).first()


def merge_profile(driver_code, display_name="", phone="", note="", is_active=True):
    profiles = load_profiles()
    profiles[str(driver_code).upper()] = {
        "display_name": display_name,
        "phone": phone,
        "note": note,
        "is_active": is_active,
    }
    save_profiles(profiles)
    return profiles


def admin_drivers_api(request):
    if request.method != "GET":
        return cors_json({"ok": False, "message": "只允許 GET"}, status=405)

    keyword = (request.GET.get("q") or "").strip().upper()
    profiles = load_profiles()

    drivers = list(Driver.objects.all().order_by("driver_code"))
    items = [serialize_driver(driver, profiles) for driver in drivers]

    if keyword:
        items = [
            item for item in items
            if keyword in str(item.get("driver_code", "")).upper()
            or keyword in str(item.get("display_name", "")).upper()
            or keyword in str(item.get("phone", "")).upper()
        ]

    return cors_json({
        "ok": True,
        "count": len(items),
        "drivers": items,
    })


@csrf_exempt
def admin_driver_save_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "POST":
        return cors_json({"ok": False, "message": "只允許 POST"}, status=405)

    try:
        data = json.loads(request.body or "{}")

        driver_id = to_int(data.get("id"), 0)
        driver_code = (data.get("driver_code") or "").strip().upper()
        depot_id = to_int(data.get("depot_id"), 0)
        max_minutes = to_int(data.get("max_minutes"), 540)
        password = (data.get("password") or "").strip()

        display_name = (data.get("display_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        note = (data.get("note") or "").strip()
        is_active = to_bool(data.get("is_active"), True)

        if not driver_code:
            return cors_json({"ok": False, "message": "請填寫司機編號"}, status=400)

        if not password:
            return cors_json({"ok": False, "message": "請填寫密碼"}, status=400)

        if driver_id > 0:
            driver = Driver.objects.filter(id=driver_id).first()
            if not driver:
                return cors_json({"ok": False, "message": "找不到要編輯的司機資料"}, status=404)

            conflict = Driver.objects.filter(driver_code__iexact=driver_code).exclude(id=driver.id).first()
            if conflict:
                return cors_json({"ok": False, "message": "司機編號已存在，請改用其他編號"}, status=400)

            old_code = str(driver.driver_code).upper()

            driver.driver_code = driver_code
            driver.depot_id = depot_id
            driver.max_minutes = max_minutes
            driver.password = password
            driver.save()

            profiles = load_profiles()
            if old_code != driver_code and old_code in profiles:
                profiles[driver_code] = profiles.pop(old_code)

            profiles[driver_code] = {
                "display_name": display_name,
                "phone": phone,
                "note": note,
                "is_active": is_active,
            }
            save_profiles(profiles)

            return cors_json({
                "ok": True,
                "message": "司機資料已更新",
                "driver": serialize_driver(driver, profiles),
            })

        existing = Driver.objects.filter(driver_code__iexact=driver_code).first()
        if existing:
            return cors_json({"ok": False, "message": "司機編號已存在，請直接編輯原資料"}, status=400)

        driver = Driver.objects.create(
            created_at=timezone.now(),
            driver_code=driver_code,
            depot_id=depot_id,
            max_minutes=max_minutes,
            password=password,
        )

        profiles = merge_profile(
            driver_code=driver_code,
            display_name=display_name,
            phone=phone,
            note=note,
            is_active=is_active,
        )

        return cors_json({
            "ok": True,
            "message": "司機帳號已新增",
            "driver": serialize_driver(driver, profiles),
        })

    except Exception as e:
        return cors_json({"ok": False, "message": f"儲存失敗：{str(e)}"}, status=500)


@csrf_exempt
def admin_driver_password_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "POST":
        return cors_json({"ok": False, "message": "只允許 POST"}, status=405)

    try:
        data = json.loads(request.body or "{}")
        driver_code = (data.get("driver_code") or "").strip().upper()
        new_password = (data.get("new_password") or "").strip()

        if not driver_code:
            return cors_json({"ok": False, "message": "缺少 driver_code"}, status=400)

        if not new_password:
            return cors_json({"ok": False, "message": "請輸入新密碼"}, status=400)

        driver = get_driver_by_code(driver_code)
        if not driver:
            return cors_json({"ok": False, "message": "找不到該司機帳號"}, status=404)

        driver.password = new_password
        driver.save()

        return cors_json({
            "ok": True,
            "message": f"{driver_code} 密碼已更新",
        })

    except Exception as e:
        return cors_json({"ok": False, "message": f"重設密碼失敗：{str(e)}"}, status=500)


def driver_profile_api(request):
    if request.method != "GET":
        return cors_json({"ok": False, "message": "只允許 GET"}, status=405)

    driver_code = (request.GET.get("driver_code") or "").strip().upper()
    if not driver_code:
        return cors_json({"ok": False, "message": "缺少 driver_code"}, status=400)

    driver = get_driver_by_code(driver_code)
    if not driver:
        return cors_json({"ok": False, "message": "找不到該司機"}, status=404)

    profiles = load_profiles()
    item = serialize_driver(driver, profiles)
    item.pop("password", None)

    return cors_json({
        "ok": True,
        "profile": item,
    })