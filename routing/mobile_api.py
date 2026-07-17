from django.http import FileResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import Driver, DriverCompanyProfile, ServicePoint, ServicePointCompanyProfile
from django.utils import timezone
from django.db import connection

import os
import tempfile
from django.conf import settings
from ultralytics import YOLO

from .views import OUTPUT_DIR, VARIANT_LABELS, load_variant_payload, to_float, to_int, write_admin_log, is_manager
from .security import authenticate_driver_token, find_driver_by_code, is_manager_user
from .services.driver_roster import get_driver_assignment, load_profiles, normalize_schedule_slot
from .tenant import company_output_dir, get_driver_company, get_user_company, tenant_file_path

import json
from datetime import datetime

MODEL_PATH = os.path.join(settings.BASE_DIR, "models_ai", "best.pt")
yolo_model = YOLO(MODEL_PATH)


EXPORTABLE_FILES = {
    "dispatch_latest": "Dispatch_Report_Latest.xlsx",
    "weekly_summary": "Weekly_Schedule_Summary.xlsx",
    "daily_summary": "Daily_Route_Summary.xlsx",
    "weekly_normal": "Weekly_Schedule_Summary_normal.xlsx",
    "daily_normal": "Daily_Route_Summary_normal.xlsx",
    "weekly_cross": "Weekly_Schedule_Summary_cross.xlsx",
    "daily_cross": "Daily_Route_Summary_cross.xlsx",
    "weekly_compact": "Weekly_Schedule_Summary_compact.xlsx",
    "daily_compact": "Daily_Route_Summary_compact.xlsx",
    "unassigned_normal": "Unassigned_Points_normal.xlsx",
    "unassigned_cross": "Unassigned_Points_cross.xlsx",
    "unassigned_compact": "Unassigned_Points_compact.xlsx",
}

REPORTS_FILE = OUTPUT_DIR / "driver_reports.json"


def cors_json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Driver-Token"
    response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def normalize_variant(value):
    value = (value or "normal").strip().lower()
    if value not in VARIANT_LABELS:
        return "normal"
    return value


def require_driver_request(request, driver_code=None):
    if is_manager_user(getattr(request, "user", None)):
        return None, None
    driver, error = authenticate_driver_token(request, driver_code)
    if driver is None:
        return None, cors_json({"ok": False, "message": error}, status=403)
    return driver, None


def ensure_driver_code_from_token(request, driver_code):
    driver_code = (driver_code or "").strip().upper()
    driver, error_response = require_driver_request(request, driver_code or None)
    if error_response is not None:
        return driver_code, error_response, None
    if not driver_code and driver is not None:
        driver_code = str(driver.driver_code or "").strip().upper()
    return driver_code, None, driver


def valid_uploaded_image(image):
    if not image:
        return False, "缺少 image"
    max_size = int(getattr(settings, "APP_IMAGE_MAX_UPLOAD_BYTES", 0) or 0)
    if max_size > 0 and getattr(image, "size", 0) and image.size > max_size:
        return False, "圖片檔案過大"

    filename = (getattr(image, "name", "") or "").lower()
    ext = os.path.splitext(filename)[1]
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
    content_type = (getattr(image, "content_type", "") or "").lower()
    generic_types = {"", "application/octet-stream", "binary/octet-stream"}
    if content_type in generic_types and ext in image_exts:
        return True, ""
    if content_type and not content_type.startswith("image/"):
        return False, "只允許上傳圖片檔案"
    return True, ""


def format_taipei_datetime(value):
    if not value:
        return ""
    try:
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


def first_value(record, *keys):
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def find_driver_route(routes, driver_code, day):
    target_code = (driver_code or "").strip().upper()
    target_day = to_int(day, 1)

    for route in routes:
        route_driver = str(route.get("driver") or "").strip().upper()
        route_day = to_int(route.get("day"), 0)
        if route_driver == target_code and route_day == target_day:
            return route

    return None


def get_route_lookup_code(driver_code, routes, profiles=None):
    """Resolve an app account to its fixed schedule slot when routes are slot-based."""
    target_code = (driver_code or "").strip().upper()
    assignment = get_driver_assignment(target_code, profiles=profiles)
    schedule_slot = normalize_schedule_slot((assignment or {}).get("schedule_slot"))
    direct_routes = [
        route for route in routes
        if str(route.get("driver") or "").strip().upper() == target_code
    ]

    if direct_routes and (
        not schedule_slot
        or schedule_slot == target_code
        or any(normalize_schedule_slot(route.get("schedule_slot")) == schedule_slot for route in direct_routes)
    ):
        return target_code

    if schedule_slot and any(
        str(route.get("driver") or "").strip().upper() == schedule_slot for route in routes
    ):
        return schedule_slot

    if direct_routes:
        return target_code

    return target_code


def load_reports():
    if not REPORTS_FILE.exists():
        return []

    try:
        with REPORTS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except Exception:
        return []


def save_reports(reports):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with REPORTS_FILE.open("w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)


def get_next_report_id(reports):
    if not reports:
        return 1
    ids = []
    for item in reports:
        try:
            ids.append(int(item.get("id", 0)))
        except Exception:
            pass
    return (max(ids) + 1) if ids else 1


def find_report_index(reports, report_id):
    target_id = to_int(report_id, 0)
    for index, item in enumerate(reports):
        if to_int(item.get("id"), 0) == target_id:
            return index
    return -1


def is_platform_admin_user(user):
    return bool(
        user
        and user.is_authenticated
        and user.username == "system_admin"
        and user.is_superuser
    )


def admin_company_for_request(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    if is_platform_admin_user(user):
        return None
    return get_user_company(user)


def admin_driver_codes_for_request(request):
    company = admin_company_for_request(request)
    if company is False:
        return []
    if company is None:
        return None
    if not getattr(company, "id", None):
        return []
    return [
        str(code or "").strip().upper()
        for code in DriverCompanyProfile.objects.filter(company=company).values_list("driver_code", flat=True)
        if str(code or "").strip()
    ]


def admin_company_point_addresses_for_request(request):
    company = admin_company_for_request(request)
    if company is False:
        return []
    if company is None:
        return None
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


def append_driver_code_filter(sql, params, request, column="driver_code"):
    company = admin_company_for_request(request)
    if company is not None and company is not False and getattr(company, "id", None):
        codes = admin_driver_codes_for_request(request)
        addresses = admin_company_point_addresses_for_request(request)
        params.append(company.key)
        if codes and addresses:
            params.extend([codes, addresses])
            return (
                f"{sql} AND (company_key = %s OR "
                f"(NULLIF(company_key, '') IS NULL AND UPPER({column}) = ANY(%s) AND stop_address = ANY(%s)))"
            ), params
        return f"{sql} AND company_key = %s", params
    codes = admin_driver_codes_for_request(request)
    if codes is None:
        return sql, params
    if not codes:
        return f"{sql} AND 1=0", params
    params.append(codes)
    return f"{sql} AND UPPER({column}) = ANY(%s)", params


def report_company_key(report):
    key = str(report.get("company_key") or "").strip()
    if key:
        return key
    driver_code = str(report.get("driver_code") or "").strip().upper()
    if not driver_code:
        return ""
    company = get_driver_company(driver_code)
    return company.key if getattr(company, "id", None) else ""


def report_belongs_to_admin_company(report, request):
    company = admin_company_for_request(request)
    if company is False:
        return False
    if company is None:
        return True
    return report_company_key(report) == company.key


def driver_task_api(request):
    if request.method != "GET":
        return JsonResponse({"ok": False, "message": "只支援 GET"}, status=405)

    driver_code = (request.GET.get("driver_code") or "").strip().upper()
    day = to_int(request.GET.get("day"), 1)
    variant = normalize_variant(request.GET.get("variant"))

    if not driver_code:
        return JsonResponse({"ok": False, "message": "缺少 driver_code"}, status=400)

    driver, auth_error = require_driver_request(request, driver_code)
    if auth_error is not None:
        return auth_error
    company = get_driver_company(driver or driver_code)
    output_dir = company_output_dir(OUTPUT_DIR, company)
    profiles = load_profiles(output_dir / "driver_profiles.json")
    payload = load_variant_payload(variant, output_dir, company=company)
    if not payload["ok"]:
        return JsonResponse(
            {
                "ok": False,
                "message": payload["warning"] or "找不到路線資料",
                "variant": variant,
            },
            status=404,
        )

    routes = payload["routes"]
    route_lookup_code = get_route_lookup_code(driver_code, routes, profiles)
    matched_route = find_driver_route(routes, route_lookup_code, day)

    if matched_route is None:
        available_days = sorted(
            {
                to_int(route.get("day"), 0)
                for route in routes
                if str(route.get("driver") or "").strip().upper() == route_lookup_code
            }
        )
        return JsonResponse(
            {
                "ok": False,
                "message": f"找不到 {driver_code} 第 {day} 天的路線",
                "driver_code": driver_code,
                "schedule_slot": route_lookup_code if route_lookup_code != driver_code else "",
                "day": day,
                "variant": variant,
                "available_days": available_days,
            },
            status=404,
        )

    metrics = matched_route.get("metrics", {}) or {}
    stops = matched_route.get("stops", []) or []

    return JsonResponse(
        {
            "ok": True,
            "variant": payload["variant"],
            "label": payload["label"],
            "warning": payload["warning"],
            "file_used": payload["file_used"],
            "driver_code": driver_code,
            "schedule_slot": route_lookup_code if route_lookup_code != driver_code else "",
            "day": day,
            "route": {
                "route_id": matched_route.get("route_id"),
                "driver": matched_route.get("driver"),
                "driver_label": matched_route.get("driver_label"),
                "depot": matched_route.get("depot"),
                "stop_count": to_int(matched_route.get("stop_count"), len(stops)),
                "counties": matched_route.get("counties", []),
                "cross_county": bool(matched_route.get("cross_county")),
                "metrics": {
                    "service_min": to_float(metrics.get("service_min")) or 0,
                    "drive_min": to_float(metrics.get("drive_min")) or 0,
                    "dist_km": to_float(metrics.get("dist_km")) or 0,
                    "total_min": to_float(metrics.get("total_min")) or 0,
                    "overtime_min": to_float(metrics.get("overtime_min")) or 0,
                },
                "stops": stops,
            },
        }
    )


@csrf_exempt
def driver_report_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "POST":
        return cors_json({"ok": False, "message": "只支援 POST"}, status=405)

    try:
        data = json.loads(request.body or "{}")

        driver_code = (data.get("driver_code") or "").strip().upper()
        report_type = (data.get("report_type") or "").strip()
        content = (data.get("content") or "").strip()
        day = to_int(data.get("day"), 0)
        stop_seq = to_int(data.get("stop_seq"), 0)
        route_id = (data.get("route_id") or "").strip()
        variant = normalize_variant(data.get("variant"))

        if not driver_code:
            return cors_json({"ok": False, "message": "缺少 driver_code"}, status=400)

        driver, auth_error = require_driver_request(request, driver_code)
        if auth_error is not None:
            return auth_error
        if not report_type:
            return cors_json({"ok": False, "message": "請選擇回報類型"}, status=400)

        if not content:
            return cors_json({"ok": False, "message": "請輸入回報內容"}, status=400)

        company = get_driver_company(driver or driver_code)
        reports = load_reports()
        report_id = get_next_report_id(reports)
        now = datetime.now()

        new_report = {
            "id": report_id,
            "driver_code": driver_code,
            "company_key": company.key if getattr(company, "id", None) else "",
            "company_name": company.name if getattr(company, "id", None) else "",
            "day": day,
            "stop_seq": stop_seq,
            "route_id": route_id,
            "variant": variant,
            "report_type": report_type,
            "content": content,
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "created_at_iso": now.isoformat(),
            "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "new",
        }

        reports.insert(0, new_report)
        save_reports(reports)

        return cors_json(
            {
                "ok": True,
                "message": "回報已送出",
                "report": new_report,
            }
        )

    except Exception as e:
        return cors_json({"ok": False, "message": f"送出回報失敗：{str(e)}"}, status=500)


def driver_reports_api(request):
    if request.method != "GET":
        return JsonResponse({"ok": False, "message": "只支援 GET"}, status=405)

    driver_code = (request.GET.get("driver_code") or "").strip().upper()
    backend_user = getattr(request, "user", None)
    token_driver = None
    if not backend_user or not backend_user.is_authenticated:
        driver_code, auth_error, token_driver = ensure_driver_code_from_token(request, driver_code)
        if auth_error is not None:
            return auth_error
    limit = to_int(request.GET.get("limit"), 10)

    reports = load_reports()

    if driver_code:
        reports = [
            item for item in reports
            if str(item.get("driver_code") or "").strip().upper() == driver_code
        ]
    if token_driver is not None:
        company = get_driver_company(token_driver)
        reports = [
            item for item in reports
            if report_company_key(item) == company.key
        ]
    if backend_user and backend_user.is_authenticated:
        reports = [
            item for item in reports
            if report_belongs_to_admin_company(item, request)
        ]

    if limit <= 0:
        limit = 10

    reports = reports[:limit]

    return cors_json(
        {
            "ok": True,
            "count": len(reports),
            "reports": reports,
        }
    )


@csrf_exempt
def driver_report_update_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "POST":
        return cors_json({"ok": False, "message": "只支援 POST"}, status=405)

    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return cors_json({"ok": False, "message": "請先登入後台"}, status=403)
    if not is_manager(request.user):
        return cors_json({"ok": False, "message": "需要管理員權限"}, status=403)

    try:
        data = json.loads(request.body or "{}")
        report_id = to_int(data.get("id"), 0)
        report_type = (data.get("report_type") or "").strip()
        content = (data.get("content") or "").strip()
        stop_seq = to_int(data.get("stop_seq"), 0)
        status_value = (data.get("status") or "").strip()

        if report_id <= 0:
            return cors_json({"ok": False, "message": "缺少回報 id"}, status=400)

        if not report_type:
            return cors_json({"ok": False, "message": "請選擇回報類型"}, status=400)

        if not content:
            return cors_json({"ok": False, "message": "請輸入回報內容"}, status=400)

        reports = load_reports()
        idx = find_report_index(reports, report_id)

        if idx < 0:
            return cors_json({"ok": False, "message": "找不到要編輯的回報"}, status=404)

        if not report_belongs_to_admin_company(reports[idx], request):
            return cors_json({"ok": False, "message": "不能編輯其他公司的回報"}, status=403)

        reports[idx]["report_type"] = report_type
        reports[idx]["content"] = content
        reports[idx]["stop_seq"] = stop_seq
        reports[idx]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if status_value:
            reports[idx]["status"] = status_value

        save_reports(reports)
        write_admin_log(request, "更新問題回報", report_id, {"driver_code": reports[idx].get("driver_code"), "report_type": reports[idx].get("report_type")})

        return cors_json(
            {
                "ok": True,
                "message": "回報已更新",
                "report": reports[idx],
            }
        )

    except Exception as e:
        return cors_json({"ok": False, "message": f"更新失敗：{str(e)}"}, status=500)


@csrf_exempt
def driver_report_delete_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "POST":
        return cors_json({"ok": False, "message": "只支援 POST"}, status=405)

    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return cors_json({"ok": False, "message": "請先登入後台"}, status=403)
    if not is_manager(request.user):
        return cors_json({"ok": False, "message": "需要管理員權限"}, status=403)

    try:
        data = json.loads(request.body or "{}")
        report_id = to_int(data.get("id"), 0)

        if report_id <= 0:
            return cors_json({"ok": False, "message": "缺少回報 id"}, status=400)

        reports = load_reports()
        idx = find_report_index(reports, report_id)

        if idx < 0:
            return cors_json({"ok": False, "message": "找不到要刪除的回報"}, status=404)

        if not report_belongs_to_admin_company(reports[idx], request):
            return cors_json({"ok": False, "message": "不能刪除其他公司的回報"}, status=403)

        deleted_report = reports.pop(idx)
        save_reports(reports)
        write_admin_log(request, "刪除問題回報", report_id, {"driver_code": deleted_report.get("driver_code"), "report_type": deleted_report.get("report_type")})

        return cors_json(
            {
                "ok": True,
                "message": "回報已刪除",
                "deleted_report": deleted_report,
            }
        )

    except Exception as e:
        return cors_json(
            {"ok": False, "message": f"刪除失敗：{str(e)}"},
            status=500,
        )


def export_excel_api(request):
    if request.method != "GET":
        return JsonResponse({"ok": False, "message": "只支援 GET"}, status=405)

    key = (request.GET.get("key") or "dispatch_latest").strip()
    filename = EXPORTABLE_FILES.get(key)

    if not filename:
        return JsonResponse(
            {
                "ok": False,
                "message": "找不到匯出檔案 key",
                "available_keys": sorted(EXPORTABLE_FILES.keys()),
            },
            status=400,
        )

    company = get_user_company(getattr(request, "user", None))
    file_path = tenant_file_path(OUTPUT_DIR, company, filename, fallback=False)
    if not file_path.exists():
        return JsonResponse(
            {
                "ok": False,
                "message": f"找不到匯出檔案：{filename}",
            },
            status=404,
        )

    return FileResponse(open(file_path, "rb"), as_attachment=True, filename=filename)


def admin_cleaning_records_api(request):
    if request.method != "GET":
        return cors_json({"ok": False, "message": "只支援 POST"}, status=405)

    driver_code = (request.GET.get("driver_code") or "").strip()
    status = (request.GET.get("status") or "").strip()
    photo_type = (request.GET.get("photo_type") or "").strip()
    date = (request.GET.get("date") or "").strip()

    sql = "SELECT * FROM uploaded_photos WHERE 1=1"
    params = []

    if driver_code:
        sql += " AND UPPER(driver_code) = %s"
        params.append(driver_code.upper())

    sql, params = append_driver_code_filter(sql, params, request)

    if photo_type:
        sql += " AND photo_type = %s"
        params.append(photo_type)

    if status == "合格":
        sql += " AND photo_type = 'after' AND is_qualified = true"
    elif status == "不合格":
        sql += " AND photo_type = 'after' AND is_qualified = false"
    elif status == "風險":
        sql += " AND is_risk = true"

    if date:
        sql += " AND DATE(created_at) = %s"
        params.append(date)

    sql += " ORDER BY created_at DESC LIMIT 100"

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        column_names = [column[0] for column in cursor.description]
        rows = cursor.fetchall()

    raw_records = [dict(zip(column_names, row)) for row in rows]
    addresses = {
        str(record.get("stop_address") or "").strip()
        for record in raw_records
        if str(record.get("stop_address") or "").strip()
    }
    point_coordinates = {}
    if addresses:
        company = get_user_company(getattr(request, "user", None))
        point_qs = ServicePoint.objects.filter(address__in=addresses)
        if getattr(company, "id", None):
            point_ids = ServicePointCompanyProfile.objects.filter(company=company).values_list("service_point_id", flat=True)
            point_qs = point_qs.filter(id__in=point_ids)
        for point in point_qs.values("address", "lat", "lon"):
            address = str(point.get("address") or "").strip()
            if address and address not in point_coordinates:
                point_coordinates[address] = {
                    "lat": point.get("lat"),
                    "lon": point.get("lon"),
                }

    records = []

    for row in raw_records:
        record_id = row.get("id")
        driver_code = row.get("driver_code")
        photo_type = row.get("photo_type")
        public_url = row.get("public_url")
        is_qualified = row.get("is_qualified")
        review_status = row.get("review_status")
        is_risk = row.get("is_risk")
        address = str(row.get("stop_address") or "").strip()
        stored_point = point_coordinates.get(address, {})
        point_lat = first_value(row, "point_lat", "stop_lat") or stored_point.get("lat")
        point_lon = first_value(row, "point_lon", "stop_lon") or stored_point.get("lon")
        photo_lat = first_value(row, "driver_lat", "photo_lat", "capture_lat", "captured_lat", "gps_lat")
        photo_lon = first_value(row, "driver_lon", "photo_lon", "capture_lon", "captured_lon", "gps_lon")

        if photo_type == "after":
            if is_qualified is True:
                status_text = "合格"
            elif is_qualified is False:
                status_text = "不合格"
            else:
                status_text = review_status or "-"
        else:
            status_text = review_status or "-"

        records.append({
            "id": str(record_id) if record_id is not None else "",
            "driver_code": driver_code,
            "photo_type": photo_type,
            "public_url": public_url,
            "image_url": public_url,
            "is_qualified": is_qualified,
            "status": status_text,
            "review_status": review_status,
            "is_risk": is_risk,
            "risk_score": row.get("risk_score"),
            "risk_reason": row.get("risk_reason"),
            "stop_address": address,
            "created_at": format_taipei_datetime(row.get("created_at")),
            "timezone": "Asia/Taipei",
            "point_lat": point_lat,
            "point_lon": point_lon,
            "photo_lat": photo_lat,
            "photo_lon": photo_lon,
        })

    return cors_json({
        "ok": True,
        "records": records
    })


@csrf_exempt
def admin_cleaning_record_delete_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "POST":
        return cors_json({"ok": False, "message": "只支援 POST"}, status=405)

    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return cors_json({"ok": False, "message": "請先登入後台"}, status=403)
    if not is_manager(request.user):
        return cors_json({"ok": False, "message": "需要管理員權限"}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        payload = {}

    raw_ids = payload.get("ids") or []
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]

    ids = [str(v).strip() for v in raw_ids if str(v).strip()]
    if not ids:
        return cors_json({"ok": False, "message": "請選擇要刪除的紀錄 ID"}, status=400)

    try:
        with connection.cursor() as cursor:
            sql, params = append_driver_code_filter(
                "DELETE FROM uploaded_photos WHERE id::text = ANY(%s)",
                [ids],
                request,
            )
            cursor.execute(
                f"{sql} RETURNING id",
                params,
            )
            deleted_rows = cursor.fetchall()

        deleted_ids = [str(row[0]) for row in deleted_rows]
        write_admin_log(
            request,
            "刪除清掃紀錄",
            ",".join(deleted_ids) if deleted_ids else ",".join(ids),
            {
                "requested_ids": ids,
                "deleted_ids": deleted_ids,
                "deleted_count": len(deleted_ids),
            },
        )

        return cors_json({
            "ok": True,
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
        })
    except Exception as e:
        return cors_json({"ok": False, "message": str(e)}, status=500)


def admin_cleaning_summary_api(request):
    if request.method != "GET":
        return cors_json({"ok": False, "message": "只支援 POST"}, status=405)

    summary_sql = """
            SELECT
                COUNT(*) AS total_count,
                AVG(CASE
                    WHEN risk_score IS NOT NULL THEN risk_score
                    ELSE NULL
                END) AS avg_risk_score,
                SUM(CASE
                    WHEN photo_type = 'after' AND is_qualified = true THEN 1
                    ELSE 0
                END) AS pass_count,
                SUM(CASE
                    WHEN photo_type = 'after' AND is_qualified = false THEN 1
                    ELSE 0
                END) AS fail_count
            FROM uploaded_photos
            WHERE 1=1
    """
    summary_sql, summary_params = append_driver_code_filter(summary_sql, [], request)

    driver_sql = """
            SELECT
                driver_code,
                COUNT(*) FILTER (WHERE photo_type = 'after') AS total_after,
                COUNT(*) FILTER (WHERE photo_type = 'after' AND is_qualified = true) AS qualified_after
            FROM uploaded_photos
            WHERE 1=1
    """
    driver_sql, driver_params = append_driver_code_filter(driver_sql, [], request)
    driver_sql += """
            GROUP BY driver_code
            ORDER BY driver_code
    """

    with connection.cursor() as cursor:
        cursor.execute(summary_sql, summary_params)
        summary_row = cursor.fetchone()

        cursor.execute(driver_sql, driver_params)
        driver_rows = cursor.fetchall()

    total_count = summary_row[0] or 0
    avg_score = round(float(summary_row[1] or 0), 1)
    pass_count = summary_row[2] or 0
    fail_count = summary_row[3] or 0

    drivers = []
    for row in driver_rows:
        driver_code = row[0]
        total_after = row[1] or 0
        qualified_after = row[2] or 0
        rate = round((qualified_after / total_after * 100), 1) if total_after > 0 else 0

        drivers.append({
            "driver_code": driver_code,
            "avg_score": rate,
            "count": total_after,
            "qualified_count": qualified_after,
        })
    return cors_json({
        "ok": True,
        "summary": {
            "total_count": total_count,
            "avg_score": avg_score,
            "pass_count": pass_count,
            "fail_count": fail_count,
        },
        "drivers": drivers
    })

@csrf_exempt
def detect_cleaning_ai_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "POST":
        return cors_json({"ok": False, "message": "只支援 POST"}, status=405)

    driver_code = (request.POST.get("driver_code") or "").strip().upper()
    photo_type = (request.POST.get("photo_type") or "before").strip().lower()

    if not driver_code:
        return cors_json({"ok": False, "message": "缺少 driver_code"}, status=400)

    driver, auth_error = require_driver_request(request, driver_code)
    if auth_error is not None:
        return auth_error
    if not driver:
        return cors_json({"ok": False, "message": "找不到司機帳號"}, status=404)

    image = request.FILES.get("image")
    is_valid_image, image_error = valid_uploaded_image(image)
    if not is_valid_image:
        return cors_json({"ok": False, "message": image_error}, status=400)

    temp_file_path = None

    try:
        suffix = os.path.splitext(image.name)[1] or ".jpg"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            for chunk in image.chunks():
                temp_file.write(chunk)
            temp_file_path = temp_file.name

        results = yolo_model.predict(source=temp_file_path, conf=0.10, save=False)

        predictions = []
        class_counts = {}

        for result in results:
            boxes = result.boxes
            names = result.names

            for box in boxes:
                cls_id = int(box.cls[0].item())
                conf = float(box.conf[0].item())
                class_name = names[cls_id]

                predictions.append({
                    "class": class_name,
                    "confidence": round(conf, 4),
                })

                class_counts[class_name] = class_counts.get(class_name, 0) + 1

        overflow_bin = class_counts.get("overflow_bin", 0)
        dirty_area = class_counts.get("dirty_area", 0)
        bottle = class_counts.get("bottle", 0)
        toiletpaper = class_counts.get("toiletpaper", 0)

        if photo_type == "before":
            risk_score = overflow_bin * 3 + dirty_area * 2 + bottle + toiletpaper
            is_risk = risk_score > 8
            reason = "環境狀況需注意" if is_risk else "環境狀況尚可"

            return cors_json({
                "ok": True,
                "photo_type": "before",
                "predictions": predictions,
                "class_counts": class_counts,
                "risk_score": risk_score,
                "is_risk": is_risk,
                "reason": reason,
                "message": "AI辨識完成",
            })

        else:
            is_qualified = (
                overflow_bin == 0 and
                dirty_area == 0 and
                bottle == 0 and
                toiletpaper == 0
            )

            return cors_json({
                "ok": True,
                "photo_type": "after",
                "predictions": predictions,
                "class_counts": class_counts,
                "is_qualified": is_qualified,
                "status": "合格" if is_qualified else "不合格",
                "message": "AI辨識完成",
            })

    except Exception as e:
        return cors_json({
            "ok": False,
            "message": f"AI辨識失敗：{str(e)}"
        }, status=500)

    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)

