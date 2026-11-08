"""Additive tests for richer Desktop chip payload — merge into test_desktop_api.py."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_snapshot_rich_chip_fields(ledger_mod):
    snap = ledger_mod.snapshot_for_ui()
    assert snap["ok"] is True
    assert "blocked_count" in snap
    assert snap["blocked_count"] == snap["loops_stopped"] + snap["cap_vetoes"]
    assert "hang_idle_seconds" in snap
    assert "longest_silent_seconds" in snap
    assert snap["hang_idle_seconds"] == snap["longest_silent_seconds"]
    assert "spend_saved_usd" in snap
    assert isinstance(snap["stages"], dict)
    for key in ("intake", "work", "spend", "watch"):
        assert key in snap["stages"]
        assert snap["stages"][key]["level"] in ("ok", "warning", "alert")
    assert isinstance(snap["open_turns_detail"], list)
    for a in snap["recent_alerts"]:
        assert "relative" in a
        assert "alerted_at" in a


def test_snapshot_open_turn_idle(ledger_mod):
    now = time.time()
    with ledger_mod._tx() as conn:
        conn.execute(
            "INSERT INTO turns(turn_id, message_key, session_id, platform, chat_id, "
            "opened_at, deadline_at, status, cost_usd, loop_count, loop_blocked, "
            "alerts_fired, last_reason, meta_json) "
            "VALUES ('open1','','s','cli','', ?, ?, 'open', 0.05, 1, 0, 0, '', '{}')",
            (now - 47, now + 200),
        )
    snap = ledger_mod.snapshot_for_ui()
    assert snap["open_turns"] >= 1
    assert len(snap["open_turns_detail"]) >= 1
    idle = snap["open_turns_detail"][0]["idle_seconds"]
    assert 40 <= idle <= 120
    assert snap["hang_idle_seconds"] >= 40
    assert snap["stages"]["watch"]["value"] == snap["hang_idle_seconds"]


def test_snapshot_stages_from_evidence_shape(ledger_mod):
    """Seed counters matching docs/evidence/desktop_api_status.json."""
    now = time.time()
    day = ledger_mod.utc_day()
    with ledger_mod._tx() as conn:
        conn.execute(
            "INSERT INTO spend(scope, scope_key, day, cost_usd) VALUES('day', ?, ?, 0.42) "
            "ON CONFLICT(scope, scope_key, day) DO UPDATE SET cost_usd=0.42",
            (day, day),
        )
        for key, val in (
            ("duplicates_dropped", 3),
            ("loops_stopped", 2),
            ("cap_vetoes", 1),
            ("alerts", 1),
            ("cost_usd_total", 0.42),
        ):
            conn.execute(
                "INSERT INTO stats(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, val),
            )
        conn.execute(
            "INSERT INTO turns(turn_id, message_key, session_id, platform, chat_id, "
            "opened_at, deadline_at, status, cost_usd, loop_count, loop_blocked, "
            "alerts_fired, last_reason, meta_json) "
            "VALUES ('demo','','sess','cli','', ?, ?, 'alerted', 0.42, 6, 0, 1, "
            "'stuck after 6 search loops', '{}')",
            (now - 90, now - 30),
        )
    snap = ledger_mod.snapshot_for_ui()
    assert snap["level"] == "alert"
    assert snap["duplicates_dropped"] == 3
    assert snap["loops_stopped"] == 2
    assert snap["cap_vetoes"] == 1
    assert snap["blocked_count"] == 3
    assert snap["stages"]["intake"]["value"] == 3
    assert snap["stages"]["work"]["value"] == 2
    assert snap["stages"]["spend"]["value"] == pytest.approx(0.42, abs=0.01)
    assert snap["stages"]["watch"]["level"] == "alert"
    assert "stuck" in snap["recent_alerts"][0]["reason"]
    assert snap["recent_alerts"][0]["relative"].endswith("ago")


def test_desktop_plugin_js_rich_chip_markers():
    js = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert "STATUSBAR_AREAS" in js
    assert "StatusDot" in js
    assert "Popover" in js
    assert "PopoverContent" in js
    assert "blocked_count" in js or "blockedOf" in js
    assert "hang_idle" in js or "hangIdle" in js
    assert "stages" in js
    assert "open_turns_detail" in js
    assert "writeClipboard" in js or "Copy status" in js
    assert "</" not in js
    assert "@hermes/plugin-sdk" in js


def test_dropdown_renders_receipt_and_empty_state():
    """Exercise the real dropdown with an element-factory SDK stand-in."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for desktop rendering checks")
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let source = fs.readFileSync('desktop/plugin.js', 'utf8');
source = source.replace(/^import[\s\S]*?from '[^']+'\n/gm, '');
source = source.replace('export default {', 'const plugin = {');
const element = (type, props) => ({type, ...props});
const context = { jsx: element, jsxs: element, Button: 'Button',
  StatusDot: 'StatusDot' };
vm.createContext(context);
vm.runInContext(source + '\nthis.renderDropdown = Dropdown;', context);
function text(node) {
  if (node == null) return '';
  if (typeof node !== 'object') return String(node);
  if (Array.isArray(node)) return node.map(text).join(' ');
  return text(node.children);
}
const render = (data) => text(context.renderDropdown({data, ctx: {}}));
assert.match(render({ok: true, last_receipt: {text: 'Turn abc: loop blocked'}}),
  /Latest turn receipt.*Turn abc: loop blocked/);
assert.match(render({ok: true}), /No turn receipt yet/);
assert.match(render({ok: false}), /Ledger backend unavailable/);
"""
    subprocess.run([node, "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
