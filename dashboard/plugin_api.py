"""Excubitor backend — mounted at /api/plugins/excubitor/.

Shared by Hermes Desktop (ctx.rest) and the web dashboard. Loaded via
importlib as a standalone module; sibling package imports are bootstrapped.
Fail-open: handlers return JSON and never raise to the client.
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger("excubitor.plugin_api")

router = APIRouter()

_ROOT = Path(__file__).resolve().parents[1]  # .../plugins/excubitor


def _ensure_ledger() -> Any:
    """Return the excubitor.ledger module."""
    try:
        from excubitor import ledger  # type: ignore

        return ledger
    except Exception:
        pass

    parent = str(_ROOT.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)

    pkg_name = "excubitor"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(_ROOT)]  # type: ignore[attr-defined]
        pkg.__file__ = str(_ROOT / "__init__.py")
        sys.modules[pkg_name] = pkg

    try:
        from excubitor import ledger  # type: ignore

        return ledger
    except Exception as exc:
        # Last resort: load leaf modules explicitly (relative imports need package).
        import importlib.util

        logger.debug("excubitor: package import failed (%s); loading modules", exc)
        for mod_name, filename in (
            ("excubitor.hashing", "hashing.py"),
            ("excubitor.config", "config.py"),
            ("excubitor.ledger", "ledger.py"),
        ):
            if mod_name in sys.modules:
                continue
            path = _ROOT / filename
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            mod.__package__ = "excubitor"
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        from excubitor import ledger  # type: ignore

        return ledger


def _snapshot() -> dict[str, Any]:
    try:
        return _ensure_ledger().snapshot_for_ui()
    except Exception as exc:
        logger.debug("excubitor: snapshot failed: %s", exc)
        return {
            "ok": False,
            "error": type(exc).__name__,
            "message": "Excubitor ledger unavailable",
            "level": "ok",
            "chip_label": "excubitor",
            "day_spend_usd": 0.0,
            "day_budget_usd": 5.0,
            "open_turns": 0,
            "duplicates_dropped": 0,
            "loops_stopped": 0,
            "cap_vetoes": 0,
            "alerts": 0,
            "recent_alerts": [],
            "enabled": True,
        }


@router.get("/status")
async def status() -> dict[str, Any]:
    """Ledger snapshot for the Desktop status-bar chip / pane."""
    return _snapshot()


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "plugin": "excubitor"}
