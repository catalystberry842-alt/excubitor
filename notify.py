"""Pluggable alert notifier: log (default), ntfy.sh topic, or webhook. Fail-open."""

from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import re
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("excubitor.notify")

_SECRET_KV = re.compile(
    r"""(?i)(api[_-]?key|token|secret|authorization|bearer|password)["']?\s*[:=]\s*"""
    r"""(?:["'][^"'\r\n]*["']|(?:Bearer\s+|Basic\s+)?[^\s,}\]"']+)"""
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_NAME = re.compile(r"(?i)^(api[_-]?key|token|secret|authorization|password)$")
_SK = re.compile(r"sk-[A-Za-z0-9_-]{10,}")
_GHO = re.compile(r"gho_[A-Za-z0-9]{10,}")
_GHP = re.compile(r"ghp_[A-Za-z0-9]{10,}")


def _sanitize_text(s: str, *, limit: int = 500) -> str:
    """Strip likely secrets and bound length before any network/log emit."""
    text = str(s or "")
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _SECRET_KV.sub(lambda m: m.group(1) + "=[REDACTED]", text)
    text = _SK.sub("sk-[REDACTED]", text)
    text = _GHO.sub("gho_[REDACTED]", text)
    text = _GHP.sub("ghp_[REDACTED]", text)
    if len(text) > limit:
        text = text[: limit - 20] + "…[truncated]"
    return text


def _validate_webhook_url(url: str, *, allow_private: bool = False) -> bool:
    """Require HTTPS outside loopback; private targets need explicit opt-in.

    DNS targets are additionally resolved/checked immediately before transport.
    """
    try:
        u = urlparse(url)
        host = u.hostname
        if not host or u.username or u.password or u.fragment:
            return False
        _ = u.port  # reject invalid ports
        local = host.lower() == "localhost"
        try:
            addr = ipaddress.ip_address(host)
            local = addr.is_loopback
            if not addr.is_global and not local and not allow_private:
                return False
        except ValueError:
            pass
        return u.scheme == "https" or (u.scheme == "http" and local)
    except ValueError:
        return False


def _sanitize_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(k): ("[REDACTED]" if _SECRET_NAME.fullmatch(str(k)) else _sanitize_payload(v))
            for k, v in value.items()
            if str(k) not in ("user_message", "raw_message", "args", "result", "prompt")
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(v) for v in value]
    return _sanitize_text(value) if isinstance(value, str) else value


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
        safe_payload = _sanitize_payload(data)
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
            if not url or not _validate_webhook_url(
                url, allow_private=bool(cfg.get("allow_private_webhooks", False))
            ):
                logger.warning(
                    "excubitor alert (webhook URL rejected, falling back to log): %s — %s",
                    title_s,
                    body_s,
                )
                return True
            if cfg.get("allow_private_webhooks", False):
                return _webhook(url, safe_payload, allow_private=True)
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


def _webhook(url: str, payload: Mapping[str, Any], *, allow_private: bool = False) -> bool:
    """Pin the connection to a validated DNS result; preserve TLS SNI/Host.

    No proxies or redirects: either could bypass the destination checks.
    """
    if not _validate_webhook_url(url, allow_private=allow_private):
        return False
    connection = None
    try:
        u = urlparse(url)
        port = u.port or (443 if u.scheme == "https" else 80)
        addresses = socket.getaddrinfo(u.hostname, port, type=socket.SOCK_STREAM)
        if not addresses:
            return False
        local_url = u.hostname == "localhost"
        try:
            local_url = ipaddress.ip_address(u.hostname).is_loopback
        except ValueError:
            pass
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global and not (local_url and ip.is_loopback) and not allow_private:
                return False
        factory = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        connection = factory(u.hostname, port, timeout=8)
        # HTTPSConnection wraps this socket with the ORIGINAL hostname for SNI
        # and certificate verification; it never resolves that hostname again.
        pinned = addresses[0][4][0]
        connection._create_connection = lambda addr, timeout=8, source_address=None: (
            socket.create_connection((pinned, port), timeout, source_address)
        )
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        target = (u.path or "/") + ("?" + u.query if u.query else "")
        connection.request("POST", target, body=raw, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        response.read(256)
        return 200 <= response.status < 300
    except (ValueError, OSError, http.client.HTTPException) as exc:
        logger.warning("excubitor: webhook notify failed: %s", type(exc).__name__)
        return False
    finally:
        if connection is not None:
            connection.close()
