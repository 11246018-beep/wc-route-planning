from pathlib import Path
import json
import subprocess
import sys
import threading
import time

from django.core.paginator import Paginator
from django.db.models import Q
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST

import pandas as pd

from .models import ServicePoint, Driver
from .services.driver_roster import get_driver_assignment, normalize_schedule_slot, schedule_sort_key

from collections import defaultdict
from supabase import create_client

SUPABASE_URL = "https://evwzonunmjvulzitxjmn.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV2d3pvbnVubWp2dWx6aXR4am1uIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI3OTAzOTUsImV4cCI6MjA4ODM2NjM5NX0.lWMaSu_B6q4AhzAxFykA6YBkwMN0QqNptAoUaraM2E4"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def toilet_demand_analysis_api(request):
    try:
        response = (
            supabase.table("uploaded_photos")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )

        records = response.data or []

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
                cursor.execute(
                    "DELETE FROM uploaded_photos WHERE id::text = ANY(%s) RETURNING id",
                    [ids],
                )
            else:
                cursor.execute(
                    """
                    DELETE FROM uploaded_photos
                    WHERE (stop_address = %s OR point_key = %s)
                      AND (photo_type = 'before' OR photo_type = '前')
                      AND (
                        is_qualified = false
                        OR review_status = '不合格'
                        OR is_risk = true
                      )
                    RETURNING id
                    """,
                    [address, address],
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


def is_manager(user):
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def role_label(user):
    if not getattr(user, "is_active", False):
        return "待審核 / 已停用"
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
        row = {
            "time": timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M:%S"),
            "username": user.username if user and user.is_authenticated else "anonymous",
            "role": role_label(user) if user and user.is_authenticated else "未登入",
            "action": action,
            "target": str(target or ""),
            "ip": get_client_ip(request),
            "extra": extra or {},
        }
        with ACTION_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_admin_logs(limit=300):
    if not ACTION_LOG_PATH.exists():
        return []
    try:
        lines = ACTION_LOG_PATH.read_text(encoding="utf-8").splitlines()
        logs = []
        for line in lines[-limit:]:
            try:
                logs.append(json.loads(line))
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


def _run_scheduler_background(variant):
    _set_run_state(
        running=True,
        finished=False,
        success=False,
        variant=variant,
        message="正在執行 run_all.py，請稍候。",
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
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=3600,
        )

        log_path = OUTPUT_DIR / "run_all_last.log"
        log_text = []
        log_text.append(f"Return code: {result.returncode}\n")
        log_text.append("\n=== STDOUT ===\n")
        log_text.append(result.stdout or "")
        log_text.append("\n\n=== STDERR ===\n")
        log_text.append(result.stderr or "")
        log_path.write_text("".join(log_text), encoding="utf-8")

        if result.returncode == 0:
            run_meta = extract_variant_run_meta(variant)
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
def driver_login_api(request):
    if request.method == "OPTIONS":
        response = JsonResponse({"ok": True})
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return response

    if request.method != "POST":
        return JsonResponse(
            {"success": False, "message": "只允許 POST"},
            status=405
        )

    try:
        data = json.loads(request.body)
        driver_code = data.get("driver_code", "").strip()
        password = data.get("password", "").strip()

        if not driver_code or not password:
            return JsonResponse(
                {"success": False, "message": "請輸入司機編號與密碼"},
                status=400
            )

        try:
            driver = Driver.objects.get(driver_code=driver_code)
        except Driver.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": "找不到此司機帳號"},
                status=401
            )

        if driver.password != password:
            return JsonResponse(
                {"success": False, "message": "密碼錯誤"},
                status=401
            )

        return JsonResponse({
            "success": True,
            "driver_code": driver.driver_code,
            "name": driver.driver_code,
            "depot_id": driver.depot_id,
            "max_minutes": driver.max_minutes,
        })

    except Exception as e:
        return JsonResponse(
            {"success": False, "message": f"伺服器錯誤：{str(e)}"},
            status=500
        )

def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_current_service_point_count():
    """回傳目前資料庫 service_points 的即時筆數，避免畫面沿用舊 JSON meta 的歷史最大值。"""
    try:
        return int(ServicePoint.objects.count())
    except Exception:
        return 0


def normalize_route_meta(meta):
    """把路線 JSON 的 meta 與目前資料庫點位數同步。

    排程路線本身仍讀 JSON，不動演算法；這裡只修正首頁顯示的
    total_db_points / scheduled_db_points，避免刪除點位後仍顯示曾經的最大筆數。
    """
    data = dict(meta or {})
    current_total = get_current_service_point_count()
    if current_total <= 0:
        return data

    unassigned = to_int(data.get("unassigned_db_points"), 0)
    if unassigned < 0 or unassigned > current_total:
        unassigned = 0

    data["total_db_points"] = current_total
    data["unassigned_db_points"] = unassigned
    data["scheduled_db_points"] = max(current_total - unassigned, 0)
    return data


def load_variant_payload(variant):
    if variant not in VARIANT_LABELS:
        variant = "normal"

    file_path = OUTPUT_DIR / VARIANT_FILES[variant]
    if not file_path.exists():
        return {
            "ok": False,
            "warning": f"找不到 {VARIANT_FILES[variant]}，請先按「重新計算最佳路徑」。",
            "routes": [],
            "variant": variant,
            "label": VARIANT_LABELS[variant],
            "file_used": None,
            "meta": {},
        }

    raw = load_json(file_path)
    routes = raw.get("routes", []) if isinstance(raw, dict) else []
    meta = raw.get("meta", {}) if isinstance(raw, dict) else {}
    meta = normalize_route_meta(meta)

    return {
        "ok": True,
        "warning": clean_text(meta.get("note")),
        "routes": routes,
        "variant": variant,
        "label": VARIANT_LABELS[variant],
        "file_used": file_path.name,
        "meta": meta,
    }


def load_old_payload():
    file_path = OUTPUT_DIR / "old_routes.json"
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


def extract_variant_run_meta(variant):
    payload = load_variant_payload(variant)
    meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
    return {
        "variant": variant,
        "label": VARIANT_LABELS.get(variant, variant),
        "scheduled_db_points": to_int(meta.get("scheduled_db_points"), 0),
        "unassigned_db_points": to_int(meta.get("unassigned_db_points"), 0),
        "total_db_points": to_int(meta.get("total_db_points"), 0),
        "assigned_task_count": to_int(meta.get("assigned_task_count"), 0),
        "unassigned_task_count": to_int(meta.get("unassigned_task_count"), 0),
        "unassigned_node_count": to_int(meta.get("unassigned_node_count"), 0),
        "download_key": clean_text(meta.get("unassigned_download_key")) or "",
        "download_filename": clean_text(meta.get("unassigned_download_filename")) or "",
        "message": clean_text(meta.get("summary_message")) or "",
    }

def login_view(request):
    """管理者登入。未審核帳號不能登入，admin 帳號固定視為高階管理者。"""
    if request.method == "POST":
        try:
            data = json.loads(request.body or "{}")
            u_name = data.get("userid", "").strip()
            p_word = data.get("password", "").strip()

            if not u_name or not p_word:
                return JsonResponse({"success": False, "message": "請輸入管理員帳號與密碼"})

            if u_name == "admin":
                ensure_admin_superuser("admin")

            user = authenticate(request, username=u_name, password=p_word)

            if user is not None:
                if not user.is_active:
                    return JsonResponse({
                        "success": False,
                        "message": "此帳號尚未審核或已停用，請聯絡高階管理者。",
                    })
                login(request, user)
                write_admin_log(request, "管理者登入", user.username)
                return JsonResponse({"success": True})

            # authenticate 對 is_active=False 會直接失敗，所以額外判斷提示更清楚。
            pending_user = User.objects.filter(username=u_name).first()
            if pending_user and not pending_user.is_active:
                return JsonResponse({
                    "success": False,
                    "message": "此帳號尚未審核或已停用，請等待高階管理者開通。",
                })

            return JsonResponse({"success": False, "message": "管理員帳號或密碼錯誤"})
        except Exception as e:
            return JsonResponse({"success": False, "message": f"系統錯誤: {str(e)}"})

    return render(request, "routing/login.html")


def logout_view(request):
    if request.user.is_authenticated:
        write_admin_log(request, "管理者登出", request.user.username)
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

            if not u_name or not p_word:
                return JsonResponse({"success": False, "message": "請填寫完整帳號與密碼"})

            if User.objects.filter(username=u_name).exists():
                return JsonResponse({"success": False, "message": "此管理員帳號已存在"})

            user = User.objects.create_user(username=u_name, password=p_word)
            user.is_active = False
            user.is_staff = False
            user.is_superuser = False
            user.save(update_fields=["is_active", "is_staff", "is_superuser"])

            # 若系統還沒有 admin，仍不自動開通申請者，避免公開註冊直接變管理員。
            return JsonResponse({
                "success": True,
                "pending": True,
                "message": "帳號申請已送出，請等待高階管理者審核後再登入。",
            })
        except Exception as e:
            return JsonResponse({"success": False, "message": f"申請失敗: {str(e)}"})

    return render(request, "routing/register.html")


@login_required(login_url="login")
@user_passes_test(is_super_admin, login_url="home")
def account_management(request):
    ensure_admin_superuser("admin")
    users = User.objects.all().order_by("-date_joined")
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
@user_passes_test(is_super_admin, login_url="home")
@require_POST
def account_management_action(request, user_id):
    ensure_admin_superuser("admin")
    target = get_object_or_404(User, pk=user_id)
    action = request.POST.get("action", "").strip()

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


@login_required(login_url="login")
@user_passes_test(is_super_admin, login_url="home")
def admin_action_logs_page(request):
    return render(request, "routing/admin_action_logs.html", {"logs": read_admin_logs()})


@login_required(login_url="login")
def home(request):
    initial_variant = request.GET.get("variant", "normal")
    if initial_variant not in VARIANT_LABELS:
        initial_variant = "normal"

    total_points = ServicePoint.objects.count()
    depots_count = (
        ServicePoint.objects.exclude(depot__isnull=True)
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
        },
    )


def run_scheduler(request):
    variant = request.GET.get("variant", "normal")
    if variant not in VARIANT_LABELS:
        variant = "normal"

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

        write_admin_log(request, "重新計算最佳路徑", variant, {"mode": "background"})
        thread = threading.Thread(target=_run_scheduler_background, args=(variant,), daemon=True)
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
    write_admin_log(request, "重新計算最佳路徑", variant, {"mode": "sync"})
    try:
        result = subprocess.run(
            [sys.executable, "run_all.py"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=3600,
        )
        log_path = OUTPUT_DIR / "run_all_last.log"
        log_path.write_text(
            f"Return code: {result.returncode}\n\n=== STDOUT ===\n{result.stdout or ''}\n\n=== STDERR ===\n{result.stderr or ''}",
            encoding="utf-8",
        )
        if result.returncode == 0:
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
    payload = load_variant_payload(variant)

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
            "meta": payload["meta"],
            "route_count": len(routes),
            "drivers": drivers,
            "days_by_driver": days_by_driver,
        }
    )


def api_route_detail(request):
    variant = request.GET.get("variant", "normal")
    payload = load_variant_payload(variant)

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

    requested_driver_param = clean_text(request.GET.get("driver"))
    requested_driver = requested_driver_param or routes[0]["driver"]
    driver = requested_driver
    assignment = get_driver_assignment(driver)
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
            "meta": payload["meta"],
            "requested_driver": requested_driver,
            "schedule_slot": driver if driver != requested_driver else "",
            "route": route,
        }
    )


def api_old_route_options(request):
    payload = load_old_payload()

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
    payload = load_old_payload()

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

    payload = load_variant_payload(variant)
    if not payload.get("ok"):
        return JsonResponse(payload, status=404)

    q_norm = _norm_for_search(q)
    db_point = None
    db_candidates = []

    if q.isdigit():
        try:
            db_point = ServicePoint.objects.filter(id=int(q)).first()
        except Exception:
            db_point = None

    if db_point is None:
        db_candidates = list(ServicePoint.objects.filter(
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

    rows = ServicePoint.objects.all().order_by("id")

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

    allowed_page_sizes = [20, 50, 100, 200]
    try:
        page_size = int(page_size_raw)
    except ValueError:
        page_size = 20

    if page_size not in allowed_page_sizes:
        page_size = 20

    rows = ServicePoint.objects.all().order_by("id")

    if q:
        rows = rows.filter(
            Q(client_name__icontains=q)
            | Q(address__icontains=q)
            | Q(order_id__icontains=q)
        )

    if selected_depot:
        rows = rows.filter(depot=selected_depot)

    depots = (
        ServicePoint.objects.exclude(depot__isnull=True)
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
        },
    )


@login_required(login_url="login")
@user_passes_test(is_manager, login_url="data_list")
def data_add(request):
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
        write_admin_log(request, "新增點位", getattr(obj, "id", ""), {"address": getattr(obj, "address", "")})
        return redirect("data_list")

    return render(request, "routing/data_form.html", {"mode": "add", "row": None})


@login_required(login_url="login")
@user_passes_test(is_manager, login_url="data_list")
def data_edit(request, pk):
    row = get_object_or_404(ServicePoint, pk=pk)

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
    row = get_object_or_404(ServicePoint, pk=pk)

    if request.method == "POST":
        target_info = {"id": row.id, "address": row.address or "", "client_name": row.client_name or ""}
        row.delete()
        write_admin_log(request, "刪除點位", target_info.get("id"), target_info)
        return redirect("data_list")

    return render(request, "routing/data_delete.html", {"row": row})


@login_required(login_url="login")
@user_passes_test(is_manager, login_url="data_list")
def data_import(request):
    error_message = ""
    summary = None

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
                                ServicePoint.objects.create(
                                    id=row_id,
                                    created_at=timezone.now(),
                                    **data,
                                )
                                created_count += 1
                        else:
                            ServicePoint.objects.create(
                                created_at=timezone.now(),
                                **data,
                            )
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
