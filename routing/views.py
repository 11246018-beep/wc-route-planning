from pathlib import Path
import json
import math
import subprocess
import sys
import threading
import time
import os
import shutil

from django.core.paginator import Paginator
from django.db.models import Q
from django.db import connection, connections
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST

import pandas as pd

from .models import (
    CompanyDepot,
    CompanyProfile,
    CompanyScheduleSettings,
    DriverCompanyProfile,
    ServicePoint,
    ServicePointCompanyProfile,
    Driver,
    UserCompanyProfile,
)
from .services.driver_roster import get_driver_assignment, normalize_schedule_slot, schedule_sort_key
from .security import find_driver_by_code, is_hashed_password, make_driver_token, verify_driver_password
from .tenant import (
    DEFAULT_COMPANY_KEY,
    company_output_dir,
    current_company_output_dir,
    ensure_tenant_output_dir,
    get_default_company,
    get_driver_company,
    find_company_by_key,
    find_driver_for_company,
    get_user_company,
    normalize_company_key,
    serialize_company,
    tenant_file_path,
)

from collections import defaultdict
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://evwzonunmjvulzitxjmn.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV2d3pvbnVubWp2dWx6aXR4am1uIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI3OTAzOTUsImV4cCI6MjA4ODM2NjM5NX0.lWMaSu_B6q4AhzAxFykA6YBkwMN0QqNptAoUaraM2E4")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def health_api(request):
    return JsonResponse({"ok": True, "message": "Dispatch Nav API OK"})


def admin_company_driver_codes(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return []
    if user and user.is_authenticated and is_system_admin(user):
        return None
    company = get_user_company(user)
    if not getattr(company, "id", None):
        return []
    return [
        str(code or "").strip().upper()
        for code in DriverCompanyProfile.objects.filter(company=company).values_list("driver_code", flat=True)
        if str(code or "").strip()
    ]


def admin_company_point_addresses(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return []
    if is_system_admin(user):
        return None
    company = get_user_company(user)
    if not getattr(company, "id", None):
        return []

    point_ids = list(
        ServicePointCompanyProfile.objects
        .filter(company=company)
        .values_list("service_point_id", flat=True)
    )
    if not point_ids:
        return []

    return [
        str(address or "").strip()
        for address in ServicePoint.objects
        .filter(id__in=point_ids)
        .exclude(address__isnull=True)
        .values_list("address", flat=True)
        if str(address or "").strip()
    ]


def append_driver_company_filter(sql, params, request, column="driver_code"):
    user = getattr(request, "user", None)
    if user and user.is_authenticated and not is_system_admin(user):
        company = get_user_company(user)
        if getattr(company, "id", None):
            codes = admin_company_driver_codes(request)
            addresses = admin_company_point_addresses(request)
            params.append(company.key)
            if codes and addresses:
                params.extend([codes, addresses])
                return (
                    f"{sql} AND (company_key = %s OR "
                    f"(NULLIF(company_key, '') IS NULL AND UPPER({column}) = ANY(%s) AND stop_address = ANY(%s)))"
                ), params
            return f"{sql} AND company_key = %s", params
    codes = admin_company_driver_codes(request)
    if codes is None:
        return sql, params
    if not codes:
        return f"{sql} AND 1=0", params
    params.append(codes)
    return f"{sql} AND UPPER({column}) = ANY(%s)", params


def toilet_demand_analysis_api(request):
    try:
        response = (
            supabase.table("uploaded_photos")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )

        records = response.data or []
        allowed_driver_codes = admin_company_driver_codes(request)
        if allowed_driver_codes is not None:
            allowed = set(allowed_driver_codes)
            records = [
                r for r in records
                if str(r.get("driver_code") or "").strip().upper() in allowed
            ]

        # 只看清掃前
        before_records = [
            r for r in records
            if str(r.get("photo_type")).lower() in ["前", "before"]
        ]

        grouped = defaultdict(list)

        for record in before_records:
            key = (
                record.get("stop_address")
                or record.get("point_key")
                or "未知點位"
            )
            grouped[key].append(record)

        result = []

        for address, items in grouped.items():
            # 最新排序
            items.sort(key=lambda x: x.get("created_at", ""), reverse=True)

            # 最近三次
            latest_three = items[:3]

            # 必須滿三次
            if len(latest_three) < 3:
                continue

            # 三次都不合格
            all_failed = all(
                (
                    str(r.get("is_qualified")).lower() == "false"
                    or str(r.get("review_status")) == "不合格"
                    or str(r.get("is_risk")).lower() == "true"
                )
                for r in latest_three
            )

            if all_failed:
                result.append({
                    "address": address,
                    "status": "需求量偏高",
                    "latest_time": latest_three[0].get("created_at"),
                    "ids": [str(r.get("id")) for r in latest_three if r.get("id") is not None],
                    "count": len(latest_three),
                })

        return JsonResponse({"ok": True, "records": result})

    except Exception as e:
        return JsonResponse({"ok": False, "message": str(e)}, status=500)


@csrf_exempt
def toilet_demand_analysis_delete_api(request):
    if request.method == "OPTIONS":
        return JsonResponse({"ok": True})

    if request.method != "POST":
        return JsonResponse({"ok": False, "message": "只允許 POST"}, status=405)

    # 只讀帳號不能刪除分析來源紀錄。這裡回 JSON，不做 redirect，避免前端拿到 HTML 後解析失敗。
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return JsonResponse({"ok": False, "message": "請先登入管理者帳號。"}, status=403)
    if not is_manager(request.user):
        return JsonResponse({"ok": False, "message": "權限不足：只讀人員不能刪除清掃分析紀錄。"}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        payload = {}

    raw_ids = payload.get("ids") or []
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    ids = [str(v).strip() for v in raw_ids if str(v).strip()]

    address = str(payload.get("address") or "").strip()

    if not ids and not address:
        return JsonResponse({"ok": False, "message": "缺少要刪除的點位或紀錄 ID"}, status=400)

    try:
        with connection.cursor() as cursor:
            if ids:
                sql, params = append_driver_company_filter(
                    "DELETE FROM uploaded_photos WHERE id::text = ANY(%s)",
                    [ids],
                    request,
                )
                cursor.execute(
                    f"{sql} RETURNING id",
                    params,
                )
            else:
                sql = """
                    DELETE FROM uploaded_photos
                    WHERE (stop_address = %s OR point_key = %s)
                      AND (photo_type = 'before' OR photo_type = '前')
                      AND (
                        is_qualified = false
                        OR review_status = '不合格'
                        OR is_risk = true
                      )
                """
                sql, params = append_driver_company_filter(sql, [address, address], request)
                cursor.execute(
                    f"{sql} RETURNING id",
                    params,
                )

            deleted_rows = cursor.fetchall()

        deleted_ids = [str(row[0]) for row in deleted_rows]
        write_admin_log(
            request,
            "刪除可能需新增點位分析紀錄",
            address or ",".join(ids),
            {"requested_ids": ids, "deleted_ids": deleted_ids, "deleted_count": len(deleted_ids)},
        )

        return JsonResponse({
            "ok": True,
            "deleted_count": len(deleted_rows),
            "deleted_ids": deleted_ids,
        })
    except Exception as e:
        return JsonResponse({"ok": False, "message": str(e)}, status=500)


BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

ACTION_LOG_PATH = OUTPUT_DIR / "admin_action_logs.jsonl"


def is_super_admin(user):
    return bool(user and user.is_authenticated and user.is_superuser)


def is_system_admin(user):
    return bool(user and user.is_authenticated and user.username == "system_admin" and user.is_superuser)


def is_company_super_admin(user):
    return bool(is_super_admin(user) and not is_system_admin(user))


def is_manager(user):
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def role_label(user):
    if not getattr(user, "is_active", False):
        return "待審核 / 已停用"
    if getattr(user, "username", "") == "system_admin":
        return "系統管理員"
    if getattr(user, "is_superuser", False):
        return "高階管理者"
    if getattr(user, "is_staff", False):
        return "管理者"
    return "只讀人員"


def ensure_admin_superuser(username="admin"):
    """讓既有 admin 帳號固定成高階管理者，避免門禁把自己鎖在外面。"""
    try:
        user = User.objects.filter(username=username).first()
        if user and (not user.is_active or not user.is_staff or not user.is_superuser):
            user.is_active = True
            user.is_staff = True
            user.is_superuser = True
            user.save(update_fields=["is_active", "is_staff", "is_superuser"])
        return user
    except Exception:
        return None


def ensure_system_admin():
    """建立平台層系統管理員。這個帳號只負責公司租戶管理，不屬於任何公司。"""
    try:
        user, _ = User.objects.get_or_create(username="system_admin")
        user.set_password("admin")
        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["password", "is_active", "is_staff", "is_superuser"])
        try:
            UserCompanyProfile.objects.filter(user=user).delete()
        except Exception:
            pass
        return user
    except Exception:
        return None


def active_company_options(include_system=False):
    try:
        get_default_company()
        companies = list(CompanyProfile.objects.filter(is_active=True).order_by("name", "key"))
    except Exception:
        companies = []
    options = []
    if include_system:
        options.append({"key": "__system__", "name": "系統管理員"})
    options.extend({"key": company.key, "name": company.name} for company in companies)
    return options


def company_scoped_username(company, login_name):
    raw = str(login_name or "").strip()
    key = normalize_company_key(getattr(company, "key", "") or "company")
    return f"{key}__{raw}"


def public_company_username(user, company=None):
    username = str(getattr(user, "username", "") or "")
    if company:
        prefix = f"{normalize_company_key(getattr(company, 'key', ''))}__"
        if username.startswith(prefix):
            return username[len(prefix):]
    return username


def find_company_user(company, login_name):
    raw = str(login_name or "").strip()
    if not raw or not getattr(company, "id", None):
        return None
    scoped = company_scoped_username(company, raw)
    profiles = list(
        UserCompanyProfile.objects.select_related("user")
        .filter(company=company, user__username__in=[raw, scoped])
    )
    for profile in profiles:
        if profile.user.username == raw:
            return profile.user
    for profile in profiles:
        if profile.user.username == scoped:
            return profile.user
    return None


def allocate_company_username(company, login_name):
    raw = str(login_name or "").strip()
    if not raw:
        return ""
    if find_company_user(company, raw):
        return ""
    if not User.objects.filter(username=raw).exists():
        return raw
    scoped = company_scoped_username(company, raw)
    if not User.objects.filter(username=scoped).exists():
        return scoped
    index = 2
    while User.objects.filter(username=f"{scoped}_{index}").exists():
        index += 1
    return f"{scoped}_{index}"


def get_client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def write_admin_log(request, action, target="", extra=None):
    """用 JSONL 檔紀錄管理端重要操作，不新增資料表，降低破壞既有功能風險。"""
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        user = getattr(request, "user", None)
        extra = dict(extra or {})
        company_key = str(extra.get("company") or "").strip()
        company_name = ""
        if company_key:
            company = CompanyProfile.objects.filter(key=company_key).first()
            company_name = company.name if company else company_key
        elif user and user.is_authenticated and is_system_admin(user):
            company_key = "__system__"
            company_name = "系統管理員"
        elif user and user.is_authenticated:
            company = get_user_company(user)
            if getattr(company, "id", None):
                company_key = company.key
                company_name = company.name
        display_username = user.username if user and user.is_authenticated else "anonymous"
        if user and user.is_authenticated and company_key and company_key != "__system__":
            company = CompanyProfile.objects.filter(key=company_key).first()
            if company:
                display_username = public_company_username(user, company)
        row = {
            "time": timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M:%S"),
            "username": display_username,
            "role": role_label(user) if user and user.is_authenticated else "未登入",
            "company_key": company_key,
            "company_name": company_name,
            "action": action,
            "target": str(target or ""),
            "ip": get_client_ip(request),
            "extra": extra,
        }
        with ACTION_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def log_belongs_to_company(log, company):
    if not getattr(company, "id", None):
        return False
    company_key = str(log.get("company_key") or (log.get("extra") or {}).get("company") or "").strip()
    if company_key:
        return company_key == company.key
    username = str(log.get("username") or "").strip()
    target = str(log.get("target") or "").strip()
    prefix = f"{normalize_company_key(getattr(company, 'key', ''))}__"
    return bool(
        (username.startswith(prefix) and find_company_user(company, username))
        or (target.startswith(prefix) and find_company_user(company, target))
    )


def read_admin_logs(limit=300, company=None):
    if not ACTION_LOG_PATH.exists():
        return []
    try:
        lines = ACTION_LOG_PATH.read_text(encoding="utf-8").splitlines()
        logs = []
        for line in lines[-limit:]:
            try:
                log = json.loads(line)
                if company is not None and not log_belongs_to_company(log, company):
                    continue
                logs.append(log)
            except Exception:
                pass
        return list(reversed(logs))
    except Exception:
        return []

VARIANT_LABELS = {
    "normal": "不跨縣市",
    "cross": "可跨縣市",
    "compact": "跨縣市精簡版",
}

VARIANT_FILES = {
    "normal": "routes_normal.json",
    "cross": "routes_cross.json",
    "compact": "routes_compact.json",
}

ESG_CO2_KG_PER_KM = 0.21
ESG_FUEL_KM_PER_LITER = 10.0
ESG_BASELINE_FILE = "old_routes.json"

DEFAULT_SCHEDULE_SETTINGS = {
    "default_route_variant": "normal",
    "daily_work_minutes": 540,
    "default_service_minutes": 10,
    "driver_limit": 14,
    "schedule_days": 6,
    "depot_name": "總部",
    "depot_address": "",
    "depot_lat": None,
    "depot_lon": None,
    "co2_kg_per_km": ESG_CO2_KG_PER_KM,
}

RUN_LOCK = threading.Lock()
RUN_STATE = {
    "running": False,
    "finished": False,
    "success": False,
    "variant": "normal",
    "message": "尚未開始重新計算。",
    "started_at": None,
    "ended_at": None,
    "elapsed_sec": 0,
    "progress": 0,
    "run_meta": None,
}


def _safe_elapsed(started_at):
    if not started_at:
        return 0
    return max(0, int(time.time() - started_at))


def _set_run_state(**kwargs):
    with RUN_LOCK:
        RUN_STATE.update(kwargs)


def _snapshot_run_state(variant="normal"):
    with RUN_LOCK:
        data = dict(RUN_STATE)
    elapsed = _safe_elapsed(data.get("started_at")) if data.get("running") else int(data.get("elapsed_sec") or 0)
    if data.get("running"):
        # run_all.py 目前沒有逐站回報，這裡用時間推估讓使用者知道系統仍在執行。
        estimated = min(95, 8 + int(elapsed / 6))
        progress = max(int(data.get("progress") or 0), estimated)
    else:
        progress = 100 if data.get("finished") and data.get("success") else int(data.get("progress") or 0)
    data["elapsed_sec"] = elapsed
    data["progress"] = progress
    data["variant"] = data.get("variant") or variant
    return data


def schedule_settings_env(settings):
    return {
        "DISPATCH_DEFAULT_ROUTE_VARIANT": str(getattr(settings, "default_route_variant", "normal") or "normal"),
        "DISPATCH_DAILY_WORK_MINUTES": str(getattr(settings, "daily_work_minutes", 540) or 540),
        "DISPATCH_DEFAULT_SERVICE_MINUTES": str(getattr(settings, "default_service_minutes", 10) or 10),
        "DISPATCH_DRIVER_LIMIT": str(getattr(settings, "driver_limit", 14) or 14),
        "DISPATCH_SCHEDULE_DAYS": str(getattr(settings, "schedule_days", 6) or 6),
        "DISPATCH_DEPOT_NAME": str(getattr(settings, "depot_name", "") or ""),
        "DISPATCH_DEPOT_ADDRESS": str(getattr(settings, "depot_address", "") or ""),
        "DISPATCH_DEPOT_LAT": "" if getattr(settings, "depot_lat", None) is None else str(settings.depot_lat),
        "DISPATCH_DEPOT_LON": "" if getattr(settings, "depot_lon", None) is None else str(settings.depot_lon),
        "DISPATCH_CO2_KG_PER_KM": str(getattr(settings, "co2_kg_per_km", ESG_CO2_KG_PER_KM) or ESG_CO2_KG_PER_KM),
    }


def get_company_schedule_settings(company):
    if not getattr(company, "id", None):
        class DefaultSettings:
            pass
        settings = DefaultSettings()
        for key, value in DEFAULT_SCHEDULE_SETTINGS.items():
            setattr(settings, key, value)
        return settings
    settings, _ = CompanyScheduleSettings.objects.get_or_create(
        company=company,
        defaults=DEFAULT_SCHEDULE_SETTINGS,
    )
    return settings


def serialize_schedule_settings(settings):
    return {
        "default_route_variant": getattr(settings, "default_route_variant", "normal") or "normal",
        "daily_work_minutes": to_int(getattr(settings, "daily_work_minutes", 540), 540),
        "default_service_minutes": to_int(getattr(settings, "default_service_minutes", 10), 10),
        "driver_limit": to_int(getattr(settings, "driver_limit", 14), 14),
        "schedule_days": to_int(getattr(settings, "schedule_days", 6), 6),
        "depot_name": getattr(settings, "depot_name", "") or "",
        "depot_address": getattr(settings, "depot_address", "") or "",
        "depot_lat": getattr(settings, "depot_lat", None),
        "depot_lon": getattr(settings, "depot_lon", None),
        "co2_kg_per_km": round(float(getattr(settings, "co2_kg_per_km", ESG_CO2_KG_PER_KM) or ESG_CO2_KG_PER_KM), 4),
    }


def update_schedule_settings_from_post(settings, post):
    default_route_variant = (post.get("default_route_variant") or "normal").strip()
    if default_route_variant not in VARIANT_LABELS:
        default_route_variant = "normal"

    settings.default_route_variant = default_route_variant
    settings.daily_work_minutes = max(to_int(post.get("daily_work_minutes"), 540), 1)
    settings.default_service_minutes = max(to_int(post.get("default_service_minutes"), 10), 0)
    settings.driver_limit = max(to_int(post.get("driver_limit"), 14), 1)
    settings.schedule_days = max(to_int(post.get("schedule_days"), 1), 1)
    settings.depot_name = (post.get("depot_name") or "").strip()
    settings.depot_address = (post.get("depot_address") or "").strip()
    settings.depot_lat = to_float(post.get("depot_lat"))
    settings.depot_lon = to_float(post.get("depot_lon"))
    co2_kg_per_km = to_float(post.get("co2_kg_per_km"))
    settings.co2_kg_per_km = co2_kg_per_km if co2_kg_per_km and co2_kg_per_km > 0 else ESG_CO2_KG_PER_KM
    settings.save()
    return settings


def sync_primary_depot_from_settings(company, settings):
    name = (getattr(settings, "depot_name", "") or "主要場站").strip() or "主要場站"
    CompanyDepot.objects.update_or_create(
        company=company,
        code="main",
        defaults={
            "name": name,
            "address": getattr(settings, "depot_address", "") or "",
            "lat": getattr(settings, "depot_lat", None),
            "lon": getattr(settings, "depot_lon", None),
            "is_active": True,
            "sort_order": 1,
        },
    )


def serialize_company_depot(depot):
    return {
        "id": depot.id,
        "code": depot.code,
        "name": depot.name,
        "address": depot.address,
        "lat": depot.lat,
        "lon": depot.lon,
        "is_active": depot.is_active,
        "sort_order": depot.sort_order,
    }


def _run_scheduler_background(variant, output_dir=None, company_key="", settings_env=None):
    output_dir = output_dir or OUTPUT_DIR
    settings_env = settings_env or {}
    _set_run_state(
        running=True,
        finished=False,
        success=False,
        variant=variant,
        message="",
        started_at=time.time(),
        ended_at=None,
        elapsed_sec=0,
        progress=5,
        run_meta=None,
    )
    try:
        result = subprocess.run(
            [sys.executable, "run_all.py"],
            cwd=str(BASE_DIR),
            env={**os.environ, **settings_env, "DISPATCH_COMPANY_KEY": str(company_key or "")},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=3600,
        )

        log_path = OUTPUT_DIR / "run_all_last.log"
        inner_log_text = ""
        if log_path.exists():
            try:
                inner_log_text = log_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                inner_log_text = ""
        log_text = []
        log_text.append(f"Return code: {result.returncode}\n")
        log_text.append("\n=== STDOUT ===\n")
        log_text.append(result.stdout or "")
        log_text.append("\n\n=== STDERR ===\n")
        log_text.append(result.stderr or "")
        if result.returncode != 0 and inner_log_text:
            log_text.append("\n\n=== INNER RUN LOG ===\n")
            log_text.append(inner_log_text)
        log_path.write_text("".join(log_text), encoding="utf-8")

        if result.returncode == 0:
            if output_dir != OUTPUT_DIR:
                copy_scheduler_outputs_to_tenant(output_dir)
            company = find_company_by_key(company_key) if company_key else None
            run_meta = extract_variant_run_meta(variant, output_dir, company=company)
            _set_run_state(
                running=False,
                finished=True,
                success=True,
                message=run_meta.get("message") or "排程已重新執行完成。",
                ended_at=time.time(),
                elapsed_sec=_safe_elapsed(RUN_STATE.get("started_at")),
                progress=100,
                run_meta=run_meta,
            )
        else:
            _set_run_state(
                running=False,
                finished=True,
                success=False,
                message="排程執行失敗，請檢查 output/run_all_last.log。",
                ended_at=time.time(),
                elapsed_sec=_safe_elapsed(RUN_STATE.get("started_at")),
                progress=100,
                run_meta=None,
            )
    except Exception as e:
        (OUTPUT_DIR / "run_all_last.log").write_text(
            f"Exception while running scheduler:\n{e}",
            encoding="utf-8",
        )
        _set_run_state(
            running=False,
            finished=True,
            success=False,
            message=f"排程執行失敗：{e}",
            ended_at=time.time(),
            elapsed_sec=_safe_elapsed(RUN_STATE.get("started_at")),
            progress=100,
            run_meta=None,
        )
    finally:
        connections.close_all()


def to_float(value):
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value, default=0):
    try:
        if value is None:
            return default
        text = str(value).strip()
        if text == "" or text.lower() == "nan":
            return default
        return int(float(text))
    except Exception:
        return default


def to_bool(value):
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in ["1", "true", "t", "yes", "y", "on", "是", "有", "v", "✔"]


def clean_text(value):
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return None
    return text


def driver_sort_key(code, schedule_slot=""):
    return schedule_sort_key(schedule_slot, code)


def driver_label(code):
    s = str(code or "").upper()
    if s.startswith("P") and s[1:].isdigit():
        return f"{s}｜平鎮{s[1:].lstrip('0') or '0'}"
    if s.startswith("W") and s[1:].isdigit():
        return f"{s}｜五股{s[1:].lstrip('0') or '0'}"
    return s


from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def driver_companies_api(request):
    """Return active companies for the driver app login selector."""
    if request.method == "OPTIONS":
        response = JsonResponse({"ok": True})
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Driver-Token"
        response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        return response
    if request.method != "GET":
        return JsonResponse({"ok": False, "message": "只支援 GET"}, status=405)

    companies = list(
        CompanyProfile.objects.filter(is_active=True)
        .order_by("name", "key")
        .values("key", "name")
    )
    response = JsonResponse({"ok": True, "companies": companies})
    response["Access-Control-Allow-Origin"] = "*"
    return response


@csrf_exempt
def driver_login_api(request):
    if request.method == "OPTIONS":
        response = JsonResponse({"ok": True})
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Driver-Token"
        response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return response

    if request.method != "POST":
        return JsonResponse(
            {"success": False, "message": "只支援 POST"},
            status=405
        )

    try:
        data = json.loads(request.body)
        driver_code = data.get("driver_code", "").strip().upper()
        password = data.get("password", "").strip()
        company_key = (data.get("company_key") or "").strip()

        if not driver_code or not password:
            return JsonResponse(
                {"success": False, "message": "請輸入司機代碼與密碼"},
                status=400
            )

        company = find_company_by_key(company_key) if company_key else None
        if company_key and not company:
            return JsonResponse(
                {"success": False, "message": "找不到選擇的公司"},
                status=400,
            )

        driver = find_driver_for_company(driver_code, company=company) if company else None
        if driver is None and not company:
            matches = list(Driver.objects.filter(driver_code__iexact=driver_code)[:2])
            if len(matches) > 1:
                return JsonResponse(
                    {"success": False, "message": "此司機代碼存在於多家公司，請選擇公司代碼後再登入"},
                    status=400,
                )
            driver = matches[0] if matches else None
        if not driver:
            return JsonResponse(
                {"success": False, "message": "找不到司機帳號"},
                status=401
            )

        if not verify_driver_password(driver, password):
            return JsonResponse(
                {"success": False, "message": "密碼錯誤"},
                status=401
            )

        company = get_driver_company(driver, company_key)
        token = make_driver_token(driver, company)
        return JsonResponse({
            "success": True,
            "driver_code": driver.driver_code,
            "company_key": company.key,
            "company_name": company.name,
            "name": driver.driver_code,
            "depot_id": driver.depot_id,
            "max_minutes": driver.max_minutes,
            "token": token,
        })

    except Exception as e:
        return JsonResponse(
            {"success": False, "message": f"登入失敗：{str(e)}"},
            status=500
        )


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def haversine_km(lat1, lon1, lat2, lon2):
    lat1 = to_float(lat1)
    lon1 = to_float(lon1)
    lat2 = to_float(lat2)
    lon2 = to_float(lon2)
    if None in [lat1, lon1, lat2, lon2]:
        return 0.0
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def route_metric(route, key):
    metrics = route.get("metrics") if isinstance(route, dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    value = to_float(metrics.get(key))
    return value if value is not None else 0.0


def route_distance_km(route, prefer_metric=True):
    dist = route_metric(route, "dist_km")
    if prefer_metric and dist > 0:
        return dist

    points = []
    depot = route.get("depot") if isinstance(route, dict) else {}
    if isinstance(depot, dict):
        depot_lat = to_float(depot.get("lat"))
        depot_lon = to_float(depot.get("lon"))
        if depot_lat is not None and depot_lon is not None:
            points.append((depot_lat, depot_lon))

    for stop in route.get("stops", []) if isinstance(route, dict) else []:
        if not isinstance(stop, dict):
            continue
        lat = to_float(stop.get("lat") or stop.get("latitude") or stop.get("Latitude"))
        lon = to_float(stop.get("lon") or stop.get("lng") or stop.get("longitude") or stop.get("Longitude"))
        if lat is not None and lon is not None:
            points.append((lat, lon))

    if len(points) < 2:
        return 0.0

    if isinstance(depot, dict) and points[0] != points[-1]:
        points.append(points[0])

    return sum(haversine_km(a[0], a[1], b[0], b[1]) for a, b in zip(points, points[1:]))


def build_esg_summary(routes, variant, output_dir=None, settings=None):
    output_dir = output_dir or OUTPUT_DIR
    emission_factor = ESG_CO2_KG_PER_KM
    if settings is not None:
        try:
            emission_factor = float(getattr(settings, "co2_kg_per_km", ESG_CO2_KG_PER_KM) or ESG_CO2_KG_PER_KM)
        except Exception:
            emission_factor = ESG_CO2_KG_PER_KM
    total_distance = sum(route_distance_km(route) for route in routes)
    current_coord_distance = sum(route_distance_km(route, prefer_metric=False) for route in routes)
    total_drive = sum(route_metric(route, "drive_min") for route in routes)
    total_service = sum(route_metric(route, "service_min") for route in routes)
    total_work = sum(route_metric(route, "total_min") for route in routes)
    stop_count = sum(
        to_int(route.get("stop_count"), len(route.get("stops", []) or []))
        for route in routes
        if isinstance(route, dict)
    )

    baseline_distance = 0.0
    baseline_path = output_dir / ESG_BASELINE_FILE
    if baseline_path.exists():
        raw = load_json(baseline_path)
        baseline_routes = raw.get("routes", []) if isinstance(raw, dict) else []
        baseline_metric_distance = sum(route_metric(route, "dist_km") for route in baseline_routes)
        baseline_coord_distance = sum(route_distance_km(route, prefer_metric=False) for route in baseline_routes)
        road_factor = total_distance / current_coord_distance if current_coord_distance > 0 else 1.0
        if baseline_metric_distance > 0:
            baseline_distance = baseline_metric_distance
        elif baseline_coord_distance > 0:
            baseline_distance = baseline_coord_distance * max(road_factor, 1.0)

    if baseline_distance <= 0:
        baseline_distance = total_distance / 0.9 if total_distance > 0 else 0.0
    elif total_distance > 0 and baseline_distance <= total_distance:
        baseline_distance = total_distance / 0.9

    saved_distance = max(baseline_distance - total_distance, 0.0)
    co2 = total_distance * emission_factor
    baseline_co2 = baseline_distance * emission_factor
    saved_co2 = saved_distance * emission_factor
    fuel_liters = total_distance / ESG_FUEL_KM_PER_LITER if ESG_FUEL_KM_PER_LITER > 0 else 0.0

    return {
        "variant": variant,
        "emission_factor_kg_per_km": round(emission_factor, 3),
        "fuel_km_per_liter": ESG_FUEL_KM_PER_LITER,
        "route_count": len(routes),
        "stop_count": stop_count,
        "total_distance_km": round(total_distance, 2),
        "total_drive_min": round(total_drive, 1),
        "total_service_min": round(total_service, 1),
        "total_work_min": round(total_work, 1),
        "estimated_fuel_liter": round(fuel_liters, 2),
        "estimated_co2_kg": round(co2, 2),
        "baseline_distance_km": round(baseline_distance, 2),
        "baseline_co2_kg": round(baseline_co2, 2),
        "saved_distance_km": round(saved_distance, 2),
        "saved_co2_kg": round(saved_co2, 2),
        "saved_pct": round((saved_distance / baseline_distance) * 100, 1) if baseline_distance > 0 else 0.0,
        "baseline_label": "原始/人工路線估算",
        "note": "碳排為展示估算值，依路線里程與平均車輛排放係數換算；正式導入可依廠商車種、油耗或電動車耗電係數調整。",
    }


def get_current_service_point_count(company=None):
    """回傳目前資料庫 service_points 的即時筆數，避免畫面沿用舊 JSON meta 的歷史最大值。"""
    try:
        if company is not None:
            return int(company_service_points_queryset(company).count())
        return int(ServicePoint.objects.count())
    except Exception:
        return 0


def company_service_point_ids(company):
    try:
        if not getattr(company, "id", None):
            return None
        if getattr(company, "key", "") == DEFAULT_COMPANY_KEY:
            linked_ids = ServicePointCompanyProfile.objects.values_list("service_point_id", flat=True)
            unassigned_ids = list(
                ServicePoint.objects.exclude(id__in=linked_ids).values_list("id", flat=True)
            )
            if unassigned_ids:
                ServicePointCompanyProfile.objects.bulk_create(
                    [
                        ServicePointCompanyProfile(service_point_id=point_id, company=company)
                        for point_id in unassigned_ids
                    ],
                    ignore_conflicts=True,
                )
        ids = list(
            ServicePointCompanyProfile.objects.filter(company=company)
            .values_list("service_point_id", flat=True)
        )
        return ids
    except Exception:
        return None


def company_service_points_queryset(company):
    ids = company_service_point_ids(company)
    if ids is None:
        return ServicePoint.objects.all()
    return ServicePoint.objects.filter(id__in=ids)


def current_company_service_points_queryset(request):
    return company_service_points_queryset(get_user_company(request.user))


def bind_service_point_to_company(service_point, company):
    try:
        if service_point and getattr(company, "id", None):
            ServicePointCompanyProfile.objects.update_or_create(
                service_point_id=service_point.id,
                defaults={"company": company},
            )
    except Exception:
        pass


def service_point_belongs_to_company(service_point_id, company):
    try:
        if not getattr(company, "id", None):
            return True
        return ServicePointCompanyProfile.objects.filter(
            service_point_id=service_point_id,
            company=company,
        ).exists()
    except Exception:
        return True


def normalize_route_meta(meta, company=None):
    """把路線 JSON 的 meta 與目前資料庫點位數同步。

    排程路線本身仍讀 JSON，不動演算法；這裡只修正首頁顯示的
    total_db_points / scheduled_db_points，避免刪除點位後仍顯示曾經的最大筆數。
    """
    data = dict(meta or {})
    current_total = get_current_service_point_count(company)
    if current_total <= 0:
        return data

    unassigned = to_int(data.get("unassigned_db_points"), 0)
    if unassigned < 0 or unassigned > current_total:
        unassigned = 0

    data["total_db_points"] = current_total
    data["unassigned_db_points"] = unassigned
    data["scheduled_db_points"] = max(current_total - unassigned, 0)
    return data


def load_variant_payload(variant, output_dir=None, settings=None, company=None):
    output_dir = output_dir or OUTPUT_DIR
    if variant not in VARIANT_LABELS:
        variant = "normal"

    file_path = output_dir / VARIANT_FILES[variant]
    if not file_path.exists():
        return {
            "ok": False,
            "warning": f"找不到此公司的 {VARIANT_FILES[variant]}，請先在目前公司按「重新計算最佳路徑」。",
            "routes": [],
            "variant": variant,
            "label": VARIANT_LABELS[variant],
            "file_used": None,
            "meta": {},
            "esg": build_esg_summary([], variant, output_dir, settings=settings),
        }

    raw = load_json(file_path)
    routes = raw.get("routes", []) if isinstance(raw, dict) else []
    meta = raw.get("meta", {}) if isinstance(raw, dict) else {}
    meta = normalize_route_meta(meta, company)

    return {
        "ok": True,
        "warning": clean_text(meta.get("note")),
        "routes": routes,
        "variant": variant,
        "label": VARIANT_LABELS[variant],
        "file_used": file_path.name,
        "meta": meta,
        "esg": build_esg_summary(routes, variant, output_dir, settings=settings),
    }


def load_old_payload(output_dir=None):
    output_dir = output_dir or OUTPUT_DIR
    file_path = output_dir / "old_routes.json"
    if not file_path.exists():
        return {
            "ok": False,
            "warning": "找不到 old_routes.json。若要用舊路線疊圖，請先把舊 map.html 放到專案根目錄後，重新執行 run_all.py。",
            "routes": [],
            "file_used": None,
            "meta": {},
        }

    raw = load_json(file_path)
    routes = raw.get("routes", []) if isinstance(raw, dict) else []
    meta = raw.get("meta", {}) if isinstance(raw, dict) else {}

    return {
        "ok": True,
        "warning": clean_text(meta.get("note")),
        "routes": routes,
        "file_used": file_path.name,
        "meta": meta,
    }


def copy_scheduler_outputs_to_tenant(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    filenames = set(VARIANT_FILES.values()) | {
        "routes_new.json",
        "routes_unassigned_strict.json",
        "Unassigned_Points_normal.xlsx",
        "Unassigned_Points_compact.xlsx",
        "Daily_Route_Summary.xlsx",
        "Weekly_Schedule_Summary.xlsx",
        "Driver_Weekly_Load_Strict.xlsx",
        "run_all_last.log",
    }
    for filename in filenames:
        src = OUTPUT_DIR / filename
        if src.exists() and src.is_file():
            shutil.copy2(src, output_dir / filename)


def extract_variant_run_meta(variant, output_dir=None, company=None):
    payload = load_variant_payload(variant, output_dir, company=company)
    meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
    scheduled = to_int(meta.get("scheduled_db_points"), 0)
    unassigned = to_int(meta.get("unassigned_db_points"), 0)
    total = to_int(meta.get("total_db_points"), 0)
    return {
        "variant": variant,
        "label": VARIANT_LABELS.get(variant, variant),
        "scheduled_db_points": scheduled,
        "unassigned_db_points": unassigned,
        "total_db_points": total,
        "assigned_task_count": to_int(meta.get("assigned_task_count"), 0),
        "unassigned_task_count": to_int(meta.get("unassigned_task_count"), 0),
        "unassigned_node_count": to_int(meta.get("unassigned_node_count"), 0),
        "download_key": clean_text(meta.get("unassigned_download_key")) or "",
        "download_filename": clean_text(meta.get("unassigned_download_filename")) or "",
        "message": f"重排完成：已排入 {scheduled} 個點位，未排入 {unassigned} 個點位。" if total > 0 else (clean_text(meta.get("summary_message")) or ""),
    }

def login_view(request):
    """管理者登入。未審核帳號不能登入，admin 帳號固定視為高階管理者。"""
    ensure_system_admin()
    if request.method == "POST":
        try:
            data = json.loads(request.body or "{}")
            u_name = data.get("userid", "").strip()
            p_word = data.get("password", "").strip()
            company_key = (data.get("company_key") or "").strip()

            if not u_name or not p_word:
                return JsonResponse({"success": False, "message": "請輸入管理員帳號與密碼"})

            if not company_key:
                return JsonResponse({"success": False, "message": "請選擇登入身分或公司"})

            if u_name == "admin":
                ensure_admin_superuser("admin")
            if u_name == "system_admin":
                ensure_system_admin()

            company = None
            auth_username = u_name
            if company_key != "__system__":
                company = CompanyProfile.objects.filter(key=company_key, is_active=True).first()
                if not company:
                    return JsonResponse({"success": False, "message": "找不到可登入的公司。"})
                company_user = find_company_user(company, u_name)
                if company_user:
                    auth_username = company_user.username

            existing_user = User.objects.filter(username=auth_username).first()
            if existing_user and not is_hashed_password(existing_user.password) and existing_user.password == p_word:
                existing_user.set_password(p_word)
                existing_user.save(update_fields=["password"])

            user = authenticate(request, username=auth_username, password=p_word)

            if user is not None:
                if not user.is_active:
                    return JsonResponse({
                        "success": False,
                        "message": "此帳號尚未審核或已停用，請聯絡高階管理者。",
                    })
                if is_system_admin(user):
                    if company_key != "__system__":
                        return JsonResponse({"success": False, "message": "系統管理員請選擇「系統管理員」登入。"})
                    login(request, user)
                    write_admin_log(request, "系統管理員登入", user.username)
                    return JsonResponse({"success": True, "redirect_url": "/companies/"})

                if company_key == "__system__":
                    return JsonResponse({"success": False, "message": "一般管理者請選擇所屬公司登入。"})

                profile = UserCompanyProfile.objects.select_related("company").filter(user=user).first()
                if profile and profile.company_id != company.id:
                    return JsonResponse({"success": False, "message": "此帳號不屬於你選擇的公司。"})
                if not profile:
                    return JsonResponse({"success": False, "message": "此帳號尚未綁定公司，請聯絡系統管理員。"})

                login(request, user)
                write_admin_log(request, "管理者登入", public_company_username(user, company), {"company": company.key})
                return JsonResponse({"success": True, "redirect_url": "/home/"})

            # authenticate 對 is_active=False 會直接失敗，所以額外判斷提示更清楚。
            pending_user = User.objects.filter(username=auth_username).first()
            if pending_user and not pending_user.is_active:
                return JsonResponse({
                    "success": False,
                    "message": "此帳號尚未審核或已停用，請等待高階管理者開通。",
                })

            return JsonResponse({"success": False, "message": "管理員帳號或密碼錯誤"})
        except Exception as e:
            return JsonResponse({"success": False, "message": f"系統錯誤: {str(e)}"})

    return render(request, "routing/login.html", {"company_options": active_company_options(include_system=True)})


def logout_view(request):
    if request.user.is_authenticated:
        company = get_user_company(request.user)
        target = public_company_username(request.user, company) if not is_system_admin(request.user) else request.user.username
        extra = {"company": company.key} if getattr(company, "id", None) and not is_system_admin(request.user) else {}
        write_admin_log(request, "管理者登出", target, extra)
    logout(request)
    return redirect("login")


# 註冊頁面
def register_view(request):
    """管理者帳號申請。新帳號預設待審核，不能直接登入。"""
    if request.method == "POST":
        try:
            data = json.loads(request.body or "{}")
            u_name = data.get("username", "").strip()
            p_word = data.get("password", "").strip()
            company_key = (data.get("company_key") or "").strip()

            if not u_name or not p_word:
                return JsonResponse({"success": False, "message": "請填寫完整帳號與密碼"})

            company = CompanyProfile.objects.filter(key=company_key, is_active=True).first()
            if not company:
                return JsonResponse({"success": False, "message": "請選擇要申請加入的公司"})

            internal_username = allocate_company_username(company, u_name)
            if not internal_username:
                return JsonResponse({"success": False, "message": "此公司已經有相同名稱的管理員帳號"})

            user = User.objects.create_user(username=internal_username, password=p_word)
            user.is_active = False
            user.is_staff = False
            user.is_superuser = False
            user.save(update_fields=["is_active", "is_staff", "is_superuser"])
            UserCompanyProfile.objects.update_or_create(user=user, defaults={"company": company})

            # 若系統還沒有 admin，仍不自動開通申請者，避免公開註冊直接變管理員。
            return JsonResponse({
                "success": True,
                "pending": True,
                "message": "帳號申請已送出，請等待高階管理者審核後再登入。",
            })
        except Exception as e:
            return JsonResponse({"success": False, "message": f"申請失敗: {str(e)}"})

    return render(request, "routing/register.html", {"company_options": active_company_options(include_system=False)})


@login_required(login_url="login")
@user_passes_test(is_company_super_admin, login_url="home")
def account_management(request):
    ensure_admin_superuser("admin")
    company = get_user_company(request.user)
    user_ids = UserCompanyProfile.objects.filter(company=company).values_list("user_id", flat=True)
    users = User.objects.filter(id__in=user_ids).exclude(username="system_admin").order_by("-date_joined")
    user_rows = []
    for u in users:
        user_rows.append({
            "obj": u,
            "role": role_label(u),
            "status": "已啟用" if u.is_active else "待審核 / 已停用",
            "last_login_text": timezone.localtime(u.last_login).strftime("%Y-%m-%d %H:%M") if u.last_login else "-",
            "date_joined_text": timezone.localtime(u.date_joined).strftime("%Y-%m-%d %H:%M") if u.date_joined else "-",
        })
    return render(
        request,
        "routing/account_management.html",
        {
            "user_rows": user_rows,
        },
    )


@csrf_exempt
@login_required(login_url="login")
@user_passes_test(is_company_super_admin, login_url="home")
@require_POST
def account_management_action(request, user_id):
    ensure_admin_superuser("admin")
    target = get_object_or_404(User, pk=user_id)
    action = request.POST.get("action", "").strip()
    company = get_user_company(request.user)

    if target.username == "system_admin":
        return redirect("account_management")

    if not UserCompanyProfile.objects.filter(user=target, company=company).exists():
        return redirect("account_management")

    if target.username == "admin" and action in ["disable", "delete", "viewer", "manager"]:
        return redirect("account_management")

    if action == "approve":
        target.is_active = True
        target.is_staff = True
        target.is_superuser = False
        target.save(update_fields=["is_active", "is_staff", "is_superuser"])
        write_admin_log(request, "核准管理者帳號", target.username)
    elif action == "disable":
        target.is_active = False
        target.save(update_fields=["is_active"])
        write_admin_log(request, "停用管理者帳號", target.username)
    elif action == "enable":
        target.is_active = True
        target.save(update_fields=["is_active"])
        write_admin_log(request, "啟用管理者帳號", target.username)
    elif action == "viewer":
        target.is_active = True
        target.is_staff = False
        target.is_superuser = False
        target.save(update_fields=["is_active", "is_staff", "is_superuser"])
        write_admin_log(request, "設定為只讀人員", target.username)
    elif action == "manager":
        target.is_active = True
        target.is_staff = True
        target.is_superuser = False
        target.save(update_fields=["is_active", "is_staff", "is_superuser"])
        write_admin_log(request, "設定為管理者", target.username)
    elif action == "super":
        target.is_active = True
        target.is_staff = True
        target.is_superuser = True
        target.save(update_fields=["is_active", "is_staff", "is_superuser"])
        write_admin_log(request, "設定為高階管理者", target.username)
    elif action == "delete":
        username = target.username
        target.delete()
        write_admin_log(request, "刪除管理者帳號", username)

    return redirect("account_management")


def redirect_company_settings(message="", error=""):
    from urllib.parse import urlencode

    params = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    suffix = f"?{urlencode(params)}" if params else ""
    return redirect(f"/company-settings/{suffix}")


@login_required(login_url="login")
@user_passes_test(is_company_super_admin, login_url="home")
def company_settings(request):
    company = get_user_company(request.user)
    settings = get_company_schedule_settings(company)
    sync_primary_depot_from_settings(company, settings)
    depots = CompanyDepot.objects.filter(company=company).order_by("sort_order", "id")
    return render(
        request,
        "routing/company_settings.html",
        {
            "company": company,
            "settings": settings,
            "schedule_settings": serialize_schedule_settings(settings),
            "route_variant_choices": CompanyScheduleSettings.ROUTE_VARIANT_CHOICES,
            "depots": depots,
            "message": request.GET.get("message", ""),
            "error": request.GET.get("error", ""),
        },
    )


@csrf_exempt
@login_required(login_url="login")
@user_passes_test(is_company_super_admin, login_url="home")
@require_POST
def company_settings_action(request):
    company = get_user_company(request.user)
    action = (request.POST.get("action") or "").strip()

    try:
        if action == "save_schedule_settings":
            settings = get_company_schedule_settings(company)
            update_schedule_settings_from_post(settings, request.POST)
            sync_primary_depot_from_settings(company, settings)
            write_admin_log(request, "更新公司營運設定", company.key, serialize_schedule_settings(settings))
            return redirect_company_settings(message="營運參數已儲存，下一次重新計算會套用。")

        if action == "save_depot":
            depot_id = to_int(request.POST.get("depot_id"), 0)
            code = normalize_company_key(request.POST.get("code") or "depot")
            name = (request.POST.get("name") or "").strip()
            if not code or not name:
                return redirect_company_settings(error="請填寫場站代碼與名稱。")

            conflict = CompanyDepot.objects.filter(company=company, code=code)
            if depot_id:
                conflict = conflict.exclude(id=depot_id)
            if conflict.exists():
                return redirect_company_settings(error="此場站代碼已存在，請換一個代碼。")

            depot = get_object_or_404(CompanyDepot, pk=depot_id, company=company) if depot_id else CompanyDepot(company=company)
            depot.code = code
            depot.name = name
            depot.address = (request.POST.get("address") or "").strip()
            depot.lat = to_float(request.POST.get("lat"))
            depot.lon = to_float(request.POST.get("lon"))
            depot.sort_order = max(to_int(request.POST.get("sort_order"), 1), 1)
            depot.is_active = request.POST.get("is_active") == "on"
            depot.save()
            write_admin_log(request, "儲存公司場站", depot.code, {"company": company.key, "depot": serialize_company_depot(depot)})
            return redirect_company_settings(message="場站資料已儲存。")

        if action == "delete_depot":
            depot_id = to_int(request.POST.get("depot_id"), 0)
            depot = get_object_or_404(CompanyDepot, pk=depot_id, company=company)
            if depot.code == "main":
                return redirect_company_settings(error="主要場站會跟上方倉庫設定同步，不能刪除。")
            code = depot.code
            depot.delete()
            write_admin_log(request, "刪除公司場站", code, {"company": company.key})
            return redirect_company_settings(message="場站已刪除。")

    except Exception as exc:
        return redirect_company_settings(error=f"處理失敗：{exc}")

    return redirect_company_settings(error="未知的操作。")


def company_industry_label(value):
    labels = dict(CompanyProfile.INDUSTRY_CHOICES)
    return labels.get(value, value or "-")


def company_management_rows(selected_company_id=None):
    companies = list(CompanyProfile.objects.all().order_by("key"))
    user_profiles = {
        row.user_id: row
        for row in UserCompanyProfile.objects.select_related("company", "user").all()
    }
    driver_profiles_by_id = {
        row.driver_id: row
        for row in DriverCompanyProfile.objects.select_related("company").all()
        if row.driver_id
    }
    driver_profiles_by_code = {
        row.driver_code.upper(): row
        for row in DriverCompanyProfile.objects.select_related("company").all()
        if row.driver_code
    }

    company_rows = []
    for company in companies:
        assigned_users = [
            public_company_username(profile.user, company)
            for profile in user_profiles.values()
            if profile.company_id == company.id
        ]
        assigned_drivers = [
            profile.driver_code
            for profile in list(driver_profiles_by_id.values()) + [
                profile for profile in driver_profiles_by_code.values()
                if not getattr(profile, "driver_id", None)
            ]
            if profile.company_id == company.id
        ]
        company_rows.append({
            "obj": company,
            "industry_label": company_industry_label(company.industry_type),
            "user_count": len(assigned_users),
            "driver_count": len(assigned_drivers),
            "users_preview": ", ".join(sorted(assigned_users)[:6]),
            "drivers_preview": ", ".join(sorted(assigned_drivers)[:8]),
        })

    users = []
    for user in User.objects.exclude(username="system_admin").order_by("username"):
        profile = user_profiles.get(user.id)
        users.append({
            "obj": user,
            "login_name": public_company_username(user, profile.company if profile else None),
            "role": role_label(user),
            "company": profile.company if profile else None,
        })

    selected_company = None
    if companies:
        selected_company = next((company for company in companies if company.id == selected_company_id), None) or companies[0]

    company_users = []
    if selected_company:
        for row in users:
            company = row.get("company")
            if company and company.id == selected_company.id:
                company_users.append(row)

    drivers = []
    profiles = load_driver_profiles_safe()
    for driver in Driver.objects.all().order_by("driver_code"):
        code = str(driver.driver_code or "").strip().upper()
        profile = driver_profiles_by_id.get(driver.id) or driver_profiles_by_code.get(code)
        driver_profile = profiles.get(code, {})
        drivers.append({
            "obj": driver,
            "code": code,
            "display_name": driver_profile.get("display_name", ""),
            "schedule_slot": driver_profile.get("schedule_slot", ""),
            "company": profile.company if profile else None,
        })

    return company_rows, users, company_users, drivers, selected_company


def set_user_role(user, role):
    role = (role or "").strip()
    user.is_active = True
    if role == "super":
        user.is_staff = True
        user.is_superuser = True
    elif role == "manager":
        user.is_staff = True
        user.is_superuser = False
    else:
        user.is_staff = False
        user.is_superuser = False
    user.save(update_fields=["is_active", "is_staff", "is_superuser"])


def load_driver_profiles_safe(company=None):
    try:
        from .services.driver_roster import load_profiles
        if getattr(company, "id", None):
            return load_profiles(company_output_dir(OUTPUT_DIR, company) / "driver_profiles.json")
        return load_profiles()
    except Exception:
        return {}


@login_required(login_url="login")
@user_passes_test(is_system_admin, login_url="home")
def company_management(request):
    default_company = get_default_company()
    if getattr(default_company, "id", None):
        ensure_tenant_output_dir(OUTPUT_DIR, default_company)
    selected_company_id = to_int(request.GET.get("company"), 0)
    company_rows, users, company_users, drivers, selected_company = company_management_rows(selected_company_id)
    selected_settings = get_company_schedule_settings(selected_company) if selected_company else None
    return render(
        request,
        "routing/company_management.html",
        {
            "company_rows": company_rows,
            "users": users,
            "company_users": company_users,
            "selected_company": selected_company,
            "selected_settings": selected_settings,
            "schedule_settings": serialize_schedule_settings(selected_settings) if selected_settings else {},
            "route_variant_choices": CompanyScheduleSettings.ROUTE_VARIANT_CHOICES,
            "industry_choices": CompanyProfile.INDUSTRY_CHOICES,
            "default_company": default_company,
            "message": request.GET.get("message", ""),
            "error": request.GET.get("error", ""),
        },
    )


def redirect_company_management(message="", error="", company_id=0):
    from urllib.parse import urlencode

    params = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    if company_id:
        params["company"] = company_id
    suffix = f"?{urlencode(params)}" if params else ""
    return redirect(f"/companies/{suffix}")


@csrf_exempt
@login_required(login_url="login")
@user_passes_test(is_system_admin, login_url="home")
@require_POST
def company_management_action(request):
    action = (request.POST.get("action") or "").strip()

    try:
        if action == "save_company":
            company_id = to_int(request.POST.get("company_id"), 0)
            key = normalize_company_key(request.POST.get("key"))
            name = (request.POST.get("name") or "").strip()
            industry_type = (request.POST.get("industry_type") or "generic_dispatch").strip()
            is_active = request.POST.get("is_active") == "on"

            if not key or not name:
                return redirect_company_management(error="請填寫公司代碼與公司名稱。")
            if industry_type not in dict(CompanyProfile.INDUSTRY_CHOICES):
                industry_type = "generic_dispatch"

            conflict = CompanyProfile.objects.filter(key=key)
            if company_id:
                conflict = conflict.exclude(id=company_id)
            if conflict.exists():
                return redirect_company_management(error="公司代碼已存在，請換一個代碼。")

            if company_id:
                company = get_object_or_404(CompanyProfile, pk=company_id)
                old_key = company.key
                company.key = key
                company.name = name
                company.industry_type = industry_type
                company.is_active = is_active
                company.save(update_fields=["key", "name", "industry_type", "is_active"])
                if old_key != key:
                    old_dir = OUTPUT_DIR / "tenants" / old_key
                    new_dir = company_output_dir(OUTPUT_DIR, company)
                    if old_dir.exists() and old_dir.is_dir():
                        shutil.copytree(old_dir, new_dir, dirs_exist_ok=True)
                message = "公司資料已更新。"
            else:
                company = CompanyProfile.objects.create(
                    key=key,
                    name=name,
                    industry_type=industry_type,
                    is_active=is_active,
                )
                message = "公司已建立。"

            ensure_tenant_output_dir(OUTPUT_DIR, company)
            get_company_schedule_settings(company)
            write_admin_log(request, "儲存公司設定", company.key, {"name": company.name})
            return redirect_company_management(message=message)

        if action == "create_generic_demo":
            company, created = CompanyProfile.objects.get_or_create(
                key="generic_demo",
                defaults={
                    "name": "Dispatch Nav 通用外勤展示公司",
                    "industry_type": "generic_dispatch",
                    "is_active": True,
                },
            )
            if not created and not company.is_active:
                company.is_active = True
                company.save(update_fields=["is_active"])
            tenant_output = ensure_tenant_output_dir(OUTPUT_DIR, company)
            get_company_schedule_settings(company)
            copy_scheduler_outputs_to_tenant(tenant_output)
            write_admin_log(request, "建立通用展示公司", company.key)
            return redirect_company_management(message="通用展示公司已準備完成。")

        if action == "save_schedule_settings":
            company_id = to_int(request.POST.get("company_id"), 0)
            return redirect_company_management(error="營運參數請由該公司的高階管理者自行調整。", company_id=company_id)

        if action == "assign_user":
            user_id = to_int(request.POST.get("user_id"), 0)
            company_id = to_int(request.POST.get("company_id"), 0)
            user = get_object_or_404(User, pk=user_id)
            company = get_object_or_404(CompanyProfile, pk=company_id, is_active=True)
            UserCompanyProfile.objects.update_or_create(
                user=user,
                defaults={"company": company},
            )
            write_admin_log(request, "綁定管理者公司", user.username, {"company": company.key})
            return redirect_company_management(message=f"{user.username} 已切換到 {company.name}。")

        if action == "create_company_user":
            company_id = to_int(request.POST.get("company_id"), 0)
            username = (request.POST.get("username") or "").strip()
            password = (request.POST.get("password") or "").strip()
            role = (request.POST.get("role") or "manager").strip()
            company = get_object_or_404(CompanyProfile, pk=company_id, is_active=True)

            if not username or not password:
                return redirect_company_management(error="請填寫管理者帳號與密碼。", company_id=company.id)
            if username == "system_admin":
                return redirect_company_management(error="system_admin 是平台帳號，不能加入公司。", company_id=company.id)
            internal_username = allocate_company_username(company, username)
            if not internal_username:
                return redirect_company_management(error="此公司已經有相同名稱的管理者帳號。", company_id=company.id)

            user = User.objects.create_user(username=internal_username, password=password)
            set_user_role(user, role)
            UserCompanyProfile.objects.update_or_create(user=user, defaults={"company": company})
            write_admin_log(request, "新增公司管理者", username, {"company": company.key, "role": role, "internal_username": internal_username})
            return redirect_company_management(message=f"{username} 已新增到 {company.name}。", company_id=company.id)

        if action == "update_company_user":
            user_id = to_int(request.POST.get("user_id"), 0)
            company_id = to_int(request.POST.get("company_id"), 0)
            target_company_id = to_int(request.POST.get("target_company_id"), 0) or company_id
            role = (request.POST.get("role") or "manager").strip()
            password = (request.POST.get("password") or "").strip()
            user = get_object_or_404(User, pk=user_id)
            company = get_object_or_404(CompanyProfile, pk=company_id)
            target_company = get_object_or_404(CompanyProfile, pk=target_company_id, is_active=True)

            if user.username == "system_admin":
                return redirect_company_management(error="不能編輯平台系統管理員。", company_id=company.id)

            set_user_role(user, role)
            if password:
                user.set_password(password)
                user.save(update_fields=["password"])
            UserCompanyProfile.objects.update_or_create(user=user, defaults={"company": target_company})
            write_admin_log(
                request,
                "更新公司管理者",
                user.username,
                {"from_company": company.key, "to_company": target_company.key, "role": role, "password_changed": bool(password)},
            )
            return redirect_company_management(message=f"{user.username} 已更新。", company_id=target_company.id)

        if action == "delete_company_user":
            user_id = to_int(request.POST.get("user_id"), 0)
            company_id = to_int(request.POST.get("company_id"), 0)
            user = get_object_or_404(User, pk=user_id)
            company = get_object_or_404(CompanyProfile, pk=company_id)

            if user.username == "system_admin":
                return redirect_company_management(error="不能刪除平台系統管理員。", company_id=company.id)
            if not UserCompanyProfile.objects.filter(user=user, company=company).exists():
                return redirect_company_management(error="此帳號不屬於目前選擇的公司。", company_id=company.id)

            username = user.username
            user.delete()
            write_admin_log(request, "刪除公司管理者", username, {"company": company.key})
            return redirect_company_management(message=f"{username} 已刪除。", company_id=company.id)

    except Exception as exc:
        return redirect_company_management(error=f"處理失敗：{exc}")

    return redirect_company_management(error="未知的操作。")


@login_required(login_url="login")
@user_passes_test(is_super_admin, login_url="home")
def admin_action_logs_page(request):
    if is_system_admin(request.user):
        logs = read_admin_logs()
    else:
        logs = read_admin_logs(company=get_user_company(request.user))
    return render(request, "routing/admin_action_logs.html", {"logs": logs})


@login_required(login_url="login")
@ensure_csrf_cookie
def home(request):
    if is_system_admin(request.user):
        return redirect("company_management")
    company = get_user_company(request.user)
    settings = get_company_schedule_settings(company)
    initial_variant = request.GET.get("variant") or getattr(settings, "default_route_variant", "normal") or "normal"
    if initial_variant not in VARIANT_LABELS:
        initial_variant = "normal"

    company_points = company_service_points_queryset(company)
    total_points = company_points.count()
    depots_count = (
        company_points.exclude(depot__isnull=True)
        .exclude(depot__exact="")
        .values("depot")
        .distinct()
        .count()
    )

    run_status = request.GET.get("run", "")
    if run_status == "success":
        run_message = "排程已重新執行完成，JSON 與 Excel 報表都已更新。"
    elif run_status == "failed":
        run_message = "排程執行失敗，請檢查 output/run_all_last.log。"
    else:
        run_message = ""

    return render(
        request,
        "routing/home.html",
        {
            "initial_variant": initial_variant,
            "total_points": total_points,
            "depots_count": depots_count,
            "run_status": run_status,
            "run_message": run_message,
            "company": serialize_company(company),
            "schedule_settings": serialize_schedule_settings(settings),
        },
    )


def run_scheduler(request):
    company = get_user_company(request.user)
    settings = get_company_schedule_settings(company)
    variant = request.GET.get("variant") or getattr(settings, "default_route_variant", "normal") or "normal"
    if variant not in VARIANT_LABELS:
        variant = "normal"
    tenant_output = ensure_tenant_output_dir(OUTPUT_DIR, company)
    settings_env = schedule_settings_env(settings)

    response_format = (request.GET.get("format") or "").strip().lower()
    wants_json = response_format == "json"

    if not is_manager(request.user):
        message = "權限不足：只有管理者可以重新計算最佳路徑。"
        if wants_json:
            return JsonResponse({"ok": False, "message": message}, status=403)
        return redirect("home")

    if wants_json:
        snapshot = _snapshot_run_state(variant)
        if snapshot.get("running"):
            return JsonResponse({
                "ok": True,
                "started": False,
                "running": True,
                "variant": snapshot.get("variant"),
                "message": "排程已在執行中，請等待目前這次完成。",
            })

        write_admin_log(request, "重新計算最佳路徑", variant, {"mode": "background", "company": company.key})
        thread = threading.Thread(target=_run_scheduler_background, args=(variant, tenant_output, company.key, settings_env), daemon=True)
        thread.start()
        return JsonResponse({
            "ok": True,
            "started": True,
            "running": True,
            "variant": variant,
            "label": VARIANT_LABELS.get(variant, variant),
            "message": "已開始背景重新計算，完成後會自動更新地圖。",
        })

    # 保留原本非 JSON 的按鈕/網址行為：同步執行後導回首頁。
    write_admin_log(request, "重新計算最佳路徑", variant, {"mode": "sync", "company": company.key})
    try:
        result = subprocess.run(
            [sys.executable, "run_all.py"],
            cwd=str(BASE_DIR),
            env={**os.environ, **settings_env, "DISPATCH_COMPANY_KEY": str(company.key or "")},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=3600,
        )
        log_path = OUTPUT_DIR / "run_all_last.log"
        inner_log_text = ""
        if log_path.exists():
            try:
                inner_log_text = log_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                inner_log_text = ""
        log_path.write_text(
            (
                f"Return code: {result.returncode}\n\n=== STDOUT ===\n{result.stdout or ''}\n\n=== STDERR ===\n{result.stderr or ''}"
                + (f"\n\n=== INNER RUN LOG ===\n{inner_log_text}" if result.returncode != 0 and inner_log_text else "")
            ),
            encoding="utf-8",
        )
        if result.returncode == 0:
            copy_scheduler_outputs_to_tenant(tenant_output)
            return redirect(f"/home/?variant={variant}&run=success")
        return redirect(f"/home/?variant={variant}&run=failed")
    except Exception as e:
        (OUTPUT_DIR / "run_all_last.log").write_text(
            f"Exception while running scheduler:\n{e}",
            encoding="utf-8",
        )
        return redirect(f"/home/?variant={variant}&run=failed")


def api_run_status(request):
    variant = request.GET.get("variant", "normal")
    if variant not in VARIANT_LABELS:
        variant = "normal"
    data = _snapshot_run_state(variant)
    return JsonResponse({
        "ok": True,
        "variant": data.get("variant") or variant,
        "running": bool(data.get("running")),
        "finished": bool(data.get("finished")),
        "success": bool(data.get("success")),
        "message": data.get("message") or "",
        "elapsed_sec": int(data.get("elapsed_sec") or 0),
        "progress": int(data.get("progress") or 0),
        "run_meta": data.get("run_meta"),
    })

def api_route_options(request):
    variant = request.GET.get("variant", "normal")
    output_dir = current_company_output_dir(OUTPUT_DIR, request.user)
    company = get_user_company(request.user)
    settings = get_company_schedule_settings(company)
    payload = load_variant_payload(variant, output_dir, settings=settings, company=company)

    if not payload["ok"]:
        return JsonResponse(payload, status=404)

    routes = payload["routes"]
    driver_index = {}
    for route in routes:
        code = clean_text(route.get("driver")) or ""
        if not code:
            continue
        label = clean_text(route.get("driver_label")) or driver_label(code)
        schedule_slot = clean_text(route.get("schedule_slot")) or code
        current = driver_index.get(code)
        if current is None or driver_sort_key(code, schedule_slot) < driver_sort_key(current.get("value"), current.get("schedule_slot")):
            driver_index[code] = {
                "value": code,
                "label": label,
                "schedule_slot": schedule_slot,
            }

    drivers = sorted(driver_index.values(), key=lambda item: driver_sort_key(item.get("value"), item.get("schedule_slot")))
    driver_codes = [item["value"] for item in drivers]

    days_by_driver = {}
    for code in driver_codes:
        days = sorted({to_int(r["day"]) for r in routes if r["driver"] == code})
        days_by_driver[code] = days

    return JsonResponse(
        {
            "ok": True,
            "variant": payload["variant"],
            "label": payload["label"],
            "warning": payload["warning"],
            "file_used": payload["file_used"],
            "meta": normalize_route_meta(payload["meta"], company),
            "esg": payload.get("esg") or build_esg_summary(routes, payload["variant"], output_dir, settings=settings),
            "company": serialize_company(company),
            "schedule_settings": serialize_schedule_settings(settings),
            "route_count": len(routes),
            "drivers": drivers,
            "days_by_driver": days_by_driver,
        }
    )


def api_route_detail(request):
    variant = request.GET.get("variant", "normal")
    company = get_user_company(request.user)
    settings = get_company_schedule_settings(company)
    payload = load_variant_payload(variant, current_company_output_dir(OUTPUT_DIR, request.user), settings=settings, company=company)

    if not payload["ok"]:
        return JsonResponse(payload, status=404)

    routes = payload["routes"]
    if not routes:
        return JsonResponse(
            {
                "ok": False,
                "warning": f"{payload['label']} 目前沒有可顯示的路線資料。",
            },
            status=404,
        )

    profiles = load_driver_profiles_safe(company)
    requested_driver_param = clean_text(request.GET.get("driver"))
    requested_driver = requested_driver_param or routes[0]["driver"]
    driver = requested_driver
    assignment = get_driver_assignment(driver, profiles=profiles)
    assigned_slot = normalize_schedule_slot((assignment or {}).get("schedule_slot"))
    direct_routes = [r for r in routes if r["driver"] == driver]
    direct_matches_slot = any(
        normalize_schedule_slot(r.get("schedule_slot")) == assigned_slot for r in direct_routes
    )
    use_direct_routes = (
        not assigned_slot
        or assigned_slot == driver
        or direct_matches_slot
    )
    candidate_routes = direct_routes if use_direct_routes else []
    if not candidate_routes:
        if assigned_slot:
            candidate_routes = [r for r in routes if r["driver"] == assigned_slot]
            if candidate_routes:
                driver = assigned_slot
    if not candidate_routes and direct_routes:
        candidate_routes = direct_routes
    if not candidate_routes:
        if requested_driver_param:
            return JsonResponse(
                {
                    "ok": False,
                    "variant": payload["variant"],
                    "label": payload["label"],
                    "message": f"找不到 {requested_driver} 的排程路線，請確認已指定固定排程席位。",
                    "requested_driver": requested_driver,
                },
                status=404,
            )
        candidate_routes = [r for r in routes if r["driver"] == routes[0]["driver"]]
        driver = candidate_routes[0]["driver"]

    available_days = sorted({to_int(r["day"]) for r in candidate_routes})
    requested_day = to_int(request.GET.get("day"), available_days[0])
    day = requested_day if requested_day in available_days else available_days[0]

    route = next((r for r in candidate_routes if to_int(r["day"]) == day), None)
    if route is None:
        route = candidate_routes[0]

    return JsonResponse(
        {
            "ok": True,
            "variant": payload["variant"],
            "label": payload["label"],
            "warning": payload["warning"],
            "file_used": payload["file_used"],
            "meta": normalize_route_meta(payload["meta"], company),
            "requested_driver": requested_driver,
            "schedule_slot": driver if driver != requested_driver else "",
            "route": route,
        }
    )


def api_old_route_options(request):
    payload = load_old_payload(current_company_output_dir(OUTPUT_DIR, request.user))

    if not payload["ok"]:
        return JsonResponse(payload, status=404)

    routes = payload["routes"]

    # 同一位司機固定同一個顯示代號，不再因為第幾天而跳號
    first_code_by_driver = {}

    for route in routes:
        raw_code = clean_text(route.get("driver")) or ""
        prefix = raw_code[:1].upper() if raw_code else "X"
        original_name = (
            clean_text(route.get("original_driver_name"))
            or clean_text(route.get("driver_label"))
            or raw_code
        )
        key = (prefix, original_name)

        saved_code = first_code_by_driver.get(key)
        if saved_code is None or driver_sort_key(raw_code) < driver_sort_key(saved_code):
            first_code_by_driver[key] = raw_code

    display_label_by_driver = {}

    # 先排平鎮 P，再排五股 W，和新路線的顯示習慣一致
    for prefix in ["P", "W"]:
        keys = [k for k in first_code_by_driver.keys() if k[0] == prefix]
        keys.sort(key=lambda k: driver_sort_key(first_code_by_driver[k]))

        for idx, key in enumerate(keys, start=1):
            code = f"{prefix}{idx:02d}"
            if prefix == "P":
                base_label = f"{code}｜平鎮{idx}"
            elif prefix == "W":
                base_label = f"{code}｜五股{idx}"
            else:
                base_label = code
            display_label_by_driver[key] = base_label

    # 其他未知前綴保底
    other_keys = [k for k in first_code_by_driver.keys() if k[0] not in ["P", "W"]]
    other_keys.sort(key=lambda k: driver_sort_key(first_code_by_driver[k]))
    for idx, key in enumerate(other_keys, start=1):
        prefix = key[0] or "X"
        display_label_by_driver[key] = f"{prefix}{idx:02d}"

    options = []
    for route in routes:
        raw_code = clean_text(route.get("driver")) or ""
        prefix = raw_code[:1].upper() if raw_code else "X"
        original_name = (
            clean_text(route.get("original_driver_name"))
            or clean_text(route.get("driver_label"))
            or raw_code
        )
        key = (prefix, original_name)

        display_driver_label = display_label_by_driver.get(
            key,
            route.get("driver_label") or raw_code,
        )

        options.append(
            {
                "route_id": route.get("route_id"),
                "label": f"{display_driver_label}｜第{route.get('day')}天｜{route.get('stop_count')}站",
                "_sort_code": display_driver_label.split("｜")[0],
                "_sort_day": to_int(route.get("day")),
            }
        )

    # 下拉順序：P01 第1天、第2天... → P02 ... → W01 ...
    options.sort(key=lambda x: (driver_sort_key(x["_sort_code"]), x["_sort_day"]))

    for item in options:
        item.pop("_sort_code", None)
        item.pop("_sort_day", None)

    return JsonResponse(
        {
            "ok": True,
            "warning": payload["warning"],
            "file_used": payload["file_used"],
            "meta": payload["meta"],
            "routes": options,
        }
    )


def api_old_route_detail(request):
    payload = load_old_payload(current_company_output_dir(OUTPUT_DIR, request.user))

    if not payload["ok"]:
        return JsonResponse(payload, status=404)

    route_id = clean_text(request.GET.get("route_id"))
    routes = payload["routes"]

    route = None
    if route_id:
        route = next((r for r in routes if clean_text(r.get("route_id")) == route_id), None)

    if route is None and routes:
        route = routes[0]

    if route is None:
        return JsonResponse(
            {
                "ok": False,
                "warning": "目前沒有舊路線資料。",
            },
            status=404,
        )

    return JsonResponse(
        {
            "ok": True,
            "warning": payload["warning"],
            "file_used": payload["file_used"],
            "meta": payload["meta"],
            "route": route,
        }
    )


def _excel_column_lookup(columns):
    return {str(col).strip().lower(): col for col in columns}


def _find_excel_column(columns, aliases):
    lookup = _excel_column_lookup(columns)
    for alias in aliases:
        key = str(alias).strip().lower()
        if key in lookup:
            return lookup[key]
    return None


def _county_from_address(address):
    text = clean_text(address) or ""
    for marker in ("縣", "市"):
        idx = text.find(marker)
        if 0 < idx <= 3:
            return text[:idx + 1]
    return ""


def _routes_from_baseline_excel(upload_file):
    sheets = pd.read_excel(upload_file, sheet_name=None)
    frames = []
    for sheet_name, frame in sheets.items():
        if frame is None or frame.empty:
            continue
        frame = frame.copy()
        frame["_sheet_name"] = sheet_name
        frames.append(frame)

    if not frames:
        raise ValueError("Excel 內沒有可讀取的資料列。")

    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(how="all")
    columns = list(df.columns)

    day_col = _find_excel_column(columns, ["日程(Day)", "日程", "Day", "天數", "第幾天"])
    driver_col = _find_excel_column(columns, ["員工代號", "司機代碼", "司機", "driver", "driver_code"])
    area_col = _find_excel_column(columns, ["工作區域", "區域", "area"])
    seq_col = _find_excel_column(columns, ["順序", "站序", "序號", "seq"])
    task_col = _find_excel_column(columns, ["任務ID", "任務 ID", "點位ID", "點位 ID", "task_id", "id"])
    address_col = _find_excel_column(columns, ["地址", "站點地址", "stop_address", "address"])
    lat_col = _find_excel_column(columns, ["緯度", "lat", "latitude"])
    lon_col = _find_excel_column(columns, ["經度", "lon", "lng", "longitude"])
    service_col = _find_excel_column(columns, ["維護時間(分)", "維護時間", "服務時間", "服務時間(分)", "service_min"])
    drive_col = _find_excel_column(columns, ["預估車程(分)", "預估車程", "車程", "車程(分)", "drive_min"])
    total_col = _find_excel_column(columns, ["累計工時(分)", "累計工時", "總工時", "total_min"])

    missing = []
    for label, col in [
        ("日程(Day)", day_col),
        ("員工代號", driver_col),
        ("順序", seq_col),
        ("地址", address_col),
        ("緯度", lat_col),
        ("經度", lon_col),
    ]:
        if col is None:
            missing.append(label)
    if missing:
        raise ValueError(f"Excel 缺少必要欄位：{', '.join(missing)}")

    grouped = {}
    for _, row in df.iterrows():
        day = to_int(row.get(day_col), None)
        driver = clean_text(row.get(driver_col))
        seq = to_int(row.get(seq_col), None)
        lat = to_float(row.get(lat_col))
        lon = to_float(row.get(lon_col))
        address = clean_text(row.get(address_col))

        if day is None or not driver or seq is None or lat is None or lon is None:
            continue

        key = (driver, day)
        grouped.setdefault(key, {
            "driver": driver,
            "day": day,
            "area": clean_text(row.get(area_col)) if area_col is not None else "",
            "stops": [],
        })

        service_min = to_float(row.get(service_col)) if service_col is not None else 0.0
        drive_min = to_float(row.get(drive_col)) if drive_col is not None else 0.0
        total_min = to_float(row.get(total_col)) if total_col is not None else None
        county = _county_from_address(address)

        grouped[key]["stops"].append({
            "seq": seq,
            "task_id": clean_text(row.get(task_col)) if task_col is not None else "",
            "address": address or "-",
            "lat": lat,
            "lon": lon,
            "service_min": service_min or 0.0,
            "drive_min": drive_min or 0.0,
            "total_min": total_min,
            "county": county,
        })

    routes = []
    for (driver, day), item in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        stops = sorted(item["stops"], key=lambda stop: to_int(stop.get("seq"), 0))
        if not stops:
            continue
        counties = sorted({stop.get("county") for stop in stops if stop.get("county")})
        service_min = sum(to_float(stop.get("service_min")) or 0.0 for stop in stops)
        drive_min = sum(to_float(stop.get("drive_min")) or 0.0 for stop in stops)
        total_values = [to_float(stop.get("total_min")) for stop in stops if to_float(stop.get("total_min")) is not None]
        total_min = max(total_values) if total_values else service_min + drive_min

        routes.append({
            "route_id": f"OLD-{driver}-D{day}",
            "driver": driver,
            "driver_label": f"{driver}" + (f"｜{item['area']}" if item.get("area") else ""),
            "original_driver_name": driver,
            "day": day,
            "depot": {},
            "stop_count": len(stops),
            "counties": counties,
            "cross_county": len(counties) > 1,
            "metrics": {
                "service_min": round(service_min, 2),
                "drive_min": round(drive_min, 2),
                "dist_km": 0,
                "total_min": round(total_min, 2),
                "overtime_min": 0,
            },
            "stops": stops,
        })

    if not routes:
        raise ValueError("Excel 沒有可轉換的路線資料，請確認日程、員工、順序、地址、緯度與經度都有值。")

    return {
        "meta": {
            "source": "uploaded_excel",
            "source_filename": upload_file.name,
            "sheet_count": len(sheets),
            "row_count": int(len(df)),
        },
        "routes": routes,
    }


@require_POST
def api_upload_esg_baseline(request):
    upload_file = request.FILES.get("file")
    if not upload_file:
        return JsonResponse({"ok": False, "message": "請先選擇舊路線 Excel 或 JSON 檔。"}, status=400)

    file_name = str(upload_file.name or "").lower()

    try:
        if file_name.endswith(".json"):
            raw_bytes = b"".join(upload_file.chunks())
            raw_text = raw_bytes.decode("utf-8-sig")
            data = json.loads(raw_text)
        elif file_name.endswith(".xlsx") or file_name.endswith(".xls"):
            data = _routes_from_baseline_excel(upload_file)
        else:
            return JsonResponse({"ok": False, "message": "請上傳 Excel（.xlsx/.xls）或 JSON 檔。"}, status=400)
    except UnicodeDecodeError:
        return JsonResponse({"ok": False, "message": "檔案編碼無法讀取，請使用 UTF-8 JSON。"}, status=400)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "message": "JSON 格式不正確，請確認檔案內容。"}, status=400)
    except ValueError as e:
        return JsonResponse({"ok": False, "message": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"ok": False, "message": f"檔案讀取失敗：{str(e)}"}, status=400)

    routes = data.get("routes") if isinstance(data, dict) else None
    if not isinstance(routes, list) or not routes:
        return JsonResponse({"ok": False, "message": "檔案內找不到 routes 陣列，無法作為舊路線基準。"}, status=400)

    valid_route_count = sum(1 for route in routes if isinstance(route, dict) and route.get("stops"))
    if valid_route_count <= 0:
        return JsonResponse({"ok": False, "message": "routes 中沒有可計算的站點資料。"}, status=400)

    variant = request.POST.get("variant", "normal")
    if variant not in VARIANT_LABELS:
        variant = "normal"
    company = get_user_company(request.user)
    tenant_output = ensure_tenant_output_dir(OUTPUT_DIR, company)

    try:
        baseline_path = tenant_output / ESG_BASELINE_FILE
        if baseline_path.exists():
            backup_name = f"{ESG_BASELINE_FILE}.{timezone.localtime(timezone.now()).strftime('%Y%m%d_%H%M%S')}.bak"
            (tenant_output / backup_name).write_bytes(baseline_path.read_bytes())

        baseline_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        current_payload = load_variant_payload(variant, tenant_output, company=company)
        esg = current_payload.get("esg") if current_payload.get("ok") else build_esg_summary([], variant, tenant_output)

        write_admin_log(
            request,
            "上傳 ESG 舊路線基準",
            upload_file.name,
            {"route_count": len(routes), "valid_route_count": valid_route_count, "company": company.key},
        )

        return JsonResponse({
            "ok": True,
            "message": f"已更新舊路線基準，共讀取 {len(routes)} 條路線。",
            "route_count": len(routes),
            "valid_route_count": valid_route_count,
            "esg": esg,
        })
    except Exception as e:
        return JsonResponse({"ok": False, "message": f"儲存舊路線基準失敗：{str(e)}"}, status=500)


def _norm_for_search(value):
    return str(value or "").strip().lower()


def _rough_same_coord(a, b):
    try:
        return abs(float(a) - float(b)) < 0.00008
    except Exception:
        return False


def api_search_point_route(request):
    variant = request.GET.get("variant", "normal")
    q = clean_text(request.GET.get("q")) or ""
    if not q:
        return JsonResponse({"ok": False, "message": "請輸入點位 ID、名稱或地址。"}, status=400)

    company = get_user_company(request.user)
    payload = load_variant_payload(variant, current_company_output_dir(OUTPUT_DIR, request.user), company=company)
    if not payload.get("ok"):
        return JsonResponse(payload, status=404)

    q_norm = _norm_for_search(q)
    db_point = None
    db_candidates = []
    company_points = current_company_service_points_queryset(request)

    if q.isdigit():
        try:
            db_point = company_points.filter(id=int(q)).first()
        except Exception:
            db_point = None

    if db_point is None:
        db_candidates = list(company_points.filter(
            Q(client_name__icontains=q) | Q(address__icontains=q) | Q(order_id__icontains=q)
        )[:5])
    else:
        db_candidates = [db_point]

    results = []
    seen = set()

    def add_result(route, stop, matched_by="route"):
        key = (route.get("driver"), route.get("day"), stop.get("seq"), stop.get("task_id"), stop.get("node_id"))
        if key in seen:
            return
        seen.add(key)
        results.append({
            "driver": route.get("driver"),
            "driver_label": route.get("driver_label") or driver_label(route.get("driver")),
            "day": to_int(route.get("day"), 0),
            "seq": to_int(stop.get("seq"), 0),
            "task_id": stop.get("task_id"),
            "node_id": stop.get("node_id"),
            "address": stop.get("address"),
            "county": stop.get("county"),
            "lat": stop.get("lat"),
            "lon": stop.get("lon"),
            "service_min": stop.get("service_min"),
            "matched_by": matched_by,
        })

    # 1) 直接查路線檔中的 task/node/address
    for route in payload.get("routes", []):
        for stop in route.get("stops", []) or []:
            fields = [
                stop.get("task_id"), stop.get("node_id"), stop.get("id"), stop.get("db_id"),
                stop.get("original_id"), stop.get("order_id"), stop.get("address"), stop.get("county"),
            ]
            hay = " ".join(_norm_for_search(x) for x in fields if x is not None)
            if q_norm and q_norm in hay:
                add_result(route, stop, "route_text")

    # 2) 如果輸入的是資料庫 ID / 名稱 / 地址，用 DB 內容對照地址或座標
    for point in db_candidates:
        p_address = _norm_for_search(getattr(point, "address", ""))
        p_name = _norm_for_search(getattr(point, "client_name", ""))
        p_order = _norm_for_search(getattr(point, "order_id", ""))
        p_lat = getattr(point, "lat", None)
        p_lon = getattr(point, "lon", None)

        for route in payload.get("routes", []):
            for stop in route.get("stops", []) or []:
                s_address = _norm_for_search(stop.get("address"))
                same_addr = bool(p_address and s_address and (p_address == s_address or p_address in s_address or s_address in p_address))
                same_coord = _rough_same_coord(p_lat, stop.get("lat")) and _rough_same_coord(p_lon, stop.get("lon"))
                text_hit = (p_name and p_name in _norm_for_search(stop.get("node_id"))) or (p_order and p_order in _norm_for_search(stop.get("task_id")))
                if same_addr or same_coord or text_hit:
                    add_result(route, stop, "database_match")

    if results:
        results.sort(key=lambda r: (str(r.get("driver") or ""), int(r.get("day") or 0), int(r.get("seq") or 0)))
        return JsonResponse({
            "ok": True,
            "query": q,
            "variant": payload.get("variant"),
            "label": payload.get("label"),
            "count": len(results),
            "results": results[:50],
        })

    if db_candidates:
        p = db_candidates[0]
        return JsonResponse({
            "ok": True,
            "query": q,
            "variant": payload.get("variant"),
            "label": payload.get("label"),
            "count": 0,
            "results": [],
            "message": f"資料庫有此點位，但目前 {payload.get('file_used') or '路線檔'} 找不到它的排程位置。請確認已重新計算，且排程演算法有排入此點。",
            "database_point": {
                "id": getattr(p, "id", None),
                "client_name": getattr(p, "client_name", ""),
                "address": getattr(p, "address", ""),
                "lat": getattr(p, "lat", None),
                "lon": getattr(p, "lon", None),
            }
        })

    return JsonResponse({
        "ok": True,
        "query": q,
        "variant": payload.get("variant"),
        "label": payload.get("label"),
        "count": 0,
        "results": [],
        "message": "找不到符合的點位。可以輸入資料庫 ID、點位名稱、地址、task_id 或 node_id。",
    })


def api_points_page(request):
    q = request.GET.get("q", "").strip()
    selected_depot = request.GET.get("depot", "").strip()
    page = to_int(request.GET.get("page"), 1)
    page_size = to_int(request.GET.get("page_size"), 20)

    allowed_page_sizes = [20, 50, 100, 200]
    if page_size not in allowed_page_sizes:
        page_size = 20

    rows = current_company_service_points_queryset(request).order_by("id")

    if q:
        rows = rows.filter(
            Q(client_name__icontains=q)
            | Q(address__icontains=q)
            | Q(order_id__icontains=q)
        )

    if selected_depot:
        rows = rows.filter(depot=selected_depot)

    paginator = Paginator(rows, page_size)
    page_obj = paginator.get_page(page)

    items = []
    for row in page_obj:
        items.append(
            {
                "id": row.id,
                "depot": row.depot,
                "client_name": row.client_name,
                "service_time": row.service_time,
                "address": row.address,
                "lat": row.lat,
                "lon": row.lon,
                "weekly_1": row.weekly_1,
                "weekly_2": row.weekly_2,
            }
        )

    return JsonResponse(
        {
            "ok": True,
            "page": page_obj.number,
            "page_size": page_size,
            "total_pages": paginator.num_pages,
            "total_count": paginator.count,
            "items": items,
        }
    )


@login_required(login_url="login")
def data_list(request):
    q = request.GET.get("q", "").strip()
    selected_depot = request.GET.get("depot", "").strip()
    page_size_raw = request.GET.get("page_size", "20").strip()
    deleted_count = to_int(request.GET.get("deleted_count"), 0)

    allowed_page_sizes = [20, 50, 100, 200]
    try:
        page_size = int(page_size_raw)
    except ValueError:
        page_size = 20

    if page_size not in allowed_page_sizes:
        page_size = 20

    rows = current_company_service_points_queryset(request).order_by("id")

    if q:
        rows = rows.filter(
            Q(client_name__icontains=q)
            | Q(address__icontains=q)
            | Q(order_id__icontains=q)
        )

    if selected_depot:
        rows = rows.filter(depot=selected_depot)

    depots = (
        current_company_service_points_queryset(request).exclude(depot__isnull=True)
        .exclude(depot__exact="")
        .values_list("depot", flat=True)
        .distinct()
        .order_by("depot")
    )

    total_count = rows.count()

    paginator = Paginator(rows, page_size)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "routing/data_list.html",
        {
            "page_obj": page_obj,
            "q": q,
            "depots": depots,
            "selected_depot": selected_depot,
            "page_size": page_size,
            "page_size_options": allowed_page_sizes,
            "total_count": total_count,
            "deleted_count": deleted_count,
        },
    )


@login_required(login_url="login")
@user_passes_test(is_manager, login_url="data_list")
def data_add(request):
    company = get_user_company(request.user)
    if request.method == "POST":
        obj = ServicePoint.objects.create(
            created_at=timezone.now(),
            depot=request.POST.get("depot") or None,
            client_name=request.POST.get("client_name") or None,
            service_time=to_float(request.POST.get("service_time")),
            address=request.POST.get("address") or None,
            floor=to_float(request.POST.get("floor")),
            order_id=request.POST.get("order_id") or None,
            weekly_1=True if request.POST.get("weekly_1") == "on" else False,
            weekly_2=True if request.POST.get("weekly_2") == "on" else False,
            lat=to_float(request.POST.get("lat")),
            lon=to_float(request.POST.get("lon")),
        )
        bind_service_point_to_company(obj, company)
        write_admin_log(request, "新增點位", getattr(obj, "id", ""), {"address": getattr(obj, "address", "")})
        return redirect("data_list")

    return render(request, "routing/data_form.html", {"mode": "add", "row": None})


@login_required(login_url="login")
@user_passes_test(is_manager, login_url="data_list")
def data_edit(request, pk):
    company = get_user_company(request.user)
    row = get_object_or_404(company_service_points_queryset(company), pk=pk)

    if request.method == "POST":
        row.depot = request.POST.get("depot") or None
        row.client_name = request.POST.get("client_name") or None
        row.service_time = to_float(request.POST.get("service_time"))
        row.address = request.POST.get("address") or None
        row.floor = to_float(request.POST.get("floor"))
        row.order_id = request.POST.get("order_id") or None
        row.weekly_1 = True if request.POST.get("weekly_1") == "on" else False
        row.weekly_2 = True if request.POST.get("weekly_2") == "on" else False
        row.lat = to_float(request.POST.get("lat"))
        row.lon = to_float(request.POST.get("lon"))
        row.save()
        write_admin_log(request, "修改點位", row.id, {"address": row.address or ""})
        return redirect("data_list")

    return render(request, "routing/data_form.html", {"mode": "edit", "row": row})


@login_required(login_url="login")
@user_passes_test(is_manager, login_url="data_list")
def data_delete(request, pk):
    company = get_user_company(request.user)
    row = get_object_or_404(company_service_points_queryset(company), pk=pk)

    if request.method == "POST":
        target_info = {"id": row.id, "address": row.address or "", "client_name": row.client_name or ""}
        ServicePointCompanyProfile.objects.filter(service_point_id=row.id).delete()
        row.delete()
        write_admin_log(request, "刪除點位", target_info.get("id"), target_info)
        return redirect("data_list")

    return render(request, "routing/data_delete.html", {"row": row})


@login_required(login_url="login")
@user_passes_test(is_manager, login_url="data_list")
@require_POST
def data_bulk_delete(request):
    company = get_user_company(request.user)
    raw_ids = request.POST.getlist("ids")
    ids = []
    for value in raw_ids:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            pass

    if not ids:
        return redirect("data_list")

    rows = list(company_service_points_queryset(company).filter(id__in=ids))
    allowed_ids = [row.id for row in rows]
    if not allowed_ids:
        return redirect("data_list")

    target_info = [
        {"id": row.id, "address": row.address or "", "client_name": row.client_name or ""}
        for row in rows
    ]
    ServicePointCompanyProfile.objects.filter(service_point_id__in=allowed_ids, company=company).delete()
    deleted_count, _ = ServicePoint.objects.filter(id__in=allowed_ids).delete()
    write_admin_log(
        request,
        "批量刪除點位",
        ",".join(str(point_id) for point_id in allowed_ids),
        {"deleted_count": deleted_count, "points": target_info},
    )
    return redirect(f"/data/?deleted_count={deleted_count}")


@login_required(login_url="login")
@user_passes_test(is_manager, login_url="data_list")
def data_import(request):
    error_message = ""
    summary = None
    company = get_user_company(request.user)

    if request.method == "POST":
        upload_file = request.FILES.get("file")

        if not upload_file:
            error_message = "請先選擇要匯入的檔案。"
        else:
            file_name = upload_file.name.lower()

            try:
                if file_name.endswith(".csv"):
                    df = pd.read_csv(upload_file)
                elif file_name.endswith(".xlsx") or file_name.endswith(".xls"):
                    df = pd.read_excel(upload_file)
                else:
                    df = None
                    error_message = "只支援 CSV、XLSX、XLS 檔案。"
            except Exception as e:
                df = None
                error_message = f"檔案讀取失敗：{e}"

            if df is not None:
                df.columns = [str(col).strip() for col in df.columns]

                rename_map = {
                    "ID": "id",
                    "id": "id",
                    "場站": "depot",
                    "depot": "depot",
                    "客戶名稱": "client_name",
                    "client_name": "client_name",
                    "服務時間": "service_time",
                    "service_time": "service_time",
                    "地址": "address",
                    "address": "address",
                    "樓層": "floor",
                    "floor": "floor",
                    "排序": "order_id",
                    "order_id": "order_id",
                    "週1": "weekly_1",
                    "weekly_1": "weekly_1",
                    "週2": "weekly_2",
                    "weekly_2": "weekly_2",
                    "緯度": "lat",
                    "lat": "lat",
                    "經度": "lon",
                    "lon": "lon",
                }

                df = df.rename(columns={col: rename_map.get(col, col) for col in df.columns})

                created_count = 0
                updated_count = 0
                skipped_count = 0

                for _, row in df.iterrows():
                    record = row.to_dict()

                    data = {
                        "depot": clean_text(record.get("depot")),
                        "client_name": clean_text(record.get("client_name")),
                        "service_time": to_float(record.get("service_time")),
                        "address": clean_text(record.get("address")),
                        "floor": to_float(record.get("floor")),
                        "order_id": clean_text(record.get("order_id")),
                        "weekly_1": to_bool(record.get("weekly_1")),
                        "weekly_2": to_bool(record.get("weekly_2")),
                        "lat": to_float(record.get("lat")),
                        "lon": to_float(record.get("lon")),
                    }

                    is_empty_row = not any(
                        [
                            data["depot"],
                            data["client_name"],
                            data["service_time"] is not None,
                            data["address"],
                            data["floor"] is not None,
                            data["order_id"],
                            data["weekly_1"],
                            data["weekly_2"],
                            data["lat"] is not None,
                            data["lon"] is not None,
                        ]
                    )

                    if is_empty_row:
                        skipped_count += 1
                        continue

                    raw_id = record.get("id")
                    has_id = (
                        raw_id is not None
                        and str(raw_id).strip() != ""
                        and str(raw_id).strip().lower() != "nan"
                    )

                    try:
                        if has_id:
                            row_id = int(float(raw_id))
                            obj = ServicePoint.objects.filter(pk=row_id).first()

                            if obj:
                                if not service_point_belongs_to_company(row_id, company):
                                    skipped_count += 1
                                    continue
                                obj.depot = data["depot"]
                                obj.client_name = data["client_name"]
                                obj.service_time = data["service_time"]
                                obj.address = data["address"]
                                obj.floor = data["floor"]
                                obj.order_id = data["order_id"]
                                obj.weekly_1 = data["weekly_1"]
                                obj.weekly_2 = data["weekly_2"]
                                obj.lat = data["lat"]
                                obj.lon = data["lon"]
                                obj.save()
                                updated_count += 1
                            else:
                                obj = ServicePoint.objects.create(
                                    id=row_id,
                                    created_at=timezone.now(),
                                    **data,
                                )
                                bind_service_point_to_company(obj, company)
                                created_count += 1
                        else:
                            obj = ServicePoint.objects.create(
                                created_at=timezone.now(),
                                **data,
                            )
                            bind_service_point_to_company(obj, company)
                            created_count += 1
                    except Exception:
                        skipped_count += 1

                write_admin_log(request, "匯入點位 Excel/CSV", upload_file.name, {
                    "created_count": created_count,
                    "updated_count": updated_count,
                    "skipped_count": skipped_count,
                })

                summary = {
                    "created_count": created_count,
                    "updated_count": updated_count,
                    "skipped_count": skipped_count,
                    "columns": list(df.columns),
                }

    return render(
        request,
        "routing/data_import.html",
        {"error_message": error_message, "summary": summary},
    )


@login_required(login_url="login")
def cleaning_records_page(request):
    return render(request, "routing/cleaning_records.html")

@login_required(login_url="login")
def cleaning_report_page(request):
    return render(request, "routing/cleaning_report.html")
