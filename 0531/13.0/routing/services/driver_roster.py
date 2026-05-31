from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import os


BASE_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = BASE_DIR / "output"
PROFILES_FILE = OUTPUT_DIR / "driver_profiles.json"

FIXED_SLOT_CONFIG = [
    {"slot": "W01", "depot_code": "Wugu", "depot_name": "五股總部", "slot_index": 1},
    {"slot": "W02", "depot_code": "Wugu", "depot_name": "五股總部", "slot_index": 2},
    {"slot": "P01", "depot_code": "Pingzhen", "depot_name": "平鎮總部", "slot_index": 1},
    {"slot": "P02", "depot_code": "Pingzhen", "depot_name": "平鎮總部", "slot_index": 2},
    {"slot": "P03", "depot_code": "Pingzhen", "depot_name": "平鎮總部", "slot_index": 3},
    {"slot": "P04", "depot_code": "Pingzhen", "depot_name": "平鎮總部", "slot_index": 4},
    {"slot": "P05", "depot_code": "Pingzhen", "depot_name": "平鎮總部", "slot_index": 5},
    {"slot": "P06", "depot_code": "Pingzhen", "depot_name": "平鎮總部", "slot_index": 6},
    {"slot": "P07", "depot_code": "Pingzhen", "depot_name": "平鎮總部", "slot_index": 7},
    {"slot": "P08", "depot_code": "Pingzhen", "depot_name": "平鎮總部", "slot_index": 8},
    {"slot": "P09", "depot_code": "Pingzhen", "depot_name": "平鎮總部", "slot_index": 9},
    {"slot": "P10", "depot_code": "Pingzhen", "depot_name": "平鎮總部", "slot_index": 10},
    {"slot": "P11", "depot_code": "Pingzhen", "depot_name": "平鎮總部", "slot_index": 11},
    {"slot": "P12", "depot_code": "Pingzhen", "depot_name": "平鎮總部", "slot_index": 12},
]

FIXED_SLOT_CODES = [item["slot"] for item in FIXED_SLOT_CONFIG]
FIXED_SLOT_MAP = {item["slot"]: item for item in FIXED_SLOT_CONFIG}
DEPOT_SLOT_LIMITS = dict(Counter(item["depot_code"] for item in FIXED_SLOT_CONFIG))
ACTIVE_DRIVER_LIMIT = len(FIXED_SLOT_CODES)


def normalize_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    return text


def normalize_driver_code(value):
    return normalize_text(value).upper()


def normalize_schedule_slot(value):
    slot = normalize_text(value).upper()
    return slot if slot in FIXED_SLOT_MAP else ""


def slot_to_depot_code(slot):
    slot = normalize_schedule_slot(slot)
    if not slot:
        return ""
    return FIXED_SLOT_MAP[slot]["depot_code"]


def slot_to_depot_name(slot):
    slot = normalize_schedule_slot(slot)
    if not slot:
        return ""
    return FIXED_SLOT_MAP[slot]["depot_name"]


def slot_to_index(slot):
    slot = normalize_schedule_slot(slot)
    if not slot:
        return 0
    return FIXED_SLOT_MAP[slot]["slot_index"]


def slot_to_label(slot):
    slot = normalize_schedule_slot(slot)
    if not slot:
        return ""
    info = FIXED_SLOT_MAP[slot]
    depot_text = "五股" if info["depot_code"] == "Wugu" else "平鎮"
    return f"{slot}｜{depot_text}{info['slot_index']}"


def slot_sort_key(slot):
    slot = normalize_schedule_slot(slot)
    if not slot:
        return (9, 999, "")
    info = FIXED_SLOT_MAP[slot]
    depot_rank = 0 if info["depot_code"] == "Pingzhen" else 1
    return (depot_rank, info["slot_index"], slot)


def schedule_sort_key(schedule_slot, driver_code=""):
    slot = normalize_schedule_slot(schedule_slot)
    if slot:
        return slot_sort_key(slot)
    code = normalize_driver_code(driver_code)
    if code.startswith("P") and code[1:].isdigit():
        return (0, int(code[1:]), code)
    if code.startswith("W") and code[1:].isdigit():
        return (1, int(code[1:]), code)
    return (9, 999, code)


def default_driver_label_from_slot(slot):
    return slot_to_label(slot) or normalize_driver_code(slot)


def format_schedule_driver_label(driver_code, display_name="", schedule_slot=""):
    driver_code = normalize_driver_code(driver_code)
    display_name = normalize_text(display_name)
    slot_text = slot_to_label(schedule_slot)
    slot_suffix = slot_text.split("｜", 1)[1] if "｜" in slot_text else slot_text
    if display_name and slot_suffix:
        return f"{driver_code}｜{display_name}｜{slot_suffix}"
    if display_name:
        return f"{driver_code}｜{display_name}"
    if slot_suffix:
        return f"{driver_code}｜{slot_suffix}"
    return driver_code


def load_profiles():
    if not PROFILES_FILE.exists():
        return {}
    try:
        with PROFILES_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_profiles(profiles):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with PROFILES_FILE.open("w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)


def ensure_django():
    try:
        from django.conf import settings
        if settings.configured:
            return True
    except Exception:
        pass

    try:
        import django
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "route_system.settings")
        django.setup()
        return True
    except Exception:
        return False


def safe_get_all_drivers():
    try:
        if not ensure_django():
            return []
        from routing.models import Driver
        return list(Driver.objects.all().order_by("driver_code"))
    except Exception:
        return []


def build_driver_record(driver, profiles):
    code = normalize_driver_code(getattr(driver, "driver_code", ""))
    profile = profiles.get(code, {})
    custom_slot = normalize_schedule_slot(profile.get("schedule_slot"))
    default_slot = normalize_schedule_slot(code)
    effective_slot = custom_slot or default_slot
    is_slot_auto = bool(default_slot and not custom_slot)
    is_active = bool(profile.get("is_active", True))
    display_name = normalize_text(profile.get("display_name", ""))
    driver_label = format_schedule_driver_label(code, display_name, effective_slot) if effective_slot else (f"{code}｜{display_name}" if display_name else code)

    return {
        "id": getattr(driver, "id", None),
        "driver_code": code,
        "depot_id": getattr(driver, "depot_id", None),
        "max_minutes": getattr(driver, "max_minutes", None),
        "password": getattr(driver, "password", ""),
        "display_name": display_name,
        "phone": normalize_text(profile.get("phone", "")),
        "note": normalize_text(profile.get("note", "")),
        "is_active": is_active,
        "schedule_slot": effective_slot,
        "schedule_slot_raw": custom_slot,
        "schedule_slot_auto": is_slot_auto,
        "schedule_depot_code": slot_to_depot_code(effective_slot),
        "schedule_depot_name": slot_to_depot_name(effective_slot),
        "driver_label": driver_label,
        "created_at": getattr(driver, "created_at", None),
    }


def build_admin_driver_payload(drivers=None):
    profiles = load_profiles()
    drivers = list(drivers) if drivers is not None else safe_get_all_drivers()
    items = [build_driver_record(driver, profiles) for driver in drivers]
    items.sort(key=lambda item: schedule_sort_key(item.get("schedule_slot"), item.get("driver_code")))

    active_items = [item for item in items if item.get("is_active")]
    active_with_slot = [item for item in active_items if item.get("schedule_slot")]
    slot_usage = Counter(item.get("schedule_depot_code") for item in active_with_slot if item.get("schedule_depot_code"))

    summary = {
        "total_count": len(items),
        "active_count": len(active_items),
        "inactive_count": len(items) - len(active_items),
        "active_with_slot_count": len(active_with_slot),
        "active_without_slot_count": len(active_items) - len(active_with_slot),
        "slot_usage": {
            "Wugu": slot_usage.get("Wugu", 0),
            "Pingzhen": slot_usage.get("Pingzhen", 0),
        },
        "slot_limits": DEPOT_SLOT_LIMITS.copy(),
        "active_limit": ACTIVE_DRIVER_LIMIT,
    }

    return {
        "drivers": items,
        "profiles": profiles,
        "summary": summary,
        "fixed_slots": [
            {
                "slot": item["slot"],
                "label": slot_to_label(item["slot"]),
                "depot_code": item["depot_code"],
                "depot_name": item["depot_name"],
                "slot_index": item["slot_index"],
            }
            for item in FIXED_SLOT_CONFIG
        ],
    }


def validate_driver_constraints(drivers, edit_driver_id, edit_driver_code, is_active, schedule_slot):
    proposed = []
    edit_driver_code = normalize_driver_code(edit_driver_code)
    schedule_slot = normalize_schedule_slot(schedule_slot)

    for item in drivers:
        cloned = dict(item)
        if int(cloned.get("id") or 0) == int(edit_driver_id or 0):
            cloned["driver_code"] = edit_driver_code or cloned.get("driver_code")
            cloned["is_active"] = bool(is_active)
            cloned["schedule_slot"] = schedule_slot
            cloned["schedule_depot_code"] = slot_to_depot_code(schedule_slot)
        proposed.append(cloned)

    if edit_driver_id in [None, 0, "", "0"]:
        proposed.append(
            {
                "id": 0,
                "driver_code": edit_driver_code,
                "is_active": bool(is_active),
                "schedule_slot": schedule_slot,
                "schedule_depot_code": slot_to_depot_code(schedule_slot),
            }
        )

    active_items = [item for item in proposed if item.get("is_active")]
    if len(active_items) > ACTIVE_DRIVER_LIMIT:
        return False, f"啟用中的司機帳號最多只能 {ACTIVE_DRIVER_LIMIT} 位，請先停用其他司機再儲存。"

    slot_to_driver = {}
    for item in active_items:
        slot = normalize_schedule_slot(item.get("schedule_slot"))
        if not slot:
            continue
        code = normalize_driver_code(item.get("driver_code"))
        if slot in slot_to_driver and slot_to_driver[slot] != code:
            return False, f"排程席位 {slot} 已被 {slot_to_driver[slot]} 使用，請改選其他席位。"
        slot_to_driver[slot] = code

    slot_usage = Counter(slot_to_depot_code(slot) for slot in slot_to_driver.keys())
    for depot_code, limit in DEPOT_SLOT_LIMITS.items():
        if slot_usage.get(depot_code, 0) > limit:
            depot_name = "五股總部" if depot_code == "Wugu" else "平鎮總部"
            return False, f"{depot_name} 的排程席位最多只能 {limit} 位。"

    return True, ""


def build_schedule_driver_slots():
    admin_payload = build_admin_driver_payload()
    drivers = admin_payload["drivers"]

    active_by_slot = {}
    for item in drivers:
        if not item.get("is_active"):
            continue
        slot = normalize_schedule_slot(item.get("schedule_slot"))
        if not slot:
            continue
        if slot not in active_by_slot:
            active_by_slot[slot] = item

    slots = []
    for config in FIXED_SLOT_CONFIG:
        slot = config["slot"]
        assigned = active_by_slot.get(slot)
        if assigned:
            slots.append(
                {
                    "slot_code": slot,
                    "driver_code": assigned["driver_code"],
                    "driver_label": assigned["driver_label"],
                    "display_name": assigned.get("display_name", ""),
                    "depot_code": config["depot_code"],
                    "depot_name": config["depot_name"],
                    "slot_index": config["slot_index"],
                    "is_default": False,
                }
            )
        else:
            slots.append(
                {
                    "slot_code": slot,
                    "driver_code": slot,
                    "driver_label": default_driver_label_from_slot(slot),
                    "display_name": "",
                    "depot_code": config["depot_code"],
                    "depot_name": config["depot_name"],
                    "slot_index": config["slot_index"],
                    "is_default": True,
                }
            )

    return slots