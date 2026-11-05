"""Pluggable alert notifier: log (default), ntfy.sh topic, or webhook. Fail-open."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Mapping

logger = logging.getLogger("excubitor.notify")


def send_alert(cfg: Mapping[str, Any], *, title: str, body: str, payload: Mapping[str, Any] | None = None) -> bool:
    """Dispatch an alert. Returns True if an attempt was made without raising to the caller."""
    try:
        mode = str(cfg.get("notifier") or "log").lower()
        data = dict(payload or {})
        data.setdefault("title", title)
        data.setdefault("body", body)
        if mode == "ntfy":
            topic = str(cfg.get("ntfy_topic") or "").strip()
            if not topic:
                logger.warning("excubitor alert (ntfy misconfigured, falling back to log): %s — %s", title, body)
                return True
            return _ntfy(topic, title=title, body=body)
        if mode == "webhook":
            url = str(cfg.get("webhook_url") or "").strip()
            if not url:
                logger.warning("excubitor alert (webhook misconfigured, falling back to log): %s — %s", title, body)
                return True
            return _webhook(url, data)
        logger.warning("excubitor ALERT: %s — %s", title, body)
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
