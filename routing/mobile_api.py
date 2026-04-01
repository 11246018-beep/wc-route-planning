from django.http import FileResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .views import OUTPUT_DIR, VARIANT_LABELS, load_variant_payload, to_float, to_int

import json
from datetime import datetime


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