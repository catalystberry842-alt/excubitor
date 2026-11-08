"""Turn receipt: a short plain-English story of one turn, built from the ledger.

Example:
  Turn t_ab12 · telegram · 4 tool calls · looped 6× on web_search, blocked at #6 ·
  spent $0.42 of $5 day cap · went quiet 11:02, alerted 11:04

Receipts are stored in the ledger (``receipts`` table). Only *notable* turns
(dedup, loop block, cap veto, hang) are sent through the notifier; clean turns
stay quiet. Every entry point is fail-open.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from . import config, ledger
from .notify import send_alert

logger = logging.getLogger("excubitor.receipt")


def _hm(ts: Any) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    except Exception:
        return "?"


def _money(x: float) -> str:
    return f"${x:.2f}"


def _cap(x: float) -> str:
    return f"${x:g}" if float(x).is_integer() else f"${x:.2f}"


def short_id(turn_id: str) -> str:
    tid = str(turn_id or "?")
    return tid if len(tid) <= 24 else tid[:12] + "…" + tid[-8:]


def build(turn_id: str, cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Compute the receipt for a turn. Returns {text, notable, data} or None."""
    turn = ledger.get_turn(turn_id)
    if not turn:
        return None
    cfg = cfg or config.settings()
    events = ledger.turn_events(turn_id)
    calls = ledger.all_tool_calls(turn_id)
    day_cap = float(config.budget(cfg, "day_budget_usd", 5.0))
    cost = float(turn.get("cost_usd") or 0.0)

    dedups = [e for e in events if e["kind"] == "dedup"]
    blocks = [e for e in events if e["kind"] == "loop_block"]
    approves = [e for e in events if e["kind"] == "loop_approve"]
    vetoes = [e for e in events if e["kind"] == "veto"]
    status = str(turn.get("status") or "open")
    hung = status == "alerted" or int(turn.get("alerts_fired") or 0) > 0

    parts = [f"Turn {short_id(turn_id)}"]
    if turn.get("platform"):
        parts.append(str(turn["platform"]))
    n = len(calls)
    parts.append(f"{n} tool call{'' if n == 1 else 's'}")
    if dedups:
        parts.append(f"dropped {len(dedups)} duplicate{'' if len(dedups) == 1 else 's'}")
    loop_events = blocks or approves
    if loop_events:
        first = loop_events[0]
        tool = first.get("tool") or "?"
        at = int(first.get("call_no") or first.get("stagnant") or 0)
        bit = f"looped {at}× on {tool}"
        if blocks:
            b = blocks[0]
            bit += f", blocked at #{int(b.get('call_no') or b.get('stagnant') or at)}"
            if len(blocks) > 1:
                bit += f" (+{len(blocks) - 1} more block{'s' if len(blocks) > 2 else ''})"
        else:
            bit += ", asked for approval"
        parts.append(bit)
    parts.append(f"spent {_money(cost)} of {_cap(day_cap)} day cap")
    if vetoes:
        v = vetoes[0]
        what = f"tool {v['tool']}" if v.get("tool") else "turn"
        parts.append(f"cap veto on {what} at {_hm(v['ts'])}")
    if hung:
        last_activity = max(
            [float(turn.get("opened_at") or 0)] + [float(c.get("ts") or 0) for c in calls]
        )
        alert_ts = next((e["ts"] for e in events if e["kind"] == "hang"), None)
        if alert_ts is None:
            alert_ts = turn.get("deadline_at")
        idle = max(0.0, float(alert_ts or 0) - last_activity)
        parts.append(
            f"went quiet {_hm(last_activity)}, alerted {_hm(alert_ts)} ({idle / 60:.0f} min idle)"
        )
    elif status == "stopped":
        parts.append(f"stopped {_hm(turn.get('reply_at'))}")
    elif turn.get("reply_at"):
        dur = float(turn["reply_at"]) - float(turn.get("opened_at") or turn["reply_at"])
        parts.append(f"replied {_hm(turn['reply_at'])} ({dur:.0f}s)")

    notable = bool(dedups or blocks or vetoes or hung)
    data = {
        "turn_id": turn_id,
        "platform": turn.get("platform") or "",
        "status": status,
        "tool_calls": n,
        "duplicates_dropped": len(dedups),
        "loop_blocks": [
            {"tool": b.get("tool"), "at": b.get("call_no") or b.get("stagnant")} for b in blocks
        ],
        "loop_approvals": len(approves),
        "vetoes": len(vetoes),
        "hung": hung,
        "cost_usd": round(cost, 6),
        "day_budget_usd": day_cap,
        "opened_at": turn.get("opened_at"),
        "reply_at": turn.get("reply_at"),
    }
    return {"text": " · ".join(parts), "notable": notable, "data": data}


def finalize(turn_id: str, cfg: dict[str, Any] | None = None, *, notify: bool = True) -> str | None:
    """Build + store a receipt; notify only for notable turns. Never raises."""
    try:
        cfg = cfg or config.settings()
        if not cfg.get("enabled", True) or not cfg.get("receipts_enabled", True):
            return None
        r = build(turn_id, cfg)
        if not r:
            return None
        ledger.save_receipt(turn_id, r["text"], r["data"], notable=r["notable"])
        if not notify and r["notable"]:
            # Hang watcher already notified via its alert; replays must stay quiet.
            ledger.mark_receipt_notified(turn_id)
        if notify and r["notable"] and ledger.mark_receipt_notified(turn_id):
            send_alert(
                cfg,
                title="Excubitor: turn receipt",
                body=r["text"],
                payload={
                    "plugin": "excubitor",
                    "kind": "receipt",
                    **r["data"],
                    "receipt": r["text"],
                },
            )
        return r["text"]
    except Exception as exc:
        logger.debug("excubitor: receipt for %s failed open: %s", turn_id, exc)
        return None
