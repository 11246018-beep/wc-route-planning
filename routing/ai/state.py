"""Short-lived, company-scoped execution state for the assistant."""

from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime, timedelta, timezone


STATE_TTL_SECONDS = 15 * 60
_STATES: dict[str, dict] = {}
_LOCK = threading.Lock()


def new_execution_state(*, company_key: str, user_id: str, question: str, question_type: str) -> dict:
    now = datetime.now(timezone.utc)
    state = {
        "request_id": uuid.uuid4().hex,
        "company_key": str(company_key or "").strip(),
        "user_id": str(user_id or "").strip(),
        "original_question": str(question or ""),
        "normalized_goal": "",
        "task_type": question_type or "unknown",
        "plan": {},
        "current_step": None,
        "completed_steps": [],
        "step_results": {},
        "candidate_scenarios": [],
        "validation_results": [],
        "warnings": [],
        "errors": [],
        "requires_confirmation": False,
        "confirmation_status": "not_required",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=STATE_TTL_SECONDS)).isoformat(),
    }
    save_state(state)
    return state


def _expired(state: dict) -> bool:
    try:
        expires = datetime.fromisoformat(str(state.get("expires_at")))
        return datetime.now(timezone.utc) >= expires
    except (TypeError, ValueError):
        return True


def save_state(state: dict) -> None:
    request_id = str(state.get("request_id") or "").strip()
    if not request_id:
        raise ValueError("execution state 缺少 request_id")
    with _LOCK:
        _STATES[request_id] = copy.deepcopy(state)
        for key in list(_STATES):
            if _expired(_STATES[key]):
                _STATES.pop(key, None)


def get_state(request_id: str, *, company_key: str, user_id: str) -> dict | None:
    with _LOCK:
        state = copy.deepcopy(_STATES.get(str(request_id or "")))
    if not state or _expired(state):
        return None
    if state.get("company_key") != str(company_key or "").strip():
        return None
    if state.get("user_id") != str(user_id or "").strip():
        return None
    return state

