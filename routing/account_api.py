from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone

import json

from .models import CleaningRecord, Driver
from .services.driver_roster import (
    ACTIVE_DRIVER_LIMIT,
    build_admin_driver_payload,
    build_driver_record,
    load_profiles,
    normalize_schedule_slot,
    save_profiles,
    slot_to_depot_name,
    validate_driver_constraints,
)


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



def normalize_driver_code(value):
    return str(value or "").strip().upper()



def serialize_driver(driver, profiles=None):
    profiles = profiles if isinstance(profiles, dict) else load_profiles()
    item = build_driver_record(driver, profiles)
    item["created_at"] = driver.created_at.strftime("%Y-%m-%d %H:%M:%S") if getattr(driver, "created_at", None) else ""
    return item



def get_driver_by_code(driver_code):
    return Driver.objects.filter(driver_code__iexact=driver_code).first()



def merge_profile(driver_code, display_name="", phone="", note="", is_active=True, schedule_slot=""):
    driver_code = normalize_driver_code(driver_code)
    profiles = load_profiles()
    profiles[driver_code] = {
        "display_name": (display_name or "").strip(),
        "phone": (phone or "").strip(),
        "note": (note or "").strip(),
        "is_active": bool(is_active),
        "schedule_slot": normalize_schedule_slot(schedule_slot),
    }
    save_profiles(profiles)
    return profiles



def admin_drivers_api(request):
    if request.method != "GET":
        return cors_json({"ok": False, "message": "只允許 GET"}, status=405)

    keyword = (request.GET.get("q") or "").strip().upper()
    payload = build_admin_driver_payload(list(Driver.objects.all().order_by("driver_code")))
    items = payload["drivers"]

    if keyword:
        items = [
            item for item in items
            if keyword in str(item.get("driver_code", "")).upper()
            or keyword in str(item.get("display_name", "")).upper()
            or keyword in str(item.get("phone", "")).upper()
            or keyword in str(item.get("schedule_slot", "")).upper()
        ]

    filtered_active = [item for item in items if item.get("is_active")]
    filtered_active_with_slot = [item for item in filtered_active if item.get("schedule_slot")]

    return cors_json(
        {
            "ok": True,
            "count": len(items),
            "drivers": items,
            "summary": {
                **payload["summary"],
                "filtered_count": len(items),
                "filtered_active_count": len(filtered_active),
                "filtered_active_with_slot_count": len(filtered_active_with_slot),
            },
            "fixed_slots": payload["fixed_slots"],
            "active_limit": ACTIVE_DRIVER_LIMIT,
        }
    )


@csrf_exempt
def admin_driver_save_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "POST":
        return cors_json({"ok": False, "message": "只允許 POST"}, status=405)

    try:
        data = json.loads(request.body or "{}")

        driver_id = to_int(data.get("id"), 0)
        driver_code = normalize_driver_code(data.get("driver_code"))
        depot_id = to_int(data.get("depot_id"), 0)
        max_minutes = to_int(data.get("max_minutes"), 540)
        password = (data.get("password") or "").strip()

        display_name = (data.get("display_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        note = (data.get("note") or "").strip()
        is_active = to_bool(data.get("is_active"), True)
        schedule_slot = normalize_schedule_slot(data.get("schedule_slot"))

        if not driver_code:
            return cors_json({"ok": False, "message": "請填寫司機編號"}, status=400)

        if not password:
            return cors_json({"ok": False, "message": "請填寫 App 密碼"}, status=400)

        current_payload = build_admin_driver_payload(list(Driver.objects.all().order_by("driver_code")))
        ok, validation_message = validate_driver_constraints(
            current_payload["drivers"],
            driver_id,
            driver_code,
            is_active,
            schedule_slot,
        )
        if not ok:
            return cors_json({"ok": False, "message": validation_message}, status=400)

        if driver_id > 0:
            driver = Driver.objects.filter(id=driver_id).first()
            if not driver:
                return cors_json({"ok": False, "message": "找不到要編輯的司機資料"}, status=404)

            conflict = Driver.objects.filter(driver_code__iexact=driver_code).exclude(id=driver.id).first()
            if conflict:
                return cors_json({"ok": False, "message": "司機編號已存在，請改用其他編號"}, status=400)

            old_code = normalize_driver_code(driver.driver_code)

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
                "schedule_slot": schedule_slot,
            }
            save_profiles(profiles)

            slot_text = f"，排程席位：{schedule_slot}（{slot_to_depot_name(schedule_slot)}）" if schedule_slot else "，目前不參與固定 14 車排程"
            return cors_json(
                {
                    "ok": True,
                    "message": f"司機資料已更新{slot_text}",
                    "driver": serialize_driver(driver, profiles),
                }
            )

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
            schedule_slot=schedule_slot,
        )

        slot_text = f"，排程席位：{schedule_slot}（{slot_to_depot_name(schedule_slot)}）" if schedule_slot else "，目前尚未指定固定排程席位"
        return cors_json(
            {
                "ok": True,
                "message": f"司機帳號已新增{slot_text}",
                "driver": serialize_driver(driver, profiles),
            }
        )

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
        driver_code = normalize_driver_code(data.get("driver_code"))
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

        return cors_json(
            {
                "ok": True,
                "message": f"{driver_code} 密碼已更新",
            }
        )

    except Exception as e:
        return cors_json({"ok": False, "message": f"重設密碼失敗：{str(e)}"}, status=500)


@csrf_exempt
def admin_driver_delete_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "POST":
        return cors_json({"ok": False, "message": "只允許 POST"}, status=405)

    try:
        data = json.loads(request.body or "{}")
        driver_id = to_int(data.get("id"), 0)
        driver_code = normalize_driver_code(data.get("driver_code"))

        driver = None
        if driver_id > 0:
            driver = Driver.objects.filter(id=driver_id).first()
        if not driver and driver_code:
            driver = get_driver_by_code(driver_code)

        if not driver:
            return cors_json({"ok": False, "message": "找不到要刪除的司機帳號"}, status=404)

        code = normalize_driver_code(driver.driver_code)

        CleaningRecord.objects.filter(driver=driver).delete()
        driver.delete()

        profiles = load_profiles()
        if code in profiles:
            profiles.pop(code, None)
            save_profiles(profiles)

        return cors_json(
            {
                "ok": True,
                "message": f"司機帳號 {code} 已刪除",
            }
        )

    except Exception as e:
        return cors_json({"ok": False, "message": f"刪除失敗：{str(e)}"}, status=500)



def driver_profile_api(request):
    if request.method != "GET":
        return cors_json({"ok": False, "message": "只允許 GET"}, status=405)

    driver_code = normalize_driver_code(request.GET.get("driver_code"))
    if not driver_code:
        return cors_json({"ok": False, "message": "缺少 driver_code"}, status=400)

    driver = get_driver_by_code(driver_code)
    if not driver:
        return cors_json({"ok": False, "message": "找不到該司機"}, status=404)

    profiles = load_profiles()
    item = serialize_driver(driver, profiles)
    item.pop("password", None)

    return cors_json(
        {
            "ok": True,
            "profile": item,
        }
    )