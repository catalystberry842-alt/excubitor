"""Intake: pre_gateway_dispatch dedup + open ledger entry + start reply deadline."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from . import config, ledger
from .hashing import message_key as mk_message_key

logger = logging.getLogger("excubitor.intake")


def _source_bits(event: Any) -> tuple[str, str, str | None, Any]:
    source = getattr(event, "source", None)
    platform = ""
    chat_id = ""
    if source is not None:
        plat = getattr(source, "platform", None)
        platform = getattr(plat, "value", None) or str(plat or "")
        chat_id = str(getattr(source, "chat_id", "") or "")
    message_id = getattr(event, "message_id", None) or getattr(event, "ledger_message_id", None)
    raw = getattr(event, "raw_message", None)
    if raw is None:
        raw = getattr(event, "text", None)
    return str(platform), str(chat_id), (str(message_id) if message_id else None), raw


def _note_dup(key: str) -> None:
    """Attach the dropped duplicate to the original turn; refresh its receipt if closed."""
    try:
        turn = ledger.find_turn_by_message_key(key)
        if not turn:
            return
        tid = str(turn["turn_id"])
        ledger.add_turn_event(tid, "dedup", message_key=key)
        if turn.get("status") != "open":
            from . import receipt

            receipt.finalize(tid)
    except Exception as exc:
        logger.debug("excubitor: dedup note failed open: %s", exc)


def on_pre_gateway_dispatch(event=None, gateway=None, session_store=None, **kwargs):
    """Drop duplicate inbound messages; open a turn ledger entry for new ones."""
    try:
        cfg = config.settings()
        if not cfg.get("enabled", True):
            return None
        if event is None:
            return None
        if getattr(event, "internal", False):
            # Core already bypasses hooks for internal events; belt-and-suspenders.
            return None
        platform, chat_id, message_id, raw = _source_bits(event)
        key = mk_message_key(platform, chat_id, message_id, raw)
        if ledger.already_seen(key):
            ledger.bump_stat("duplicates_dropped")
            _note_dup(key)
            logger.info("excubitor: skipping duplicate inbound %s", key)
            return {"action": "skip", "reason": f"excubitor: duplicate message {key}"}
        inserted = ledger.mark_seen(
            key, platform=platform, chat_id=chat_id, message_id=message_id or ""
        )
        if not inserted:
            ledger.bump_stat("duplicates_dropped")
            _note_dup(key)
            return {"action": "skip", "reason": f"excubitor: duplicate message {key}"}
        turn_id = f"gw:{key}:{uuid.uuid4().hex[:8]}"
        ledger.open_turn(
            turn_id,
            message_key=key,
            platform=platform,
            chat_id=chat_id,
            deadline_minutes=cfg["reply_deadline_minutes"],
            meta={"opened_by": "pre_gateway_dispatch", "ts": time.time()},
        )
        # Stash turn id on the event when possible so later hooks can find it.
        try:
            event._excubitor_turn_id = turn_id
        except Exception:
            pass
        return None
    except Exception as exc:
        logger.debug("excubitor: pre_gateway_dispatch failed open: %s", exc)
        return None
