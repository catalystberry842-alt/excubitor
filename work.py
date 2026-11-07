"""Work: no-progress tool-loop detection on the shared ledger."""

from __future__ import annotations

import logging
from typing import Any

from . import config, ledger
from .hashing import args_hash, result_hash

logger = logging.getLogger("excubitor.work")


def effective_loop_threshold(base: int, spend_ratio: float) -> int:
    """Tighten threshold as budget is consumed. At full spend, floor is 2."""
    base = max(2, int(base))
    ratio = min(1.0, max(0.0, float(spend_ratio)))
    # Drop up to (base - 2) steps proportional to spend.
    drop = int(round((base - 2) * ratio))
    return max(2, base - drop)


def no_progress_count(history: list[dict[str, Any]]) -> int:
    """How many times this (tool, args) has been called without a new result hash.

    Counts completed calls whose result_hash is a repeat of an earlier result for
    the same signature, plus pending re-calls after at least one completed result.
    """
    if not history:
        return 0
    seen_results: set[str] = set()
    stagnant = 0
    for row in history:
        rh = row.get("result_hash")
        if rh is None:
            # A new pre-call while we already have results for this signature = spinning.
            if seen_results:
                stagnant += 1
            continue
        if rh in seen_results:
            stagnant += 1
        else:
            seen_results.add(str(rh))
    # Also treat pure identical-args spam with no results yet as progress-free once
    # the call count itself exceeds a soft floor (model hammering before first return).
    if not seen_results and len(history) >= 2:
        stagnant = max(stagnant, len(history) - 1)
    return stagnant


def spend_ratio_for_turn(turn: dict[str, Any], cfg: dict[str, Any], session_id: str = "") -> float:
    day = ledger.utc_day()
    day_spend = ledger.spend_for("day", day, day)
    sess_spend = (
        ledger.spend_for("session", session_id or turn.get("session_id") or "", day)
        if (session_id or turn.get("session_id"))
        else float(turn.get("cost_usd") or 0.0)
    )
    day_cap = max(1e-9, float(cfg.get("day_budget_usd") or 5.0))
    sess_cap = max(1e-9, float(cfg.get("session_budget_usd") or 2.0))
    return max(
        day_spend / day_cap, sess_spend / sess_cap, float(turn.get("cost_usd") or 0.0) / sess_cap
    )


def evaluate_loop(
    turn_id: str,
    tool_name: str,
    args: Any,
    *,
    session_id: str = "",
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return a pre_tool_call directive when a no-progress loop is detected, else None."""
    cfg = cfg or config.settings()
    turn = ledger.get_turn(turn_id) or {}
    ah = args_hash(args if isinstance(args, dict) else {})
    # Record this pre-call first so history includes it.
    ledger.record_tool_pre(turn_id, tool_name, ah)
    history = ledger.tool_history(turn_id, tool_name, ah)
    stagnant = no_progress_count(history)
    ratio = spend_ratio_for_turn(turn, cfg, session_id=session_id)
    threshold = effective_loop_threshold(int(cfg.get("loop_threshold") or 6), ratio)
    # Burning money + looping escalates faster: treat half-threshold as trip when ratio high.
    burning = ratio >= 0.5 and float(turn.get("cost_usd") or 0.0) > 0.0
    trip = threshold
    if burning:
        trip = max(2, threshold // 2)
    if stagnant < trip:
        return None
    count = ledger.bump_loop(turn_id, blocked=True)
    ledger.bump_stat("loops_stopped")
    reason = (
        f"excubitor: no-progress loop on {tool_name} "
        f"(stagnant={stagnant}, threshold={trip}, spend_ratio={ratio:.2f}, turn_cost=${float(turn.get('cost_usd') or 0):.4f})"
    )
    ledger.set_turn_reason(turn_id, reason)
    # First detection may ask the human; later or burning always blocks.
    approve_first = bool(cfg.get("loop_approve_first", True))
    if approve_first and count <= 1 and not burning:
        ledger.add_turn_event(turn_id, "loop_approve", tool=tool_name, stagnant=stagnant, call_no=len(history))
        return {
            "action": "approve",
            "message": reason + " — confirm to allow one more attempt.",
            "rule_key": f"excubitor:loop:{tool_name}",
        }
    ledger.add_turn_event(turn_id, "loop_block", tool=tool_name, stagnant=stagnant, call_no=len(history))
    return {"action": "block", "message": reason}


def on_post_tool_call(
    tool_name=None, args=None, result=None, task_id="", session_id="", turn_id="", **kwargs
):
    try:
        cfg = config.settings()
        if not cfg.get("enabled", True):
            return None
        tid = ledger.resolve_turn_id(
            session_id=session_id or "", turn_id=turn_id or "", task_id=task_id or ""
        )
        if not ledger.get_turn(tid):
            # Ensure a turn exists so result hashes still land for CLI paths.
            ledger.open_turn(
                tid,
                session_id=session_id or "",
                deadline_minutes=cfg["reply_deadline_minutes"],
                meta={"opened_by": "post_tool_call"},
            )
        ah = args_hash(args if isinstance(args, dict) else {})
        rh = result_hash(result)
        ledger.record_tool_post(tid, str(tool_name or ""), ah, rh)
        return None
    except Exception as exc:
        logger.debug("excubitor: post_tool_call failed open: %s", exc)
        return None
