"""Spend: attach API cost to the ledger; veto turns / block tools over budget."""

from __future__ import annotations

import logging
from typing import Any

from . import config, ledger

logger = logging.getLogger("excubitor.spend")


def _tokens_from_usage(usage: Any) -> tuple[int, int]:
    if not isinstance(usage, dict):
        return 0, 0
    prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or usage.get("prompt") or 0
    completion = (
        usage.get("completion_tokens") or usage.get("output_tokens") or usage.get("completion") or 0
    )
    try:
        return int(prompt or 0), int(completion or 0)
    except Exception:
        return 0, 0


def _cost_from_hook(
    *,
    usage: Any,
    model: str | None,
    cfg: dict[str, Any],
    explicit: Any = None,
) -> float:
    # Prefer an explicit dollar figure if a future / richer hook provides one.
    for key in ("estimated_cost_usd", "cost_usd", "total_cost"):
        if isinstance(usage, dict) and usage.get(key) is not None:
            try:
                return max(0.0, float(usage[key]))
            except Exception:
                pass
    if explicit is not None:
        try:
            return max(0.0, float(explicit))
        except Exception:
            pass
    prompt, completion = _tokens_from_usage(usage)
    return config.estimate_cost_usd(
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        overrides=cfg.get("price_table") or {},
    )


def over_cap(cfg: dict[str, Any], *, session_id: str = "") -> str | None:
    day = ledger.utc_day()
    day_spend = ledger.spend_for("day", day, day)
    day_cap = float(cfg.get("day_budget_usd") or 5.0)
    if day_spend >= day_cap:
        return f"excubitor: day budget exhausted (${day_spend:.4f} / ${day_cap:.2f})"
    if session_id:
        sess_spend = ledger.spend_for("session", session_id, day)
        sess_cap = float(cfg.get("session_budget_usd") or 2.0)
        if sess_spend >= sess_cap:
            return f"excubitor: session budget exhausted (${sess_spend:.4f} / ${sess_cap:.2f})"
    return None


def on_post_api_request(
    session_id="",
    turn_id="",
    task_id="",
    model=None,
    usage=None,
    **kwargs,
):
    try:
        cfg = config.settings()
        if not cfg.get("enabled", True):
            return None
        tid = ledger.resolve_turn_id(
            session_id=session_id or "", turn_id=turn_id or "", task_id=task_id or ""
        )
        if not ledger.get_turn(tid):
            ledger.open_turn(
                tid,
                session_id=session_id or "",
                deadline_minutes=cfg["reply_deadline_minutes"],
                meta={"opened_by": "post_api_request"},
            )
        amount = _cost_from_hook(usage=usage, model=model, cfg=cfg)
        if amount > 0:
            ledger.add_cost(tid, amount, session_id=session_id or "")
        return None
    except Exception as exc:
        logger.debug("excubitor: post_api_request failed open: %s", exc)
        return None


def on_post_llm_call(session_id="", turn_id="", task_id="", **kwargs):
    """Observer: mark the turn as replied so the hang watcher stands down."""
    try:
        cfg = config.settings()
        if not cfg.get("enabled", True):
            return None
        tid = ledger.resolve_turn_id(
            session_id=session_id or "", turn_id=turn_id or "", task_id=task_id or ""
        )
        if ledger.get_turn(tid):
            ledger.mark_reply(tid, status="replied")
        return None
    except Exception as exc:
        logger.debug("excubitor: post_llm_call failed open: %s", exc)
        return None


def on_pre_llm_call(
    session_id="", turn_id="", task_id="", user_message=None, platform="", **kwargs
):
    """Open a ledger entry if needed (CLI path) and veto the turn when over budget."""
    try:
        cfg = config.settings()
        if not cfg.get("enabled", True):
            return None
        tid = ledger.resolve_turn_id(
            session_id=session_id or "", turn_id=turn_id or "", task_id=task_id or ""
        )
        turn = ledger.get_turn(tid)
        if not turn:
            ledger.open_turn(
                tid,
                session_id=session_id or "",
                platform=str(platform or ""),
                deadline_minutes=cfg["reply_deadline_minutes"],
                meta={"opened_by": "pre_llm_call", "user_preview": str(user_message or "")[:120]},
            )
        else:
            # Bind session_id onto an intake-opened turn that didn't have it yet.
            if session_id and not turn.get("session_id"):
                try:
                    with ledger._tx() as conn:  # noqa: SLF001 — intentional soft bind
                        conn.execute(
                            "UPDATE turns SET session_id = ? WHERE turn_id = ?",
                            (session_id, tid),
                        )
                except Exception:
                    pass
        reason = over_cap(cfg, session_id=session_id or "")
        if reason:
            ledger.bump_stat("cap_vetoes")
            ledger.set_turn_reason(tid, reason)
            return {"action": "block", "message": reason}
        return None
    except Exception as exc:
        logger.debug("excubitor: pre_llm_call failed open: %s", exc)
        return None


def on_pre_tool_call_spend(
    tool_name=None, args=None, task_id="", session_id="", turn_id="", **kwargs
):
    """Mid-turn spend veto (separate from loop detection)."""
    try:
        cfg = config.settings()
        if not cfg.get("enabled", True):
            return None
        reason = over_cap(cfg, session_id=session_id or "")
        if not reason:
            return None
        tid = ledger.resolve_turn_id(
            session_id=session_id or "", turn_id=turn_id or "", task_id=task_id or ""
        )
        ledger.bump_stat("cap_vetoes")
        ledger.set_turn_reason(tid, reason)
        return {"action": "block", "message": reason + f" (blocked tool {tool_name})"}
    except Exception as exc:
        logger.debug("excubitor: pre_tool_call spend failed open: %s", exc)
        return None
