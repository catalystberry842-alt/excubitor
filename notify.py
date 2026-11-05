"""Pluggable alert notifier: log (default), ntfy.sh topic, or webhook. Fail-open."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("excubitor.notify")

_SECRET_KV = re.compile(
    r"(?i)(api[_-]?key|token|secret|authorization|bearer|password)\s*[:=]\s*\S+"
)
_SK = re.compile(r"sk-[A-Za-z0-9_-]{10,}")
_GHO = re.compile(r"gho_[A-Za-z0-9]{10,}")
_GHP = re.compile(r"ghp_[A-Za-z0-9]{10,}")


def _sanitize_text(s: str, *, limit: int = 500) -> str:
    """Strip likely secrets and bound length before any network/log emit."""
    text = str(s or "")
    text = _SECRET_KV.sub(lambda m: m.group(1) + "=[REDACTED]", text)
    text = _SK.sub("sk-[REDACTED]", text)
    text = _GHO.sub("gho_[REDACTED]", text)
    text = _GHP.sub("ghp_[REDACTED]", text)
    if len(text) > limit:
        text = text[: limit - 20] + "…[truncated]"
    return text


def _validate_webhook_url(url: str) -> bool:
    """Only allow http(s) absolute URLs; block credentials-in-URL and non-http schemes."""
    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.scheme not in ("http", "https"):
        return False
    if u.username or u.password:
        return False
    if not u.hostname:
        return False
    return True


def _validate_ntfy_topic(topic: str) -> bool:
    """ntfy topics: alphanumeric, dashes, underscores; no paths/URLs."""
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", topic or ""))


def send_alert(
    cfg: Mapping[str, Any],
    *,
    title: str,
    body: str,
    payload: Mapping[str, Any] | None = None,
) -> bool:
    """Dispatch an alert. Returns True if an attempt was made without raising to the caller."""
    try:
        mode = str(cfg.get("notifier") or "log").lower()
        title_s = _sanitize_text(title, limit=120)
        body_s = _sanitize_text(body)
        data: dict[str, Any] = dict(payload or {})
        data.setdefault("title", title_s)
        data.setdefault("body", body_s)
        # Never put raw user text / tool args into outbound alerts
        for dirty in ("user_message", "raw_message", "args", "result", "prompt"):
            data.pop(dirty, None)
        safe_payload = {
            k: (_sanitize_text(v) if isinstance(v, str) else v) for k, v in data.items()
        }
        if mode == "ntfy":
            topic = str(cfg.get("ntfy_topic") or "").strip()
            if not topic or not _validate_ntfy_topic(topic):
                logger.warning(
                    "excubitor alert (ntfy misconfigured, falling back to log): %s — %s",
                    title_s,
                    body_s,
                )
                return True
            return _ntfy(topic, title=title_s, body=body_s)
        if mode == "webhook":
            url = str(cfg.get("webhook_url") or "").strip()
            if not url or not _validate_webhook_url(url):
                logger.warning(
                    "excubitor alert (webhook URL rejected, falling back to log): %s — %s",
                    title_s,
                    body_s,
                )
                return True
            return _webhook(url, safe_payload)
        logger.warning("excubitor ALERT: %s — %s", title_s, body_s)
        return True
    except Exception as exc:
        logger.debug("excubitor: notify failed: %s", exc)
        return False


def _ntfy(topic: str, *, title: str, body: str) -> bool:
    url = f"https://ntfy.sh/{topic}"
    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers={"Title": title[:120], "Content-Type": "text/plain; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read(256)
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("excubitor: ntfy notify failed: %s", exc)
        return False


def _webhook(url: str, payload: Mapping[str, Any]) -> bool:
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read(256)
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("excubitor: webhook notify failed: %s", exc)
        return False
