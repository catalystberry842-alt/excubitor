"""Intake: replay-drop on pre_gateway_dispatch; open ledger after admission."""

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


def _admission_bits(
    platform: str = "",
    source: Any = None,
    message_id: Any = None,
    text: str = "",
    event: Any = None,
) -> tuple[str, str, str | None, Any]:
    if event is not None:
        return _source_bits(event)
    plat = str(platform or "")
    chat_id = ""
    if isinstance(source, dict):
        chat_id = str(source.get("chat_id") or "")
        if not plat:
            p = source.get("platform")
            if isinstance(p, dict):
                plat = str(p.get("value") or "")
            else:
                plat = str(p or "")
    elif source is not None:
        chat_id = str(getattr(source, "chat_id", "") or "")
        if not plat:
            p = getattr(source, "platform", None)
            plat = getattr(p, "value", None) or str(p or "")
    mid = str(message_id) if message_id else None
    return plat, chat_id, mid, text


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
    """Drop inbound replays already admitted. Do not open a turn or mark seen here."""
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
        return None
    except Exception as exc:
        logger.debug("excubitor: pre_gateway_dispatch failed open: %s", exc)
        return None


def on_post_gateway_admission(
    session_key="",
    platform="",
    source=None,
    message_id=None,
    text="",
    event=None,
    **kwargs,
):
    """Record seen + open a ledger turn only after Hermes admits the sender."""
    try:
        cfg = config.settings()
        if not cfg.get("enabled", True):
            return None
        plat, chat_id, mid, raw = _admission_bits(
            platform=platform, source=source, message_id=message_id, text=text, event=event
        )
        if not mid and raw is None:
            return None
        key = mk_message_key(plat, chat_id, mid, raw)
        inserted = ledger.mark_seen(
            key, platform=plat, chat_id=chat_id, message_id=mid or ""
        )
        if not inserted:
            ledger.bump_stat("duplicates_dropped")
            _note_dup(key)
            return None
        turn_id = f"gw:{key}:{uuid.uuid4().hex[:8]}"
        ledger.open_turn(
            turn_id,
            message_key=key,
            platform=plat,
            chat_id=chat_id,
            deadline_minutes=cfg["reply_deadline_minutes"],
            meta={
                "opened_by": "post_gateway_admission",
                "ts": time.time(),
                "session_key": session_key or "",
            },
        )
        if event is not None:
            try:
                event._excubitor_turn_id = turn_id
            except Exception:
                pass
        return None
    except Exception as exc:
        logger.debug("excubitor: post_gateway_admission failed open: %s", exc)
        return None
