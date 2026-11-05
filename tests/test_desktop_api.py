"""Unit tests for Desktop/Dashboard ledger snapshot + plugin_api."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ledger_mod(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    # Import as package
    if str(ROOT.parent) not in sys.path:
        sys.path.insert(0, str(ROOT.parent))
    # Fresh modules
    for k in list(sys.modules):
        if k == "excubitor" or k.startswith("excubitor."):
            del sys.modules[k]
    from excubitor import ledger

    db = tmp_path / "ledger.db"
    ledger.use_test_db(db)
    yield ledger
    ledger.use_test_db(None)


def test_snapshot_ok_level(ledger_mod):
    snap = ledger_mod.snapshot_for_ui()
    assert snap["ok"] is True
    assert snap["level"] == "ok"
    assert "chip_label" in snap
    assert snap["open_turns"] == 0
    assert snap["recent_alerts"] == []


def test_snapshot_warning_near_cap(ledger_mod, monkeypatch):
    day = ledger_mod.utc_day()
    # Seed high day spend relative to default $5 cap
    with ledger_mod._tx() as conn:
        conn.execute(
            "INSERT INTO spend(scope, scope_key, day, cost_usd) VALUES('day', ?, ?, 4.5) "
            "ON CONFLICT(scope, scope_key, day) DO UPDATE SET cost_usd=4.5",
            (day, day),
        )
    snap = ledger_mod.snapshot_for_ui()
    assert snap["spend_ratio"] >= 0.8
    assert snap["level"] == "warning"


def test_snapshot_alert_on_recent_hang(ledger_mod):
    now = time.time()
    with ledger_mod._tx() as conn:
        conn.execute(
            "INSERT INTO turns(turn_id, message_key, session_id, platform, chat_id, "
            "opened_at, deadline_at, status, cost_usd, loop_count, loop_blocked, "
            "alerts_fired, last_reason, meta_json) "
            "VALUES ('hang1','','s','cli','', ?, ?, 'alerted', 0.4, 6, 0, 1, "
            "'stuck after loops', '{}')",
            (now - 90, now - 30),
        )
    snap = ledger_mod.snapshot_for_ui()
    assert snap["level"] == "alert"
    assert len(snap["recent_alerts"]) >= 1
    assert "stuck" in snap["recent_alerts"][0]["reason"]
    assert "hang" in snap["chip_label"]


def test_plugin_api_status_endpoint(ledger_mod, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    api_path = ROOT / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location(
        "hermes_dashboard_plugin_excubitor_test", api_path
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    health = asyncio.run(mod.health())
    assert health["ok"] is True
    status = asyncio.run(mod.status())
    assert status["ok"] is True
    assert "chip_label" in status
    assert status["level"] in ("ok", "warning", "alert")


def test_desktop_plugin_js_exists_and_is_plain_esm():
    js = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert "STATUSBAR_AREAS" in js
    assert "export default" in js
    assert "@hermes/plugin-sdk" in js
    # No JSX syntax (disk plugins are uncompiled)
    assert "</" not in js
    assert "ctx.rest" in js
