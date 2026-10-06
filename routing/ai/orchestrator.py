"""Safe, domain-specific orchestration for the Dispatch Nav assistant.

The orchestrator deliberately separates model reasoning from domain actions. In
the first iteration all tools are read-only or simulation-only; no tool writes
route files or mutates database records.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

from .executor import PlanExecutor
from .planner import TaskPlanner
from .state import new_execution_state


READ_ONLY_ACTIONS = {
    "basic_info",
    "unassigned_points",
    "overtime_routes",
    "driver_workload",
    "carbon_summary",
    "cleaning_summary",
    "route_change_simulation",
    "route_merge_analysis",
}


def _number_from_text(text: str, default: int = 30) -> int:
    match = re.search(r"(\d+)\s*分", text or "")
    return int(match.group(1)) if match else default


def _first(items: list[dict[str, Any]]) -> dict[str, Any]:
    return items[0] if items else {}


def choose_action(question: str, question_type: str = "dispatch") -> str:
    """Deterministic fallback used when the model is unavailable."""
    q = str(question or "")
    if question_type == "carbon":
        return "carbon_summary"
    if question_type == "cleaning":
        return "cleaning_summary"
    if any(word in q for word in ("合併", "合并", "併線", "併入")):
        return "route_merge_analysis"
    if any(word in q for word in ("減少", "降低", "調整", "移動", "重新派", "方案", "模擬")):
        return "route_change_simulation"
    if any(word in q for word in ("未排", "未安排", "沒有排入")):
        return "unassigned_points"
    if any(word in q for word in ("超時", "超過工時")):
        return "overtime_routes"
    if any(word in q for word in ("工時", "工作量", "負載", "最忙")):
        return "driver_workload"
    if any(word in q for word in ("幾個點位", "幾位司機", "基本資料", "公司資訊")):
        return "basic_info"
    return "route_change_simulation" if question_type == "dispatch" else "basic_info"


def _tool_basic_info(context: dict[str, Any]) -> dict[str, Any]:
    common = context.get("common") or {}
    return {
        "action": "basic_info",
        "company": common.get("company"),
        "counts": common.get("counts"),
        "schedule_settings": common.get("schedule_settings"),
    }


def _tool_dispatch_data(context: dict[str, Any], action: str) -> dict[str, Any]:
    data = context.get("data") or {}
    if action == "unassigned_points":
        return {
            "action": action,
            "variant": data.get("requested_variant"),
            "total": data.get("unassigned_total_count"),
            "by_county": data.get("unassigned_by_county"),
            "items": data.get("unassigned"),
        }
    if action == "overtime_routes":
        return {
            "action": action,
            "items": data.get("overtime_routes") or [],
            "route_rankings": (data.get("driver_rankings") or {}).get("highest_daily_total") or [],
        }
    if action == "driver_workload":
        rankings = data.get("driver_rankings") or {}
        return {
            "action": action,
            "highest_weekly_total": rankings.get("highest_weekly_total") or [],
            "lowest_weekly_total": rankings.get("lowest_weekly_total") or [],
            "highest_daily_total": rankings.get("highest_daily_total") or [],
        }
    if action == "carbon_summary":
        return {
            "action": action,
            "variants": data.get("variants") or {},
            "lowest_carbon_variant": data.get("lowest_carbon_variant"),
            "highest_distance_routes": data.get("carbon_route_rankings") or data.get("highest_distance_routes") or [],
        }
    return {
        "action": action,
        "records": data.get("records") or [],
        "high_demand_points": data.get("high_demand_points") or [],
        "local_cleaning_records": data.get("local_cleaning_records") or [],
        "notes": data.get("notes") or [],
    }


def _tool_route_change_simulation(question: str, context: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded proposal based on existing computed route summaries.

    This is intentionally a proposal, not a route mutation. A later iteration
    can replace this implementation with a real incremental-scheduler dry run.
    """
    data = context.get("data") or {}
    settings = (context.get("common") or {}).get("schedule_settings") or {}
    overtime = list(data.get("overtime_routes") or [])
    target = _number_from_text(question)
    limit = settings.get("daily_work_minutes") or 540
    ranked = sorted(
        overtime,
        key=lambda row: float(row.get("總工時_分") or row.get("total_min") or 0),
        reverse=True,
    )
    route = _first(ranked)
    current = route.get("總工時_分") or route.get("total_min")
    current_value = float(current) if current not in (None, "") else None
    if not route:
        return {
            "action": "route_change_simulation",
            "status": "no_candidate",
            "target_reduction_min": target,
            "message": "目前找不到超時路線，無法建立調整方案。",
            "requires_confirmation": False,
        }
    if current_value is None:
        return {
            "action": "route_change_simulation",
            "status": "insufficient_data",
            "target_reduction_min": target,
            "route": route,
            "message": "找到候選路線，但報表缺少可計算的總工時。",
            "requires_confirmation": False,
        }
    return {
        "action": "route_change_simulation",
        "status": "proposal_only",
        "target_reduction_min": target,
        "constraint_daily_limit_min": limit,
        "candidate_route": route,
        "current_total_min": current_value,
        "target_total_min": max(0, current_value - target),
        "message": (
            "已找到可優先分析的超時路線。這是模擬前的候選方案，"
            "尚未移動點位、尚未寫入路線，也尚未宣稱 OSRM 驗證通過。"
        ),
        "next_step": "需要新增增量排程 dry-run，找出接收路線後才能產生正式前後比較。",
        "requires_confirmation": False,
    }


def run_tool(action: str, question: str, context: dict[str, Any]) -> dict[str, Any]:
    if action not in READ_ONLY_ACTIONS:
        return {"action": action, "status": "blocked", "message": "此工具目前未開放。"}
    if action == "basic_info":
        return _tool_basic_info(context)
    if action == "route_change_simulation":
        return _tool_route_change_simulation(question, context)
    if action == "route_merge_analysis":
        data = context.get("data") or {}
        variant = data.get("requested_variant") or "normal"
        variant_data = (data.get("variants") or {}).get(variant) or {}
        same_driver_only = bool(variant_data.get("same_driver_merge"))
        return {
            "action": action,
            "status": "proposal_only",
            "variant": variant,
            "route_version": variant_data.get("route_version", ""),
            "candidates": variant_data.get("merge_candidates") or [],
            "allow_cross_county": variant in {"cross", "compact"},
            "verification_status": variant_data.get("merge_verification_status", "unknown"),
            "verification_error": variant_data.get("merge_verification_error", ""),
            "same_driver_only": bool(variant_data.get("same_driver_merge")),
            "merge_scope": "同一位司機不同日期" if variant_data.get("same_driver_merge") else "可跨司機合併",
            "message": (
                "以下只列出同一位司機不同日期、且通過完整路線重排驗證的候選。"
                if same_driver_only
                else "以下是依目前工時資料篩選的合併候選；正式合併前仍須重新排序所有站點並通過 OSRM 驗證。"
            ),
            "apply_blocked_reason": (
                ("路線成本 dry-run 發生錯誤：" + str(variant_data.get("merge_verification_error")))
                if variant_data.get("merge_verification_status") == "failed" and variant_data.get("merge_verification_error")
                else "目前沒有通過路線成本 dry-run 的可套用方案；工時預篩選結果不能直接套用。"
                if not any(item.get("verified_by_route_cost") for item in (variant_data.get("merge_candidates") or []))
                else ""
            ),
            "requires_confirmation": False,
        }
    return _tool_dispatch_data(context, action)


class GeminiProvider:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
        self.model = (model or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")).strip()
        self.last_error = ""
        self.last_notice = ""

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.model)

    def complete(self, prompt: str, timeout: int = 30) -> str:
        if not self.available:
            self.last_error = "尚未設定可用的 Gemini API Key 或模型名稱。"
            return ""
        self.last_error = ""
        try:
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                params={"key": self.api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            self.last_error = f"無法連線至 Gemini API（{type(exc).__name__}）。"
            raise RuntimeError(self.last_error) from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not response.ok:
            api_error = payload.get("error") if isinstance(payload, dict) else {}
            detail = str((api_error or {}).get("message") or response.reason or "未知錯誤")
            if response.status_code == 503 and self.model != "gemini-3.1-flash-lite":
                original_model = self.model
                self.model = "gemini-3.1-flash-lite"
                self.last_notice = f"{original_model} 暫時滿載，本次已自動改用 gemini-3.1-flash-lite。"
                return self.complete(prompt, timeout=timeout)
            self.last_error = f"Gemini API {response.status_code}：{detail[:500]}"
            raise RuntimeError(self.last_error)
        parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "\n".join(str(part.get("text", "")) for part in parts if part.get("text")).strip()
        if not text:
            candidate = (payload.get("candidates") or [{}])[0]
            reason = candidate.get("finishReason") or (payload.get("promptFeedback") or {}).get("blockReason") or "空回應"
            self.last_error = f"Gemini 沒有回傳文字（{reason}）。"
            raise RuntimeError(self.last_error)
        return text


class AssistantOrchestrator:
    def __init__(self, question: str, context: dict[str, Any], api_key: str = "", model: str = "", user_id: str = ""):
        self.question = str(question or "").strip()
        self.context = context
        self.user_id = str(user_id or "").strip()
        # The request value is accepted for backwards compatibility only. The
        # UI no longer sends it; production deployments should use env/secret storage.
        self.provider = GeminiProvider(api_key=api_key, model=model)

    def _company_key(self) -> str:
        common = self.context.get("common") or {}
        company = common.get("company") or {}
        return str(self.context.get("company_key") or company.get("key") or "").strip()

    def _model_action(self) -> str:
        fallback = choose_action(self.question, self.context.get("question_type", "dispatch"))
        if self.context.get("question_type") in {"carbon", "cleaning"}:
            return fallback
        if not self.provider.available:
            return fallback
        prompt = (
            "你是 Dispatch Nav 的工具選擇器。只回傳 JSON。\n"
            f"問題：{self.question}\n"
            f"問題類型：{self.context.get('question_type')}\n"
            f"允許 action：{', '.join(sorted(READ_ONLY_ACTIONS))}\n"
            '格式：{"action":"overtime_routes"}\n'
            "涉及合併路線時，必須選 route_merge_analysis；涉及減少、移動、調整或重新派工時，必須選 route_change_simulation。"
        )
        try:
            text = self.provider.complete(prompt, timeout=15).replace("```json", "").replace("```", "").strip()
            action = json.loads(text).get("action", fallback)
            return action if action in READ_ONLY_ACTIONS else fallback
        except Exception:
            return fallback

    def _answer(self, tool_result: dict[str, Any]) -> tuple[str, bool]:
        # Operational metrics must be rendered deterministically. The model
        # may select the tool, but must not omit or alter route measurements.
        if tool_result.get("action") == "route_merge_analysis":
            return self._fallback_answer(tool_result), not self.provider.available
        if not self.provider.available:
            return self._fallback_answer(tool_result), True
        prompt = (
            "你是 Dispatch Nav 的營運助理。請使用繁體中文回答。\n"
            "只能根據工具結果回答，不可編造、不可以宣稱已修改資料。\n"
            "若 status 是 proposal_only，必須明確說明目前只是模擬候選，尚未套用。\n"
            "若工具結果是 insufficient_baseline，除了說明缺口，也要提出最多兩個標示為『未驗證分析方案』的方向與優缺點；不可把方向寫成已達成的數字。\n"
            f"使用者問題：{self.question}\n"
            f"工具結果：{json.dumps(tool_result, ensure_ascii=False, default=str)[:18000]}"
        )
        prompt = (
            "你是 Dispatch Nav 的營運決策助理。請只根據工具結果與對話內容，以繁體中文回答。\n"
            "請使用乾淨的純文字排版，不要輸出 Markdown 星號、井字號或反引號；清單請直接使用『•』。\n"
            "回答規則：先直接回答問題，再列出關鍵數字與資料依據；若適合，提供具體且可執行的管理建議。"
            "不得捏造工具未提供的點位、工時、里程或狀態。資料不足時明確說明還缺什麼。"
            "任何路線修改都只能提出方案，必須提醒需要預覽、OSRM 驗證及管理者確認。\n"
            f"近期對話：{json.dumps(self.context.get('conversation_history') or [], ensure_ascii=False)}\n"
            f"使用者問題：{self.question}\n"
            f"工具結果：{json.dumps(tool_result, ensure_ascii=False, default=str)[:30000]}"
        )
        try:
            answer = self.provider.complete(prompt)
            if answer:
                return answer, False
        except Exception:
            pass
        return self._fallback_answer(tool_result), True

    def _fallback_answer(self, result: dict[str, Any]) -> str:
        if result.get("action") == "analysis_plan":
            lines = ["已完成多步驟分析。", ""]
            steps = result.get("steps") or {}
            for step_id, wrapper in steps.items():
                data = wrapper.get("data") or {}
                tool = wrapper.get("tool") or step_id
                lines.append(f"{step_id}｜{tool}：{wrapper.get('summary', '已完成')}")
                if tool == "rank_carbon_routes":
                    for index, item in enumerate(data.get("items") or [], start=1):
                        lines.append(f"{index}. {item.get('route_id')}（{item.get('driver')} Day {item.get('day')}）：{item.get('estimated_co2_kg', item.get('value', '未知'))} kg CO2e")
                elif tool == "compare_route_variants":
                    for item in (data.get("variants") or []):
                        lines.append(f"{item.get('label') or item.get('variant')}：總工時 {item.get('total_work_min', '未知')} 分鐘；距離 {item.get('total_distance_km', '未知')} 公里；碳排 {item.get('estimated_co2_kg', '未知')} kg CO2e")
            if result.get("errors"):
                lines.append("\n驗證／執行錯誤：" + "；".join(str(error) for error in result["errors"]))
            lines.append("\n以上數值來自後端工具結果；目前只分析，尚未修改正式路線。")
            return "\n".join(lines)
        if result.get("action") == "driver_workload":
            driver = result.get("driver_id") or "指定司機"
            weekly = result.get("weekly") or []
            daily = result.get("daily") or []
            lines = [f"已完成 {driver} 工時分析。"]
            population = result.get("population") or {}
            if population.get("driver_ids"):
                lines.append(f"資料範圍：共 {len(population['driver_ids'])} 位司機；目前顯示的是指定條件符合的資料。")
            if weekly:
                row = weekly[0]
                lines.append(f"一週總工時：{row.get('weekly_total_min', '未知')} 分鐘；工作天數：{row.get('used_days', '未知')} 天；平均每日：{row.get('avg_per_used_day_min', '未知')} 分鐘。")
            if daily:
                lines.append("單日工時明細：")
                for row in daily[:10]:
                    lines.append(f"Day {row.get('day', '未知')}：{row.get('total_min', '未知')} 分鐘（服務 {row.get('service_min', '未知')}、車程 {row.get('drive_min', '未知')}）。")
            if not weekly and not daily:
                lines.append("目前資料中找不到指定司機的工時紀錄。")
            lines.append("以上已分開標示單日與一週工時，未將兩者相加或混用。")
            return "\n".join(lines)
        if result.get("action") == "driver_imbalance":
            items = result.get("items") or []
            if not items:
                return "目前沒有足夠的每日工時資料計算司機工作量不均衡程度。"
            lines = ["司機工作量不均衡排名（依變異係數，再以每日工時範圍排序）："]
            for index, item in enumerate(items[:10], start=1):
                lines.append(
                    f"{index}. {item.get('driver')}：變異係數 {item.get('coefficient_of_variation')}，"
                    f"每日工時範圍 {item.get('daily_range_min')} 分鐘，"
                    f"最低 {item.get('min_daily_min')}、最高 {item.get('max_daily_min')} 分鐘。"
                )
            lines.append("以上排名由後端統計計算，不是依週總工時高低推測。")
            return "\n".join(lines)
        if result.get("action") == "carbon_reduction":
            if result.get("status") != "verified":
                current = result.get("current_high_carbon_routes") or []
                target_pct = result.get("target_reduction_pct")
                current_total = result.get("current_total_co2_kg")
                target_reduction = result.get("target_reduction_kg")
                lines = ["目前無法驗證哪一條路線的碳排減少最多。", result.get("reason") or "缺少可比較的基準資料。"]
                if target_pct is not None:
                    lines.append(f"目標：降低 {target_pct:g}%；目前 NORMAL 估算總碳排約 {current_total} kg CO2e，目標至少減少 {target_reduction} kg CO2e。")
                if current:
                    lines.append("目前只能列出現行碳排較高的路線，不能把它當成減量排名：")
                    for index, item in enumerate(current[:5], start=1):
                        lines.append(f"{index}. {item.get('route_id')}：目前約 {item.get('estimated_co2_kg')} kg CO2e")
                lines.extend([
                    "",
                    "可先比較的兩種分析方案（尚未完成路線 dry-run）：",
                    "方案一｜集中優化最高碳排路線：優先分析目前碳排最高的路線，嘗試縮短距離或重新排序。優點是資源集中、較容易追蹤；缺點是單一路線可能無法達到整體目標。",
                    "方案二｜多路線分散優化：同時檢查多條高里程路線，將可減少的距離分散累積。優點是較有機會達成總體目標；缺點是需要更多司機、場站、工時與點位驗證。",
                    "下一步需要建立重新分配 dry-run，通過 540 分鐘、場站、縣市、點位完整性與 OSRM 驗證後，才能比較兩方案的實際減碳量。",
                ])
                return "\n".join(lines)
            lines = ["已完成逐路線碳排減量計算："]
            for index, item in enumerate((result.get("items") or [])[:10], start=1):
                lines.append(f"{index}. {item.get('route_id')}：減少 {item.get('saved_co2_kg')} kg CO2e（基準 {item.get('baseline_co2_kg')} → 目前 {item.get('current_co2_kg')}）")
            return "\n".join(lines)
        if result.get("action") == "backup_candidates":
            items = result.get("items") or []
            lines = ["依目前工時資料，以下是備援司機候選："]
            if not items:
                lines.append("目前沒有符合每日工時上限的候選人。")
            for index, item in enumerate(items[:10], start=1):
                lines.append(
                    f"{index}. {item.get('driver')}：一週 {item.get('weekly_total_min', '未知')} 分鐘，"
                    f"平均每日 {item.get('average_daily_min', '未知')} 分鐘，最高單日 {item.get('max_daily_min', '未知')} 分鐘，"
                    f"每日餘裕 {item.get('remaining_to_daily_limit_min', '未知')} 分鐘。"
                )
            lines.append("\n注意：以上只是工時候選，不代表已確認可出勤或具備代班資格。仍需檢查請假、排班、車輛、場站與接手路線驗證。")
            return "\n".join(lines)
        if result.get("action") == "route_change_simulation":
            candidate = result.get("candidate_route") or {}
            route_name = candidate.get("司機") or candidate.get("driver") or "未知路線"
            return (
                f"{result.get('message', '已建立候選方案')}\n"
                f"優先分析路線：{route_name}，目前約 {result.get('current_total_min', '未知')} 分鐘。\n"
                f"目標減少：{result.get('target_reduction_min', '未知')} 分鐘。\n"
                f"{result.get('next_step', '')}"
            )
        if result.get("action") == "route_merge_analysis":
            candidates = result.get("candidates") or []
            if not candidates:
                if result.get("same_driver_only"):
                    return "目前找不到同一位司機不同日期、且合併後不超過每日工時上限的方案；系統沒有把點位移給其他司機。"
                return "目前沒有足夠資料找到可合併的路線。這不代表一定不能合併，還需要讀取完整路線站點並執行重新排序模擬。"
            lines = [
                "路線合併分析結果",
                f"目前找到 {len(candidates)} 組候選方案。",
                f"合併範圍：{result.get('merge_scope', '可跨司機合併')}。",
                "以下結果已使用增量排程 dry-run；尚未修改正式路線。",
                "",
            ]
            for index, candidate in enumerate(candidates[:5], start=1):
                source = candidate.get("source_route_id") or candidate.get("left_route_id") or "來源路線"
                target = candidate.get("target_route_id") or candidate.get("right_route_id") or "目標路線"
                total = candidate.get("target_total_min") or candidate.get("combined_total_min")
                buffer = candidate.get("remaining_buffer_min")
                if buffer is None and total is not None:
                    buffer = round(540 - float(total), 2)
                verification = "已用路線成本 dry-run" if candidate.get("verified_by_route_cost") else "僅工時預篩選"
                quality = candidate.get("quality") or "未評估"
                cross_county = "是" if candidate.get("cross_county") else "否"
                fallback = "是" if candidate.get("used_fallback") or candidate.get("geometry_fallback") else "否"
                lines.extend([
                    f"方案 {index}{'（目前推薦）' if candidate.get('recommended') else ''}",
                    f"來源路線：{source}（原工時 {candidate.get('source_before_total_min', '未知')} 分鐘）",
                    f"目標路線：{target}（原工時 {candidate.get('target_before_total_min', '未知')} 分鐘）",
                    f"來源司機：{candidate.get('source_driver', '未知')}；目標司機：{candidate.get('driver', '未知')}；來源日期：Day {candidate.get('source_day', '未知')}；目標日期：Day {candidate.get('day', '未知')}",
                    f"重新排序後目標工時：{total if total is not None else '未知'} 分鐘",
                    f"增加工時：{candidate.get('added_minutes', '未知')} 分鐘；剩餘緩衝：{buffer if buffer is not None else '未知'} 分鐘",
                    f"移動站點：{candidate.get('moved_stop_count', '未知')} 個；跨縣市：{cross_county}；路線成本 fallback：{fallback}",
                    f"評估：{quality}；驗證來源：{verification}",
                ])
                insertions = candidate.get("insertions") or []
                if insertions:
                    lines.append("站點移動明細：")
                    for insertion in insertions[:30]:
                        node_id = insertion.get("node_id") or "未知點位"
                        address = insertion.get("address") or ""
                        original_seq = insertion.get("original_seq") or "未知"
                        final_seq = insertion.get("final_target_seq") or insertion.get("position") or "未知"
                        suffix = f"｜{address}" if address else ""
                        lines.append(f"- {node_id}{suffix}：來源第 {original_seq} 站 → 目標合併後第 {final_seq} 站")
                lines.append("")
            lines.extend([
                "結論：以上是可進一步套用的候選，不代表已經修改正式路線。",
                "正式套用前仍需由使用者確認，系統才會寫入路線檔並留下復原備份。",
            ])
            return "\n".join(lines)
        # Preserve the existing deterministic Chinese fallback while the
        # server-side model is not configured or has exhausted its quota.
        try:
            from ..views import build_mock_ai_answer
            return build_mock_ai_answer(self.question, self.context)
        except Exception:
            return "已完成資料查詢，但目前沒有可用的模型回答。"

    def run(self) -> dict[str, Any]:
        if is_conversation_question(self.question, self.context.get("conversation_history")):
            history = self.context.get("conversation_history") or []
            prompt = (
                "你是 Dispatch Nav 的營運對話助理，請用自然、簡潔的繁體中文延續對話。\n"
                "這是一般對話或接續提問，不要假裝呼叫工具，也不要捏造新的系統數字。"
                "可以根據近期對話解釋、澄清、比較或引導使用者；若需要新的即時資料，請說明需要再查詢。\n"
                f"近期對話：{json.dumps(history, ensure_ascii=False)}\n"
                f"使用者：{self.question}"
            )
            mock = False
            try:
                answer = self.provider.complete(prompt)
            except Exception:
                answer = "可以，我會保留近期對話脈絡。你可以直接接著問；若問題需要新的系統資料，我會再進行查詢後回答。"
                mock = True
            plan = {"goal": self.question, "task_type": "conversation", "requires_confirmation": False,
                    "steps": [], "errors": []}
            return {
                "answer": answer, "action": "conversation",
                "tool_result": {"action": "conversation", "status": "answered_without_tools"},
                "plan": plan,
                "execution": {"success": True, "step_results": {}, "completed_steps": [],
                              "warnings": [], "errors": []},
                "execution_state": None, "mock": mock,
                "model": self.provider.model if self.provider.available and not mock else "local-fallback",
                "provider_error": self.provider.last_error if mock else "",
                "provider_notice": self.provider.last_notice,
                "requires_confirmation": False, "mutation_allowed": False,
                "question_type": "接續對話",
            }
        if not is_system_related_question(self.question, self.context.get("conversation_history")):
            return out_of_scope_response(self.question)
        state = new_execution_state(
            company_key=self._company_key(),
            user_id=self.user_id,
            question=self.question,
            question_type=self.context.get("question_type", "unknown"),
        )
        planner = TaskPlanner(provider=self.provider)
        plan = planner.plan(self.question, self.context)
        state["plan"] = plan
        state["normalized_goal"] = str(plan.get("goal") or self.question)
        state["task_type"] = str(plan.get("task_type") or state["task_type"])
        if plan.get("planner_errors"):
            state["warnings"].extend(plan.get("planner_errors") or [])
        from .state import save_state
        save_state(state)
        execution = PlanExecutor().execute(plan, self.context, state) if plan.get("steps") else {
            "success": False, "step_results": {}, "completed_steps": [],
            "warnings": [], "errors": plan.get("errors") or ["目前沒有可執行的工具計畫。"],
        }
        step_results = execution.get("step_results") or {}
        if len(step_results) == 1:
            result = next(iter(step_results.values())).get("data") or {}
            if not isinstance(result, dict):
                result = {"action": "analysis", "status": "ok", "data": result}
            else:
                tool_name = next(iter(plan.get("steps") or [])).get("tool")
                result.setdefault("action", {
                    "search_service_points": "service_point_search",
                    "search_routes_by_location": "route_search",
                    "analyze_area_coverage": "area_coverage",
                    "get_company_overview": "basic_info",
                    "get_data_availability": "analysis",
                    "get_driver_summary": "driver_workload",
                    "get_driver_workload": "driver_workload",
                    "get_driver_routes": "analysis",
                    "rank_driver_imbalance": "driver_imbalance",
                    "assess_backup_driver_candidates": "backup_candidates",
                    "get_route_details": "basic_info",
                    "get_unassigned_points": "unassigned_points",
                    "analyze_overtime_routes": "overtime_routes",
                    "get_carbon_summary": "carbon_summary",
                    "rank_carbon_routes": "carbon_summary",
                    "get_route_diagnostics": "route_merge_analysis",
                    "rank_routes": "analysis",
                    "calculate_route_carbon_reduction": "carbon_reduction",
                    "compare_route_variants": "analysis",
                    "validate_schedule": "analysis",
                    "validate_point_integrity": "analysis",
                    "simulate_route_change": "route_change_simulation",
                    "analyze_cleaning_records": "cleaning_summary",
                }.get(tool_name, "analysis"))
        else:
            result = {
                "action": "analysis_plan",
                "status": "proposal_only",
                "plan": plan,
                "steps": step_results,
                "errors": execution.get("errors") or [],
                "warnings": execution.get("warnings") or [],
            }
        if execution.get("errors") and not result.get("errors"):
            result["errors"] = execution.get("errors")
        answer, mock = self._answer(result)
        return {
            "answer": answer,
            "action": result.get("action") or "analysis_plan",
            "tool_result": result,
            "plan": plan,
            "execution": execution,
            "execution_state": state,
            "mock": mock,
            "model": self.provider.model if self.provider.available and not mock else "local-fallback",
            "provider_error": self.provider.last_error if mock else "",
            "provider_notice": self.provider.last_notice,
            "requires_confirmation": bool(plan.get("requires_confirmation") or result.get("requires_confirmation")),
            "mutation_allowed": False,
        }

SYSTEM_TOPIC_KEYWORDS = (
    "路線", "點位", "站點", "清掃", "清潔", "司機", "派工", "工時", "排程",
    "倉庫", "場站", "總部", "OSRM", "里程", "距離", "碳排", "減碳", "ESG",
    "廁所", "跳過", "補排", "重排", "超時", "跨縣市", "行政區", "縣市",
    "一般模式", "精簡模式", "流動廁所", "服務時間", "工作量", "營運",
)
FOLLOW_UP_WORDS = (
    "其中", "上述", "剛才", "前面", "這些", "那些", "它們", "再比較", "前三", "後三",
    "上一題", "前一題", "那如果", "所以", "為什麼", "更詳細", "更簡單", "再說明", "意思是",
)
ASSISTANT_META_WORDS = (
    "接續", "繼續回答", "問過", "對話", "聊天", "你能", "你可以", "你會", "你的回答", "記得前面",
)


def is_conversation_question(question: str, conversation_history=None) -> bool:
    text = str(question or "").strip()
    if any(word in text for word in ASSISTANT_META_WORDS):
        return True
    if any(word in text for word in FOLLOW_UP_WORDS):
        previous = " ".join(str(item.get("question") or "") for item in (conversation_history or [])[-3:])
        return any(keyword.lower() in previous.lower() for keyword in SYSTEM_TOPIC_KEYWORDS)
    return False


def is_system_related_question(question: str, conversation_history=None) -> bool:
    """Conservatively gate the assistant to Dispatch Nav operational topics."""
    text = str(question or "").strip()
    if any(keyword.lower() in text.lower() for keyword in SYSTEM_TOPIC_KEYWORDS):
        return True
    if any(word in text for word in FOLLOW_UP_WORDS):
        previous = " ".join(str(item.get("question") or "") for item in (conversation_history or [])[-3:])
        return any(keyword.lower() in previous.lower() for keyword in SYSTEM_TOPIC_KEYWORDS)
    return False


def out_of_scope_response(question: str) -> dict[str, Any]:
    answer = (
        "這個問題與 Dispatch Nav 的路線、清掃或營運管理較無直接關聯，"
        "因此我不會拿系統資料勉強推測或提供不可靠的回答。\n\n"
        "你可以改問例如：\n"
        "• 哪些司機的每日工時分布最不平均？\n"
        "• 哪些低工時路線有安全的同倉合併方向？\n"
        "• 哪些區域的清掃點位最集中，是否需要調整派工？"
    )
    plan = {"goal": question, "task_type": "out_of_scope", "requires_confirmation": False,
            "steps": [], "errors": []}
    return {
        "answer": answer, "action": "out_of_scope",
        "tool_result": {"action": "out_of_scope", "status": "not_applicable"},
        "plan": plan,
        "execution": {"success": True, "step_results": {}, "completed_steps": [],
                      "warnings": [], "errors": []},
        "execution_state": None, "mock": False, "model": "system-scope-guard",
        "requires_confirmation": False, "mutation_allowed": False,
        "question_type": "與系統無關",
    }
