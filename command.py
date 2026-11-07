"""Slash command /excubitor (alias /watch) — ledger status summary."""

from __future__ import annotations

import logging
from typing import Any

from . import config, ledger

logger = logging.getLogger("excubitor.command")


def receipt_text(turn_id: str = "") -> str:
    try:
        r = ledger.get_receipt(turn_id.strip())
        if not r:
            return (
                f"Excubitor: no receipt for turn {turn_id.strip()}"
                if turn_id.strip()
                else "Excubitor: no turn receipts yet"
            )
        return f"Excubitor receipt\n  {r['text']}"
    except Exception as exc:
        logger.debug("excubitor: receipt command failed: %s", exc)
        return "Excubitor receipt unavailable (fail-open)."


def status_text(raw_args: str = "") -> str:
    parts = (raw_args or "").strip().split(None, 1)
    if parts and parts[0].lower() in ("receipt", "receipts", "r"):
        return receipt_text(parts[1] if len(parts) > 1 else "")
    try:
        cfg = config.settings()
        summary = ledger.summarize()
        day_cap = float(cfg.get("day_budget_usd") or 5.0)
        sess_cap = float(cfg.get("session_budget_usd") or 2.0)
        lines = [
            "Excubitor status",
            f"  enabled: {cfg.get('enabled', True)}",
            f"  day: {summary.get('day')}  spend ${summary.get('day_spend_usd', 0):.4f} / ${day_cap:.2f}",
            f"  session cap: ${sess_cap:.2f}",
            f"  duplicates dropped: {summary.get('duplicates_dropped', 0)}",
            f"  loops stopped: {summary.get('loops_stopped', 0)}",
            f"  cap vetoes: {summary.get('cap_vetoes', 0)}",
            f"  alerts fired: {summary.get('alerts', 0)}",
            f"  estimated $ tracked: ${summary.get('cost_usd_total', 0):.4f}",
            f"  open turns: {summary.get('open_turns', 0)}",
            f"  reply deadline: {cfg.get('reply_deadline_minutes')} min",
            f"  loop threshold: {cfg.get('loop_threshold')} (tightens with spend)",
            f"  notifier: {cfg.get('notifier')}",
        ]
        arg = (raw_args or "").strip()
        if arg.lower() in ("help", "?"):
            lines.append("  usage: /excubitor  — show this summary")
            lines.append("         /excubitor receipt [turn_id]  — latest turn receipt, or one turn")
        else:
            last = ledger.get_receipt("")
            if last:
                lines.append(f"  last receipt: {last['text']}")
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("excubitor: status failed: %s", exc)
        return "Excubitor status unavailable (fail-open)."


def make_handler(ctx: Any = None):
    def handler(raw_args: str = "") -> str:
        return status_text(raw_args)

    return handler
