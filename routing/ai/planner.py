"""Validated, bounded task planning for the route-system assistant."""

from __future__ import annotations

import json
import re
from typing import Any

from .tool_registry import TOOL_REGISTRY, get_tool, list_tools


MAX_STEPS = 8


def _task_type(question_type: str, question: str) -> str:
    if question_type in {"carbon", "cleaning"}:
        return "analysis"
    if any(word in str(question) for word in ("模擬", "重新分配", "調整", "降低", "改善")):
        return "analysis_and_simulation"
    return "query"


def _legacy_tool(question_type: str, question: str) -> str | None:
    from .orchestrator import choose_action

    action = choose_action(question, question_type)
    return {
        "basic_info": "get_company_overview",
        "unassigned_points": "get_unassigned_points",
        "overtime_routes": "analyze_overtime_routes",
        "driver_workload": "get_driver_summary",
        "carbon_summary": "get_carbon_summary",
        "cleaning_summary": "analyze_cleaning_records",
        "route_change_simulation": "get_route_diagnostics",
        "route_merge_analysis": "get_route_diagnostics",
    }.get(action)


def validate_plan(plan: Any) -> tuple[bool, list[str]]:
    errors = []
    if not isinstance(plan, dict):
        return False, ["plan 必須是 object"]
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("plan.steps 必須是非空陣列")
        return False, errors
    if len(steps) > MAX_STEPS:
        errors.append(f"步驟數不可超過 {MAX_STEPS}")
    ids = set()
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"step[{index}] 必須是 object")
            continue
        step_id = str(step.get("step_id") or "")
        tool = str(step.get("tool") or "")
        if not step_id or step_id in ids:
            errors.append(f"step[{index}] step_id 缺少或重複")
        ids.add(step_id)
        definition = get_tool(tool)
        if not definition:
            errors.append(f"未註冊工具：{tool}")
        elif definition.category not in {"READ", "ANALYZE", "VALIDATE"}:
            errors.append(f"AI 助理只允許查詢與分析工具：{tool}")
        if not isinstance(step.get("arguments", {}), dict):
            errors.append(f"{step_id}.arguments 必須是 object")
        dependencies = step.get("depends_on", [])
        if not isinstance(dependencies, list):
            errors.append(f"{step_id}.depends_on 必須是陣列")
        for dependency in dependencies or []:
            if dependency == step_id or dependency not in ids:
                errors.append(f"{step_id} 依賴不存在或形成不合法順序：{dependency}")
    if len(ids) != len(steps):
        errors.append("step_id 必須唯一")
    return not errors, errors


class TaskPlanner:
    def __init__(self, provider=None):
        self.provider = provider

    def _fallback(self, question: str, question_type: str) -> dict[str, Any]:
        data = self._context_data if hasattr(self, "_context_data") else {}
        constraints = data.get("query_constraints") or {}
        text = str(question or "")
        steps = []
        normalized_text = text.replace("台", "臺")
        taiwan_counties = ("臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市", "基隆市", "新竹市",
                           "嘉義市", "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣", "屏東縣",
                           "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣")
        county = next((name for name in taiwan_counties if name in normalized_text), "")
        county_end = normalized_text.find(county) + len(county) if county else 0
        district_source = normalized_text[county_end:] if county else normalized_text
        district_match = re.search(r"^([\u4e00-\u9fff]{1,4}(?:區|鄉|鎮|市))", district_source)
        district = district_match.group(1) if district_match else ""
        if any(word in normalized_text for word in ("區域", "地區", "涵蓋", "分布", "分佈")) and (county or district):
            return {
                "goal": question, "task_type": "analysis", "requires_confirmation": False,
                "steps": [{"step_id": "area_coverage", "tool": "analyze_area_coverage",
                           "arguments": {key: value for key, value in {"county": county, "district": district,
                                                                         "variant": "normal", "limit": 50}.items() if value},
                           "depends_on": []}],
            }
        if any(word in normalized_text for word in ("點位", "地址", "客戶", "廁所")) and (county or district):
            return {
                "goal": question, "task_type": "query", "requires_confirmation": False,
                "steps": [{"step_id": "point_search", "tool": "search_service_points",
                           "arguments": {key: value for key, value in {"county": county, "district": district,
                                                                         "limit": constraints.get("limit", 50)}.items() if value},
                           "depends_on": []}],
            }
        carbon_reduction_request = constraints.get("metric") == "carbon" and any(word in text for word in ("減少", "降低", "減量"))
        if constraints.get("metric") == "carbon" and not carbon_reduction_request and any(word in text for word in ("最高", "最多", "排名", "前")):
            steps.append({"step_id": "rank_carbon", "tool": "rank_carbon_routes", "arguments": {"limit": constraints.get("limit", 10)}, "depends_on": []})
        if constraints.get("metric") == "carbon" and any(word in text for word in ("減少", "降低", "減量")):
            steps.append({"step_id": "carbon_reduction", "tool": "calculate_route_carbon_reduction", "arguments": {"limit": constraints.get("limit", 10)}, "depends_on": []})
        if constraints.get("metric") == "work_time" and any(word in text for word in ("不平均", "不均衡", "差異最大", "分布")):
            steps.append({"step_id": "driver_imbalance", "tool": "rank_driver_imbalance", "arguments": {"limit": constraints.get("limit", 10)}, "depends_on": []})
        if "模式" in text and any(word in text for word in ("比較", "比較一下", "哪一種", "哪種")):
            steps.append({"step_id": "compare_variants", "tool": "compare_route_variants", "arguments": {}, "depends_on": []})
        if len(steps) > 1:
            return {
                "goal": question,
                "task_type": "analysis",
                "requires_confirmation": False,
                "steps": steps,
            }
        if constraints.get("driver_ids") and constraints.get("metric") == "work_time":
            tool = "get_driver_workload"
        elif constraints.get("metric") == "carbon" and any(word in text for word in ("減少", "降低", "減量")):
            tool = "calculate_route_carbon_reduction"
        elif constraints.get("metric") == "carbon" and any(word in text for word in ("最高", "最多", "排名", "前")):
            tool = "rank_carbon_routes"
        elif constraints.get("metric") == "work_time" and any(word in text for word in ("不平均", "不均衡", "差異最大", "分布")):
            tool = "rank_driver_imbalance"
        elif constraints.get("metric") == "work_time" and any(word in text for word in ("儲備", "備用", "替補", "備援")):
            tool = "assess_backup_driver_candidates"
        elif "模式" in text and any(word in text for word in ("比較", "比較一下", "哪一種", "哪種")):
            tool = "compare_route_variants"
        elif "驗證" in text and "點位" in text:
            tool = "validate_point_integrity"
        elif "驗證" in text and any(word in text for word in ("工時", "超時", "排程")):
            tool = "validate_schedule"
        else:
            tool = _legacy_tool(question_type, question)
        if not tool:
            return {"goal": question, "task_type": "unsupported", "steps": [], "errors": ["找不到可用的 Domain Tool"]}
        arguments = {}
        if tool == "get_driver_workload" and constraints.get("driver_ids"):
            arguments = {"driver_id": constraints["driver_ids"][0]}
            if constraints.get("scope") != "unspecified":
                arguments["scope"] = constraints.get("scope")
        elif tool in {"rank_carbon_routes", "rank_routes"}:
            arguments = {"limit": constraints.get("limit", 10)}
        return {
            "goal": question,
            "task_type": _task_type(question_type, question),
            "requires_confirmation": False,
            "steps": [{"step_id": "step_1", "tool": tool, "arguments": arguments, "depends_on": []}],
        }

    def plan(self, question: str, context: dict[str, Any]) -> dict[str, Any]:
        question_type = context.get("question_type", "dispatch")
        self._context_data = context.get("data") or {}
        fallback = self._fallback(question, question_type)
        if not self.provider or not getattr(self.provider, "available", False):
            return fallback
        prompt = (
            "你是 route_system 任務規劃器，只回傳 JSON，不要回答問題。\n"
            "只能使用以下已註冊工具，不能發明工具；只能規劃 READ、ANALYZE、SIMULATE。\n"
            f"工具：{json.dumps(list_tools(), ensure_ascii=False)}\n"
            f"問題類型：{question_type}\n查詢條件：{json.dumps(self._context_data.get('query_constraints') or {}, ensure_ascii=False)}\n使用者目標：{question}\n"
            "格式：{\"goal\":\"...\",\"task_type\":\"query|analysis|analysis_and_simulation\",\"requires_confirmation\":false,\"steps\":[{\"step_id\":\"step_1\",\"tool\":\"...\",\"arguments\":{},\"depends_on\":[]}]}"
        )
        prompt = (
            "你是 Dispatch Nav 的唯讀資料規劃器。理解使用者真正目的，選擇一至多個工具，且只輸出 JSON。\n"
            "規則：詢問縣市、行政區、地址或清理區域時，使用地區搜尋工具；只有明確要求移動或合併路線才使用 simulate_route_change。"
            "需要比較或決策時可以安排多個步驟。不得規劃任何未確認的寫入操作。\n"
            f"可用工具：{json.dumps(list_tools(), ensure_ascii=False)}\n"
            f"問題類型：{question_type}\n"
            f"已解析條件：{json.dumps(self._context_data.get('query_constraints') or {}, ensure_ascii=False)}\n"
            f"近期對話：{json.dumps(context.get('conversation_history') or [], ensure_ascii=False)}\n"
            f"使用者問題：{question}\n"
            "輸出格式：{\"goal\":\"...\",\"task_type\":\"query|analysis|analysis_and_simulation\","
            "\"requires_confirmation\":false,\"steps\":[{\"step_id\":\"step_1\",\"tool\":\"...\","
            "\"arguments\":{},\"depends_on\":[]}]}"
        )
        try:
            raw = self.provider.complete(prompt, timeout=20)
            raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.IGNORECASE).strip()
            candidate = json.loads(raw)
            valid, errors = validate_plan(candidate)
            if valid:
                candidate["requires_confirmation"] = False
                return candidate
            fallback["planner_errors"] = errors
        except Exception as exc:
            fallback["planner_errors"] = [f"Planner 回應無法解析：{type(exc).__name__}: {exc}"]
        return fallback
