"""Settings with sane defaults. Reads Hermes plugin settings when available."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEFAULTS: dict[str, Any] = {
    "day_budget_usd": 5.0,
    "session_budget_usd": 2.0,
    "reply_deadline_minutes": 5.0,
    "loop_threshold": 6,
    "loop_approve_first": True,
    "notifier": "log",
    "ntfy_topic": "",
    "webhook_url": "",
    "price_table": {},
    "watch_interval_seconds": 30.0,
    "enabled": True,
    "seen_ttl_hours": 168.0,  # 7 days
    "turn_ttl_hours": 72.0,  # 3 days
    "max_tool_rows_per_turn": 500,
    "prune_every_n_ops": 50,
}

# Conservative USD per million tokens for common models when hooks give tokens but no $ figure.
BUILTIN_PRICES: dict[str, dict[str, float]] = {
    "default": {"input_per_mTok": 1.0, "output_per_mTok": 3.0},
    "gpt-4o": {"input_per_mTok": 2.5, "output_per_mTok": 10.0},
    "gpt-4o-mini": {"input_per_mTok": 0.15, "output_per_mTok": 0.6},
    "gpt-4.1": {"input_per_mTok": 2.0, "output_per_mTok": 8.0},
    "gpt-4.1-mini": {"input_per_mTok": 0.4, "output_per_mTok": 1.6},
    "o4-mini": {"input_per_mTok": 1.1, "output_per_mTok": 4.4},
    "claude-sonnet-4": {"input_per_mTok": 3.0, "output_per_mTok": 15.0},
    "claude-opus-4": {"input_per_mTok": 15.0, "output_per_mTok": 75.0},
    "claude-haiku": {"input_per_mTok": 0.8, "output_per_mTok": 4.0},
    "gemini-2.5-pro": {"input_per_mTok": 1.25, "output_per_mTok": 10.0},
    "gemini-2.5-flash": {"input_per_mTok": 0.3, "output_per_mTok": 2.5},
}

_ctx_settings: Any = None


def bind_ctx(ctx: Any) -> None:
    """Remember the PluginContext so hooks can read live settings."""
    global _ctx_settings
    _ctx_settings = ctx


def _from_ctx(key: str, default: Any) -> Any:
    if _ctx_settings is None:
        return default
    try:
        return _ctx_settings.get_config(key, default=default)
    except Exception:
        return default


def settings() -> dict[str, Any]:
    """Merged settings: defaults ← live ctx config. Never raises."""
    out = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        try:
            val = _from_ctx(key, default)
        except Exception:
            val = default
        if val is None:
            val = default
        out[key] = val
    # Normalize types gently.
    try:
        out["day_budget_usd"] = float(out["day_budget_usd"])
        out["session_budget_usd"] = float(out["session_budget_usd"])
        out["reply_deadline_minutes"] = float(out["reply_deadline_minutes"])
        out["loop_threshold"] = max(2, int(out["loop_threshold"]))
        out["watch_interval_seconds"] = max(5.0, float(out["watch_interval_seconds"]))
        out["seen_ttl_hours"] = max(1.0, float(out.get("seen_ttl_hours") or 168.0))
        out["turn_ttl_hours"] = max(1.0, float(out.get("turn_ttl_hours") or 72.0))
        out["max_tool_rows_per_turn"] = max(50, int(out.get("max_tool_rows_per_turn") or 500))
        out["prune_every_n_ops"] = max(1, int(out.get("prune_every_n_ops") or 50))
        out["loop_approve_first"] = bool(out["loop_approve_first"])
        out["enabled"] = bool(out["enabled"])
        notifier = str(out.get("notifier") or "log").strip().lower()
        out["notifier"] = notifier if notifier in ("log", "ntfy", "webhook") else "log"
        out["ntfy_topic"] = str(out.get("ntfy_topic") or "").strip()
        out["webhook_url"] = str(out.get("webhook_url") or "").strip()
        pt = out.get("price_table") or {}
        out["price_table"] = dict(pt) if isinstance(pt, Mapping) else {}
    except Exception:
        return dict(DEFAULTS)
    return out


def price_for(model: str | None, overrides: Mapping[str, Any] | None = None) -> dict[str, float]:
    """Resolve per-mTok prices for a model name."""
    table = dict(BUILTIN_PRICES)
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, Mapping):
                table[str(k)] = {
                    "input_per_mTok": float(
                        v.get("input_per_mTok", table["default"]["input_per_mTok"])
                    ),
                    "output_per_mTok": float(
                        v.get("output_per_mTok", table["default"]["output_per_mTok"])
                    ),
                }
    if not model:
        return dict(table["default"])
    name = str(model).strip().lower()
    if name in table:
        return dict(table[name])
    # Prefix / substring soft match (e.g. "anthropic/claude-sonnet-4-20250514").
    for key, prices in table.items():
        if key == "default":
            continue
        if key in name or name.endswith(key):
            return dict(prices)
    return dict(table["default"])


def estimate_cost_usd(
    *,
    model: str | None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    overrides: Mapping[str, Any] | None = None,
) -> float:
    """Estimate USD from token counts. Returns 0.0 on bad input."""
    try:
        prices = price_for(model, overrides)
        inp = max(0, int(prompt_tokens or 0))
        out = max(0, int(completion_tokens or 0))
        return (inp / 1_000_000.0) * float(prices["input_per_mTok"]) + (out / 1_000_000.0) * float(
            prices["output_per_mTok"]
        )
    except Exception:
        return 0.0
