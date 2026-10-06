"""Bounded executor for validated READ/ANALYZE/SIMULATE plans."""

from __future__ import annotations

import copy
import time
from typing import Any

from .planner import validate_plan
from .state import save_state
from .tool_registry import get_tool, validate_arguments, READ, ANALYZE, VALIDATE


MAX_TOOL_CALLS = 12
MAX_EXECUTION_SECONDS = 45


def _resolve(value, results):
    if isinstance(value, dict) and set(value) == {"$ref"}:
        ref = str(value["$ref"])
        step_id, _, path = ref.partition(".")
        current = results.get(step_id)
        for key in path.split(".") if path else []:
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return None
        return copy.deepcopy(current)
    if isinstance(value, dict):
        return {key: _resolve(item, results) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, results) for item in value]
    return value


class PlanExecutor:
    def execute(self, plan: dict[str, Any], context: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        valid, errors = validate_plan(plan)
        if not str(state.get("company_key") or "").strip():
            errors.append("execution state 缺少 company_key，拒絕執行工具。")
            valid = False
        if not valid:
            state["errors"].extend(errors)
            save_state(state)
            return {"success": False, "errors": errors, "step_results": {}}
        started = time.monotonic()
        results: dict[str, dict] = {}
        completed = []
        steps = plan.get("steps") or []
        context = {**context, "original_question": state.get("original_question"), "execution_state": state,
                   "company_key": state.get("company_key")}
        for step in steps:
            if time.monotonic() - started > MAX_EXECUTION_SECONDS:
                error = "已達到 Agent 整體執行時間上限。"
                state["errors"].append(error)
                break
            step_id = step["step_id"]
            dependencies = step.get("depends_on") or []
            if any(dependency not in results for dependency in dependencies):
                state["errors"].append(f"{step_id} 的相依步驟尚未成功完成。")
                break
            definition = get_tool(step["tool"])
            if not definition:
                state["errors"].append(f"未註冊工具：{step['tool']}")
                break
            if definition.category not in {READ, ANALYZE, VALIDATE}:
                state["errors"].append(f"目前 Executor 禁止執行 {definition.category} 工具：{definition.name}")
                break
            state["current_step"] = step_id
            save_state(state)
            arguments = _resolve(step.get("arguments") or {}, results)
            argument_errors = validate_arguments(definition, arguments)
            if argument_errors:
                state["errors"].extend(argument_errors)
                break
            try:
                output = definition.execute(context, arguments, results)
            except Exception as exc:
                output = {"success": False, "tool": definition.name, "summary": "工具執行失敗。",
                          "data": {}, "warnings": [], "errors": [f"{type(exc).__name__}: {exc}"],
                          "source": {"type": "server", "name": definition.name, "updated_at": None},
                          "metrics": {}, "next_step_hints": []}
            if not isinstance(output, dict) or not all(key in output for key in ("success", "tool", "summary", "data", "warnings", "errors", "source", "metrics", "next_step_hints")):
                state["errors"].append(f"{step_id} 工具輸出不符合統一格式。")
                break
            results[step_id] = output
            state["step_results"][step_id] = output
            if output.get("warnings"):
                state["warnings"].extend(output["warnings"])
            if not output.get("success"):
                state["errors"].extend(output.get("errors") or [f"{step_id} 執行失敗"])
                break
            completed.append(step_id)
            state["completed_steps"] = completed[:]
            save_state(state)
        state["current_step"] = None
        save_state(state)
        return {"success": not state["errors"], "step_results": results,
                "completed_steps": completed, "warnings": state["warnings"], "errors": state["errors"]}
