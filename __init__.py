"""Excubitor — night sentry for Hermes Agent turns.

One per-turn ledger (plugin_db) is the spine:
  Intake  — pre_gateway_dispatch drops admitted replays; post_gateway_admission opens the turn
  Work    — pre/post_tool_call track no-progress loops (same tool+args, no new results)
  Spend   — post_api_request costs; pre_llm_call / pre_tool_call enforce caps
  Watch   — deadline without outbound reply → pluggable alert (log/ntfy/webhook)

Every hook is fail-open: an internal error never blocks Hermes.
Limitation: event.internal bypasses gateway hooks (documented; cannot cover those paths).
"""

from __future__ import annotations

import logging
from typing import Any

from . import config, intake, spend, watch, work
from .command import make_handler

logger = logging.getLogger("excubitor")


def _safe(fn):
    """Wrap a hook so any exception is swallowed (fail-open)."""

    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            logger.debug("excubitor: hook %s failed open: %s", getattr(fn, "__name__", fn), exc)
            return None

    wrapped.__name__ = getattr(fn, "__name__", "excubitor_hook")
    wrapped.__doc__ = getattr(fn, "__doc__", None)
    return wrapped


def on_pre_tool_call(tool_name=None, args=None, task_id="", session_id="", turn_id="", **kwargs):
    """Compose spend veto then loop detection. Block > approve precedence is host-side."""
    cfg = config.settings()
    if not cfg.get("enabled", True):
        return None
    # Spend first: a hard budget stop outranks loop soft-approve.
    spend_directive = spend.on_pre_tool_call_spend(
        tool_name=tool_name,
        args=args,
        task_id=task_id,
        session_id=session_id,
        turn_id=turn_id,
        **kwargs,
    )
    if isinstance(spend_directive, dict) and spend_directive.get("action") == "block":
        return spend_directive

    from . import ledger

    tid = ledger.resolve_turn_id(
        session_id=session_id or "", turn_id=turn_id or "", task_id=task_id or ""
    )
    if not ledger.get_turn(tid):
        ledger.open_turn(
            tid,
            session_id=session_id or "",
            deadline_minutes=cfg["reply_deadline_minutes"],
            meta={"opened_by": "pre_tool_call"},
        )
    loop_directive = work.evaluate_loop(
        tid,
        str(tool_name or ""),
        args if isinstance(args, dict) else {},
        session_id=session_id or "",
        cfg=cfg,
    )
    return loop_directive


def register(ctx: Any) -> None:
    """Wire hooks + slash commands. Never raise out of register()."""
    try:
        config.bind_ctx(ctx)
    except Exception as exc:
        logger.debug("excubitor: bind_ctx failed: %s", exc)

    hooks = {
        "pre_gateway_dispatch": intake.on_pre_gateway_dispatch,
        "post_gateway_admission": intake.on_post_gateway_admission,
        "pre_llm_call": spend.on_pre_llm_call,
        "post_llm_call": spend.on_post_llm_call,
        "pre_tool_call": on_pre_tool_call,
        "post_tool_call": work.on_post_tool_call,
        "post_api_request": spend.on_post_api_request,
        "agent_loop_stopped": watch.on_agent_loop_stopped,
    }
    for name, fn in hooks.items():
        try:
            ctx.register_hook(name, _safe(fn))
        except Exception as exc:
            logger.warning("excubitor: failed to register hook %s: %s", name, exc)

    handler = make_handler(ctx)
    for cmd, desc in (
        ("excubitor", "Excubitor ledger status (dedup / loops / spend / alerts)"),
        ("watch", "Alias for /excubitor — night-sentry status"),
    ):
        try:
            ctx.register_command(cmd, handler, description=desc)
        except Exception as exc:
            logger.debug("excubitor: register_command /%s failed: %s", cmd, exc)

    try:
        watch.ensure_watcher()
    except Exception as exc:
        logger.debug("excubitor: watcher not started: %s", exc)

    logger.info("excubitor: registered (intake/work/spend/watch on one ledger)")
