from pathlib import Path
import json
import subprocess
import sys

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import authenticate, login  # 這是驗證 manager1 的關鍵
from django.contrib.auth.models import User

import pandas as pd

from .models import ServicePoint, Driver


BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

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


def driver_sort_key(code):
    s = str(code or "").upper()
    if s.startswith("P") and s[1:].isdigit():
        return (0, int(s[1:]))
    if s.startswith("W") and s[1:].isdigit():
        return (1, int(s[1:]))
    return (9, s)


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

def login_view(request):
    """
    處理管理員(如 manager1)登入邏輯
    """
    if request.method == "POST":
        try:
            # 解析前端傳來的帳號密碼
            data = json.loads(request.body)
            u_name = data.get("userid", "").strip()
            p_word = data.get("password", "").strip()

            # 使用 Django 內建功能驗證你在 Terminal 建立的 manager1 帳號
            user = authenticate(request, username=u_name, password=p_word)

            if user is not None:
                login(request, user)  # 正式建立登入會話 (Session)
                return JsonResponse({"success": True})
            else:
                return JsonResponse({"success": False, "message": "管理員帳號或密碼錯誤"})
        except Exception as e:
            return JsonResponse({"success": False, "message": f"系統錯誤: {str(e)}"})

    # 如果是 GET 請求，就顯示你原本設計的藍色漸層登入頁面
    return render(request, "routing/login.html")

# 註冊頁面
def register_view(request):
    """
    處理管理員註冊邏輯
    """
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            u_name = data.get("username", "").strip()
            p_word = data.get("password", "").strip()

            if not u_name or not p_word:
                return JsonResponse({"success": False, "message": "請填寫完整帳號與密碼"})

            # 檢查帳號是否重複
            if User.objects.filter(username=u_name).exists():
                return JsonResponse({"success": False, "message": "此管理員帳號已存在"})

            # 建立管理員帳號 (自動加密密碼)
            user = User.objects.create_user(username=u_name, password=p_word)
            user.is_staff = True  # 讓他可以進入 Django Admin 後台
            user.save()

            return JsonResponse({"success": True})
        except Exception as e:
            return JsonResponse({"success": False, "message": f"註冊失敗: {str(e)}"})

    # GET 請求時顯示註冊頁面
    return render(request, "routing/register.html")

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

    try:
        result = subprocess.run(
            [sys.executable, "run_all.py"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=600,
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
            return redirect(f"/?variant={variant}&run=success")
        return redirect(f"/?variant={variant}&run=failed")

    except Exception as e:
        (OUTPUT_DIR / "run_all_last.log").write_text(
            f"Exception while running scheduler:\n{e}",
            encoding="utf-8",
        )
        return redirect(f"/?variant={variant}&run=failed")


def api_route_options(request):
    variant = request.GET.get("variant", "normal")
    payload = load_variant_payload(variant)

    if not payload["ok"]:
        return JsonResponse(payload, status=404)

    routes = payload["routes"]
    driver_codes = sorted({r["driver"] for r in routes}, key=driver_sort_key)
    drivers = [{"value": code, "label": driver_label(code)} for code in driver_codes]

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

    driver = clean_text(request.GET.get("driver")) or routes[0]["driver"]
    candidate_routes = [r for r in routes if r["driver"] == driver]
    if not candidate_routes:
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


def data_add(request):
    if request.method == "POST":
        ServicePoint.objects.create(
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
        return redirect("data_list")

    return render(request, "routing/data_form.html", {"mode": "add", "row": None})


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
        return redirect("data_list")

    return render(request, "routing/data_form.html", {"mode": "edit", "row": row})


def data_delete(request, pk):
    row = get_object_or_404(ServicePoint, pk=pk)

    if request.method == "POST":
        row.delete()
        return redirect("data_list")

    return render(request, "routing/data_delete.html", {"row": row})


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