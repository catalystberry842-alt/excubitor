"""Stable hashing helpers for message keys, tool args, and results."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_WS = re.compile(r"\s+")


def _canon(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _canon(obj[k]) for k in sorted(obj, key=lambda x: str(x))}
    if isinstance(obj, (list, tuple)):
        return [_canon(x) for x in obj]
    if isinstance(obj, str):
        return _WS.sub(" ", obj.strip())
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return str(obj)


def stable_hash(obj: Any, *, n: int = 16) -> str:
    raw = json.dumps(_canon(obj), ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:n]


def args_hash(args: Any) -> str:
    return stable_hash(args if isinstance(args, dict) else {"_": args})


def result_hash(result: Any) -> str:
    if result is None:
        return stable_hash("")
    if isinstance(result, (dict, list)):
        return stable_hash(result)
    text = str(result)
    # Cap huge tool results so hashing stays cheap; content-equivalence still holds for loops.
    if len(text) > 8000:
        text = text[:4000] + f"...<{len(text)}>" + text[-2000:]
    return stable_hash(text)


def message_key(
    platform: str | None, chat_id: str | None, message_id: str | None, raw: Any = None
) -> str:
    """platform+chat_id+message_id, falling back to a hash of raw_message / text."""
    plat = (platform or "unknown").strip().lower() or "unknown"
    chat = str(chat_id or "").strip() or "-"
    mid = str(message_id or "").strip()
    if mid:
        return f"{plat}:{chat}:{mid}"
    return f"{plat}:{chat}:h:{stable_hash(raw if raw is not None else '', n=20)}"
