"""Slash command /excubitor (alias /watch) — ledger status summary."""

from __future__ import annotations

import logging
from typing import Any

from . import config, ledger

logger = logging.getLogger("excubitor.command")


def status_text(raw_args: str = "") -> str:
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
        arg = (raw_args or "").strip().lower()
        if arg in ("help", "?"):
            lines.append("  usage: /excubitor  — show this summary")
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("excubitor: status failed: %s", exc)
        return "Excubitor status unavailable (fail-open)."


def make_handler(ctx: Any = None):
    def handler(raw_args: str = "") -> str:
        return status_text(raw_args)

    return handler
