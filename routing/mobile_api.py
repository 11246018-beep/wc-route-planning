from django.http import FileResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import Driver
from django.utils import timezone
from django.db import connection

import os
import tempfile
from django.conf import settings
from ultralytics import YOLO

from .views import OUTPUT_DIR, VARIANT_LABELS, load_variant_payload, to_float, to_int

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
    response["Access-Control-Allow-Headers"] = "Content-Type"
    response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def normalize_variant(value):
    value = (value or "normal").strip().lower()
    if value not in VARIANT_LABELS:
        return "normal"
    return value


def find_driver_route(routes, driver_code, day):
    target_code = (driver_code or "").strip().upper()
    target_day = to_int(day, 1)

    for route in routes:
        route_driver = str(route.get("driver") or "").strip().upper()
        route_day = to_int(route.get("day"), 0)
        if route_driver == target_code and route_day == target_day:
            return route

    return None


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


def driver_task_api(request):
    if request.method != "GET":
        return JsonResponse({"ok": False, "message": "只允許 GET"}, status=405)

    driver_code = (request.GET.get("driver_code") or "").strip().upper()
    day = to_int(request.GET.get("day"), 1)
    variant = normalize_variant(request.GET.get("variant"))

    if not driver_code:
        return JsonResponse({"ok": False, "message": "缺少 driver_code"}, status=400)

    payload = load_variant_payload(variant)
    if not payload["ok"]:
        return JsonResponse(
            {
                "ok": False,
                "message": payload["warning"] or "找不到排程資料",
                "variant": variant,
            },
            status=404,
        )

    routes = payload["routes"]
    matched_route = find_driver_route(routes, driver_code, day)

    if matched_route is None:
        available_days = sorted(
            {
                to_int(route.get("day"), 0)
                for route in routes
                if str(route.get("driver") or "").strip().upper() == driver_code
            }
        )
        return JsonResponse(
            {
                "ok": False,
                "message": f"找不到 {driver_code} 第 {day} 天的排程",
                "driver_code": driver_code,
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
        return cors_json({"ok": False, "message": "只允許 POST"}, status=405)

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

        if not report_type:
            return cors_json({"ok": False, "message": "請選擇回報類型"}, status=400)

        if not content:
            return cors_json({"ok": False, "message": "請填寫回報內容"}, status=400)

        reports = load_reports()
        report_id = get_next_report_id(reports)
        now = datetime.now()

        new_report = {
            "id": report_id,
            "driver_code": driver_code,
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
                "message": "工作回報已送出",
                "report": new_report,
            }
        )

    except Exception as e:
        return cors_json(
            {"ok": False, "message": f"送出回報失敗：{str(e)}"},
            status=500,
        )


def driver_reports_api(request):
    if request.method != "GET":
        return JsonResponse({"ok": False, "message": "只允許 GET"}, status=405)

    driver_code = (request.GET.get("driver_code") or "").strip().upper()
    limit = to_int(request.GET.get("limit"), 10)

    reports = load_reports()

    if driver_code:
        reports = [
            item for item in reports
            if str(item.get("driver_code") or "").strip().upper() == driver_code
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
        return cors_json({"ok": False, "message": "只允許 POST"}, status=405)

    try:
        data = json.loads(request.body or "{}")
        report_id = to_int(data.get("id"), 0)
        report_type = (data.get("report_type") or "").strip()
        content = (data.get("content") or "").strip()
        stop_seq = to_int(data.get("stop_seq"), 0)
        status_value = (data.get("status") or "").strip()

        if report_id <= 0:
            return cors_json({"ok": False, "message": "缺少有效的回報 id"}, status=400)

        if not report_type:
            return cors_json({"ok": False, "message": "請選擇回報類型"}, status=400)

        if not content:
            return cors_json({"ok": False, "message": "請填寫回報內容"}, status=400)

        reports = load_reports()
        idx = find_report_index(reports, report_id)

        if idx < 0:
            return cors_json({"ok": False, "message": "找不到要編輯的回報資料"}, status=404)

        reports[idx]["report_type"] = report_type
        reports[idx]["content"] = content
        reports[idx]["stop_seq"] = stop_seq
        reports[idx]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if status_value:
            reports[idx]["status"] = status_value

        save_reports(reports)

        return cors_json(
            {
                "ok": True,
                "message": "工作回報已更新",
                "report": reports[idx],
            }
        )

    except Exception as e:
        return cors_json(
            {"ok": False, "message": f"更新失敗：{str(e)}"},
            status=500,
        )


@csrf_exempt
def driver_report_delete_api(request):
    if request.method == "OPTIONS":
        return cors_json({"ok": True})

    if request.method != "POST":
        return cors_json({"ok": False, "message": "只允許 POST"}, status=405)

    try:
        data = json.loads(request.body or "{}")
        report_id = to_int(data.get("id"), 0)

        if report_id <= 0:
            return cors_json({"ok": False, "message": "缺少有效的回報 id"}, status=400)

        reports = load_reports()
        idx = find_report_index(reports, report_id)

        if idx < 0:
            return cors_json({"ok": False, "message": "找不到要刪除的回報資料"}, status=404)

        deleted_report = reports.pop(idx)
        save_reports(reports)

        return cors_json(
            {
                "ok": True,
                "message": "工作回報已刪除",
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
        return JsonResponse({"ok": False, "message": "只允許 GET"}, status=405)

    key = (request.GET.get("key") or "dispatch_latest").strip()
    filename = EXPORTABLE_FILES.get(key)

    if not filename:
        return JsonResponse(
            {
                "ok": False,
                "message": "找不到對應的匯出檔案 key",
                "available_keys": sorted(EXPORTABLE_FILES.keys()),
            },
            status=400,
        )

    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        return JsonResponse(
            {
                "ok": False,
                "message": f"找不到檔案：{filename}",
            },
            status=404,
        )

    return FileResponse(open(file_path, "rb"), as_attachment=True, filename=filename)


def admin_cleaning_records_api(request):
    if request.method != "GET":
        return cors_json({"ok": False, "message": "只允許 GET"}, status=405)

    driver_code = (request.GET.get("driver_code") or "").strip()
    status = (request.GET.get("status") or "").strip()
    photo_type = (request.GET.get("photo_type") or "").strip()
    date = (request.GET.get("date") or "").strip()

    sql = """
        SELECT
            driver_code,
            photo_type,
            public_url,
            is_qualified,
            review_status,
            is_risk,
            risk_score,
            risk_reason,
            stop_address,
            created_at
        FROM uploaded_photos
        WHERE 1=1
    """
    params = []

    # 篩選
    if driver_code:
        sql += " AND driver_code = %s"
        params.append(driver_code)

    if photo_type:
        sql += " AND photo_type = %s"
        params.append(photo_type)

    if status == "合格":
        sql += " AND photo_type = 'after' AND is_qualified = true"
    elif status == "不合格":
        sql += " AND photo_type = 'after' AND is_qualified = false"
    elif status == "高風險":
        sql += " AND is_risk = true"

    if date:
        sql += " AND DATE(created_at) = %s"
        params.append(date)

    sql += " ORDER BY created_at DESC LIMIT 100"

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    records = []

    for row in rows:
        driver_code = row[0]
        photo_type = row[1]
        public_url = row[2]
        is_qualified = row[3]
        review_status = row[4]
        is_risk = row[5]

        # 狀態統一
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
            "driver_code": driver_code,
            "photo_type": photo_type,
            "public_url": public_url,
            "image_url": public_url,
            "is_qualified": is_qualified,
            "status": status_text,
            "review_status": review_status,
            "is_risk": is_risk,
            "risk_score": row[6],
            "risk_reason": row[7],
            "stop_address": row[8],
            "created_at": row[9].strftime("%Y-%m-%d %H:%M:%S") if row[9] else "",
        })

    return cors_json({
        "ok": True,
        "records": records
    })

def admin_cleaning_summary_api(request):
    if request.method != "GET":
        return cors_json({"ok": False, "message": "只允許 GET"}, status=405)

    with connection.cursor() as cursor:
        cursor.execute("""
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
        """)
        summary_row = cursor.fetchone()

        cursor.execute("""
            SELECT
                driver_code,
                COUNT(*) FILTER (WHERE photo_type = 'after') AS total_after,
                COUNT(*) FILTER (WHERE photo_type = 'after' AND is_qualified = true) AS qualified_after
            FROM uploaded_photos
            GROUP BY driver_code
            ORDER BY driver_code
        """)
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
        return cors_json({"ok": False, "message": "只允許 POST"}, status=405)

    driver_code = (request.POST.get("driver_code") or "").strip().upper()
    photo_type = (request.POST.get("photo_type") or "before").strip().lower()

    if not driver_code:
        return cors_json({"ok": False, "message": "缺少 driver_code"}, status=400)

    try:
        driver = Driver.objects.get(driver_code=driver_code)
    except Driver.DoesNotExist:
        return cors_json({"ok": False, "message": "找不到司機"}, status=404)

    image = request.FILES.get("image")
    if not image:
        return cors_json({"ok": False, "message": "缺少 image"}, status=400)

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
            reason = "環境過於髒亂" if is_risk else "環境狀況尚可"

            return cors_json({
                "ok": True,
                "photo_type": "before",
                "predictions": predictions,
                "class_counts": class_counts,
                "risk_score": risk_score,
                "is_risk": is_risk,
                "reason": reason,
                "message": "辨識完成",
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
                "message": "辨識完成",
            })

    except Exception as e:
        return cors_json({
            "ok": False,
            "message": f"辨識失敗: {str(e)}"
        }, status=500)

    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)