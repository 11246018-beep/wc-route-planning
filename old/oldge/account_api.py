import json
from pathlib import Path

from django.http import JsonResponse
from django.db.utils import DatabaseError, OperationalError, ProgrammingError
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import CleaningRecord, CompanyScheduleSettings, Driver
from .security import authenticate_driver_token, hash_driver_password, is_manager_user
from .tenant import DriverCompanyProfile, company_output_dir, get_driver_company, get_user_company
from .services.driver_roster import (
    ACTIVE_DRIVER_LIMIT,
    DEFAULT_FIXED_SLOT_CONFIG,
    build_admin_driver_payload,
    build_driver_record,
    load_profiles,
    normalize_schedule_slot,
    save_profiles,
    slot_to_depot_name,
    validate_driver_constraints,
)


BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"


def company_default_work_minutes(company):
    try:
        settings = CompanyScheduleSettings.objects.filter(company=company).first()
        if settings and settings.daily_work_minutes:
            return int(settings.daily_work_minutes)
    except Exception:
        pass
    return 540


def company_schedule_settings(company):
    try:
        return company.schedule_settings
    except Exception:
        return None


def company_driver_limit(company):
    settings = company_schedule_settings(company)
    try:
        if settings and settings.driver_limit:
            return max(int(settings.driver_limit), 1)
    except Exception:
        pass
    return ACTIVE_DRIVER_LIMIT


def company_has_custom_depot(company):
    settings = company_schedule_settings(company)
    return bool(settings and settings.depot_lat is not None and settings.depot_lon is not None)


def company_fixed_slots(company):
    limit = company_driver_limit(company)
    settings = company_schedule_settings(company)
    if company_has_custom_depot(company):
        depot_name = (getattr(settings, "depot_name", "") or "自訂倉庫").strip()
        return [
            {
                "slot": f"P{i:02d}",
                "label": f"P{i:02d}｜{depot_name}{i}",
                "depot_code": "Custom",
                "depot_name": depot_name,
                "slot_index": i,
            }
            for i in range(1, limit + 1)
        ]

    slots = list(DEFAULT_FIXED_SLOT_CONFIG)
    if limit <= len(slots):
        slots = slots[:limit]
    else:
        next_index = 13
        while len(slots) < limit:
            slot = f"P{next_index:02d}"
            slots.append({
                "slot": slot,
                "depot_code": "Pingzhen",
                "depot_name": "平鎮總部",
                "slot_index": next_index,
            })
            next_index += 1

    return [
        {
            "slot": item["slot"],
            "label": f"{item['slot']}｜{'五股' if item['depot_code'] == 'Wugu' else '平鎮'}{item['slot_index']}",
            "depot_code": item["depot_code"],
            "depot_name": item["depot_name"],
            "slot_index": item["slot_index"],
        }
        for item in slots
    ]


def company_slot_summary(summary, slots, company):
    slot_limits = {}
    for slot in slots:
        depot_code = slot.get("depot_code") or "Custom"
        slot_limits[depot_code] = slot_limits.get(depot_code, 0) + 1
    slot_usage = {key: 0 for key in slot_limits.keys()}
    raw_usage = (summary or {}).get("slot_usage") or {}
    for key, value in raw_usage.items():
        if key in slot_usage:
            slot_usage[key] = value
    updated = dict(summary or {})
    updated["slot_limits"] = slot_limits
    updated["slot_usage"] = slot_usage
    updated["active_limit"] = company_driver_limit(company)
    updated["default_work_minutes"] = company_default_work_minutes(company)
    updated["custom_depot"] = company_has_custom_depot(company)
    return updated


def cors_json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Driver-Token"
    response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def has_manager_permission(request):
    return is_manager_user(getattr(request, "user", None))


def manager_required_response(request):
    if has_manager_permission(request):
        return None
    return cors_json({"ok": False, "message": "需要管理員權限，請先登入後台"}, status=403)


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


def company_profiles_path(company):
    if getattr(company, "id", None):
        return company_output_dir(OUTPUT_DIR, company) / "driver_profiles.json"
    return OUTPUT_DIR / "driver_profiles.json"


def company_driver_queryset(request):
    company = get_user_company(getattr(request, "user", None))
    try:
        if not getattr(company, "id", None):
            return Driver.objects.all(), company
        profiles = list(
            DriverCompanyProfile.objects.filter(company=company)
            .values("driver_id", "driver_code")
        )
        if not profiles and getattr(company, "key", "") == "toilet_demo":
            existing_drivers = list(Driver.objects.all())
            DriverCompanyProfile.objects.bulk_create(
                [
                    DriverCompanyProfile(
                        driver_id=driver.id,
                        driver_code=str(driver.driver_code or "").strip().upper(),
                        company=company,
                    )
                    for driver in existing_drivers
                    if str(driver.driver_code or "").strip()
                ],
                ignore_conflicts=True,
            )
            profiles = list(
                DriverCompanyProfile.objects.filter(company=company)
                .values("driver_id", "driver_code")
            )
        ids = [item["driver_id"] for item in profiles if item.get("driver_id")]
        if ids:
            return Driver.objects.filter(id__in=ids), company
        codes = [item["driver_code"] for item in profiles if item.get("driver_code")]
        return Driver.objects.filter(driver_code__in=codes), company
    except (DatabaseError, OperationalError, ProgrammingError):
        return Driver.objects.all(), company


def merge_profile(driver_code, display_name="", phone="", note="", is_active=True, schedule_slot="", profiles_path=None):
    driver_code = normalize_driver_code(driver_code)
    profiles = load_profiles(profiles_path)
    profiles[driver_code] = {
        "display_name": (display_name or "").strip(),
        "phone": (phone or "").strip(),
        "note": (note or "").strip(),
        "is_active": bool(is_active),
        "schedule_slot": normalize_schedule_slot(schedule_slot),
    }
    save_profiles(profiles, profiles_path)
    return profiles


def admin_drivers_api(request):
    if request.method != "GET":
        return cors_json({"ok": False, "message": "只支援 GET"}, status=405)

    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return cors_json({"ok": False, "message": "請先登入後台"}, status=403)

    keyword = (request.GET.get("q") or "").strip().upper()
    drivers_qs, company = company_driver_queryset(request)
    profiles_path = company_profiles_path(company)
    profiles = load_profiles(profiles_path)
    payload = build_admin_driver_payload(list(drivers_qs.order_by("driver_code")), profiles=profiles)
    fixed_slots = company_fixed_slots(company)
    summary = company_slot_summary(payload["summary"], fixed_slots, company)
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
                **summary,
                "filtered_count": len(items),
                "filtered_active_count": len(filtered_active),
                "filtered_active_with_slot_count": len(filtered_active_with_slot),
            },
            "fixed_slots": fixed_slots,
            "active_limit": company_driver_limit(company),
            "default_work_minutes": company_default_work_minutes(company),
            "company": {"key": company.key, "name": company.name, "industry_type": company.industry_type},
        }
    )


@csrf_exempt
def admin_driver_save_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "POST":
        return cors_json({"ok": False, "message": "只支援 POST"}, status=405)

    permission_response = manager_required_response(request)
    if permission_response is not None:
        return permission_response

    try:
        data = json.loads(request.body or "{}")

        driver_id = to_int(data.get("id"), 0)
        driver_code = normalize_driver_code(data.get("driver_code"))
        depot_id = to_int(data.get("depot_id"), 0)
        password = (data.get("password") or "").strip()

        display_name = (data.get("display_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        note = (data.get("note") or "").strip()
        is_active = to_bool(data.get("is_active"), True)
        schedule_slot = normalize_schedule_slot(data.get("schedule_slot"))

        if not driver_code:
            return cors_json({"ok": False, "message": "請輸入司機代碼"}, status=400)

        if driver_id <= 0 and not password:
            return cors_json({"ok": False, "message": "請輸入 App 密碼"}, status=400)

        drivers_qs, company = company_driver_queryset(request)
        max_minutes = to_int(data.get("max_minutes"), company_default_work_minutes(company))
        profiles_path = company_profiles_path(company)
        profiles = load_profiles(profiles_path)
        current_payload = build_admin_driver_payload(list(drivers_qs.order_by("driver_code")), profiles=profiles)
        active_limit = None
        try:
            active_limit = int(company.schedule_settings.driver_limit)
        except Exception:
            active_limit = None
        ok, validation_message = validate_driver_constraints(
            current_payload["drivers"],
            driver_id,
            driver_code,
            is_active,
            schedule_slot,
            active_limit=active_limit,
        )
        if not ok:
            return cors_json({"ok": False, "message": validation_message}, status=400)

        if driver_id > 0:
            driver = drivers_qs.filter(id=driver_id).first()
            if not driver:
                return cors_json({"ok": False, "message": "找不到要編輯的司機"}, status=404)

            conflict = (
                DriverCompanyProfile.objects
                .filter(company=company, driver_code__iexact=driver_code)
                .exclude(driver_id=driver.id)
                .first()
            )
            if conflict:
                return cors_json({"ok": False, "message": "司機代碼已存在，請改用其他代碼"}, status=400)

            old_code = normalize_driver_code(driver.driver_code)

            driver.driver_code = driver_code
            driver.depot_id = depot_id
            driver.max_minutes = max_minutes
            update_fields = ["driver_code", "depot_id", "max_minutes"]
            if password:
                driver.password = hash_driver_password(password)
                update_fields.append("password")
            driver.save(update_fields=update_fields)

            if old_code != driver_code and old_code in profiles:
                profiles[driver_code] = profiles.pop(old_code)
            if getattr(company, "id", None):
                try:
                    DriverCompanyProfile.objects.filter(driver_id=driver.id).delete()
                    DriverCompanyProfile.objects.update_or_create(
                        driver_id=driver.id,
                        defaults={"driver_code": driver_code, "company": company},
                    )
                except (DatabaseError, OperationalError, ProgrammingError):
                    pass

            profiles[driver_code] = {
                "display_name": display_name,
                "phone": phone,
                "note": note,
                "is_active": is_active,
                "schedule_slot": schedule_slot,
            }
            save_profiles(profiles, profiles_path)

            slot_text = f"，固定席位 {schedule_slot}（{slot_to_depot_name(schedule_slot)}）" if schedule_slot else "，未固定席位"
            return cors_json(
                {
                    "ok": True,
                    "message": f"司機資料已更新{slot_text}",
                    "driver": serialize_driver(driver, profiles),
                }
            )

        existing = DriverCompanyProfile.objects.filter(company=company, driver_code__iexact=driver_code).first()
        if existing:
            return cors_json({"ok": False, "message": "司機代碼已存在，請改用其他代碼"}, status=400)

        driver = Driver.objects.create(
            created_at=timezone.now(),
            driver_code=driver_code,
            depot_id=depot_id,
            max_minutes=max_minutes,
            password=hash_driver_password(password),
        )
        if getattr(company, "id", None):
            try:
                DriverCompanyProfile.objects.update_or_create(
                    driver_id=driver.id,
                    defaults={"driver_code": driver_code, "company": company},
                )
            except (DatabaseError, OperationalError, ProgrammingError):
                pass

        profiles = merge_profile(
            driver_code=driver_code,
            display_name=display_name,
            phone=phone,
            note=note,
            is_active=is_active,
            schedule_slot=schedule_slot,
            profiles_path=profiles_path,
        )

        slot_text = f"，固定席位 {schedule_slot}（{slot_to_depot_name(schedule_slot)}）" if schedule_slot else "，未固定席位"
        return cors_json(
            {
                "ok": True,
                "message": f"司機帳號已建立{slot_text}",
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
        return cors_json({"ok": False, "message": "只支援 POST"}, status=405)

    permission_response = manager_required_response(request)
    if permission_response is not None:
        return permission_response

    try:
        data = json.loads(request.body or "{}")
        driver_code = normalize_driver_code(data.get("driver_code"))
        new_password = (data.get("new_password") or "").strip()

        if not driver_code:
            return cors_json({"ok": False, "message": "缺少 driver_code"}, status=400)

        if not new_password:
            return cors_json({"ok": False, "message": "請輸入新密碼"}, status=400)

        drivers_qs, _ = company_driver_queryset(request)
        driver = drivers_qs.filter(driver_code__iexact=driver_code).first()
        if not driver:
            return cors_json({"ok": False, "message": "找不到司機帳號"}, status=404)

        driver.password = hash_driver_password(new_password)
        driver.save()

        return cors_json({"ok": True, "message": f"{driver_code} 密碼已更新"})

    except Exception as e:
        return cors_json({"ok": False, "message": f"更新密碼失敗：{str(e)}"}, status=500)


@csrf_exempt
def admin_driver_delete_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "POST":
        return cors_json({"ok": False, "message": "只支援 POST"}, status=405)

    permission_response = manager_required_response(request)
    if permission_response is not None:
        return permission_response

    try:
        data = json.loads(request.body or "{}")
        driver_id = to_int(data.get("id"), 0)
        driver_code = normalize_driver_code(data.get("driver_code"))

        drivers_qs, _ = company_driver_queryset(request)
        driver = None
        if driver_id > 0:
            driver = drivers_qs.filter(id=driver_id).first()
        if not driver and driver_code:
            driver = drivers_qs.filter(driver_code__iexact=driver_code).first()

        if not driver:
            return cors_json({"ok": False, "message": "找不到要刪除的司機"}, status=404)

        code = normalize_driver_code(driver.driver_code)

        CleaningRecord.objects.filter(driver=driver).delete()
        driver.delete()

        _, company = company_driver_queryset(request)
        profiles_path = company_profiles_path(company)
        profiles = load_profiles(profiles_path)
        if code in profiles:
            profiles.pop(code, None)
            save_profiles(profiles, profiles_path)
        try:
            DriverCompanyProfile.objects.filter(driver_id=driver.id).delete()
        except (DatabaseError, OperationalError, ProgrammingError):
            pass

        return cors_json({"ok": True, "message": f"司機帳號 {code} 已刪除"})

    except Exception as e:
        return cors_json({"ok": False, "message": f"刪除失敗：{str(e)}"}, status=500)


def driver_profile_api(request):
    if request.method != "GET":
        return cors_json({"ok": False, "message": "只支援 GET"}, status=405)

    driver_code = normalize_driver_code(request.GET.get("driver_code"))
    if not driver_code:
        return cors_json({"ok": False, "message": "缺少 driver_code"}, status=400)

    if has_manager_permission(request):
        drivers_qs, company = company_driver_queryset(request)
        driver = drivers_qs.filter(driver_code__iexact=driver_code).first()
        if not driver:
            return cors_json({"ok": False, "message": "找不到此公司的司機帳號"}, status=404)
    else:
        driver_from_token, token_error = authenticate_driver_token(request, driver_code)
        if driver_from_token is None:
            return cors_json({"ok": False, "message": token_error}, status=403)
        driver = driver_from_token
        company = get_driver_company(driver)

    profiles = load_profiles(company_profiles_path(company))
    item = serialize_driver(driver, profiles)
    item.pop("password", None)

    return cors_json({"ok": True, "profile": item})
