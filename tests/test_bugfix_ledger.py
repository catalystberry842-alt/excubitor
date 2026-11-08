"""Behavior regressions from the November audit: ledger."""

import concurrent.futures
import json
import time
from types import SimpleNamespace

import pytest


def event(mid="one", chat="A"):
    return SimpleNamespace(
        message_id=mid,
        text="hello",
        raw_message="hello",
        internal=False,
        source=SimpleNamespace(platform="telegram", chat_id=chat),
    )


def test_cli_turns_have_independent_history_and_watch(modules):
    ledger, s, p = modules["ledger"], modules["spend"], modules["pkg"]
    ids = []
    for _ in range(8):
        s.on_pre_llm_call(session_id="cli")
        tid = ledger.find_open_turn_for_session("cli")["turn_id"]
        ids.append(tid)
        assert p.on_pre_tool_call(session_id="cli", tool_name="shell", args="git status") is None
        modules["work"].on_post_tool_call(
            session_id="cli", tool_name="shell", args="git status", result="clean"
        )
        s.on_post_api_request(session_id="cli", usage={"cost_usd": 0.01})
        assert ledger.get_turn(tid)["cost_usd"] == pytest.approx(0.01)
        s.on_post_llm_call(session_id="cli")
    assert len(set(ids)) == 8
    s.on_pre_llm_call(session_id="cli")
    with ledger._tx() as c:
        c.execute("UPDATE turns SET deadline_at=0 WHERE status='open'")
    assert len(ledger.overdue_open_turns()) == 1


def test_concurrent_chats_match_identity_not_newest(modules):
    ledger, s, i = [modules[k] for k in ("ledger", "spend", "intake")]
    for chat in ("A", "B"):
        i.on_post_gateway_admission(event=event(chat=chat), session_key=chat)
    s.on_pre_llm_call(session_id="sA", session_key="A", turn_id="coreA")
    s.on_pre_llm_call(session_id="sB", session_key="B", turn_id="coreB")
    for chat in ("A", "B"):
        s.on_post_api_request(session_id="s" + chat, turn_id="core" + chat, usage={"cost_usd": 0.2})
        turn = ledger.find_open_turn_for_session("s" + chat)
        assert turn["chat_id"] == chat
        assert turn["cost_usd"] == pytest.approx(0.2)


def test_unidentified_hook_never_steals_gateway_turn(modules):
    i, ledger = modules["intake"], modules["ledger"]
    i.on_post_gateway_admission(event=event(), session_key="A")
    original = ledger.find_open_unbound_turn()["turn_id"]
    modules["spend"].on_pre_llm_call(session_id="unrelated", turn_id="cli")
    assert not ledger.get_turn(original)["session_id"]


def test_nondict_args_remain_distinct_and_post_matches(modules):
    ledger, p, w = modules["ledger"], modules["pkg"], modules["work"]
    modules["spend"].on_pre_llm_call(session_id="s")
    for arg in ("ls", "pwd", "date", [1], [2], 4, "whoami", "uptime"):
        assert p.on_pre_tool_call(session_id="s", tool_name="shell", args=arg) is None
        w.on_post_tool_call(session_id="s", tool_name="shell", args=arg, result="ok")
    calls = ledger.all_tool_calls(ledger.find_open_turn_for_session("s")["turn_id"])
    assert len({c["args_hash"] for c in calls}) == 8
    assert all(c["result_hash"] for c in calls)


def test_bad_config_preserves_kill_switch_budget_and_notifier(modules, monkeypatch, caplog):
    cfg = modules["config"]
    values = {"enabled": False, "loop_threshold": "six", "day_budget_usd": 0.5, "notifier": "ntfy"}
    monkeypatch.setattr(cfg, "_from_ctx", lambda k, d: values.get(k, d))
    settings = cfg.settings()
    assert settings["enabled"] is False
    assert settings["day_budget_usd"] == 0.5
    assert settings["notifier"] == "ntfy"
    assert settings["loop_threshold"] == 6
    assert "loop_threshold" in caplog.text


@pytest.mark.parametrize(
    "model,price", [("openai/gpt-4o-mini-2024", 0.15), ("gpt-4.1-mini-2025", 0.4)]
)
def test_specific_model_price_wins(modules, model, price):
    assert modules["config"].price_for(model)["input_per_mTok"] == price


@pytest.mark.parametrize("scope", ["day", "session"])
def test_zero_budget_is_hard_zero(modules, scope, monkeypatch):
    cfg = {**modules["config"].DEFAULTS, scope + "_budget_usd": 0}
    monkeypatch.setattr(modules["config"], "settings", lambda: cfg)
    assert modules["spend"].over_cap(cfg, session_id="s")
    snap = modules["ledger"].snapshot_for_ui()
    assert snap[scope + "_budget_usd"] == 0
    if scope == "day":
        assert snap["level"] == "alert"
        modules["ledger"].open_turn("t")
        assert modules["receipt"].build("t", cfg)["data"]["day_budget_usd"] == 0


def test_vetoed_turn_is_closed_even_without_completion(modules, monkeypatch):
    cfg = {**modules["config"].DEFAULTS, "day_budget_usd": 0}
    monkeypatch.setattr(modules["config"], "settings", lambda: cfg)
    modules["spend"].on_pre_llm_call(session_id="s", turn_id="t")
    assert modules["ledger"].get_turn("t")["status"] == "blocked"
    assert modules["ledger"].overdue_open_turns(now=time.time() + 10000) == []


def test_intermediate_tool_response_does_not_close_turn(modules):
    s, ledger = modules["spend"], modules["ledger"]
    s.on_pre_llm_call(session_id="s", turn_id="t")
    s.on_post_llm_call(session_id="s", turn_id="t", tool_calls=[{"name": "shell"}])
    assert ledger.get_turn("t")["status"] == "open"
    s.on_post_llm_call(session_id="s", turn_id="t", assistant_response="done")
    assert ledger.get_turn("t")["status"] == "replied"


def test_memory_fallback_shared_with_watcher_thread(modules, monkeypatch):
    import sys
    import types

    storage = types.ModuleType("plugins.plugin_storage")

    def unavailable(*a):
        raise RuntimeError("unavailable")

    storage.plugin_db = unavailable
    monkeypatch.setitem(sys.modules, "plugins.plugin_storage", storage)
    ledger = modules["ledger"]
    ledger.use_test_db(None)
    ledger.open_turn("memory-turn")
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        turn = pool.submit(ledger.get_turn, "memory-turn").result()
    assert turn is not None


def test_approvals_per_tool_are_not_loop_stops(modules):
    ledger, w = modules["ledger"], modules["work"]
    ledger.open_turn("t")
    cfg = {**modules["config"].DEFAULTS, "loop_threshold": 2}
    for tool in ("shell", "search"):
        results = [w.evaluate_loop("t", tool, {}, cfg=cfg) for _ in range(3)]
        assert results[-1]["action"] == "approve"
    assert ledger.get_turn("t")["loop_blocked"] == 0
    assert ledger.summarize()["loops_stopped"] == 0
    assert w.evaluate_loop("t", "shell", {}, cfg=cfg)["action"] == "block"
    assert ledger.summarize()["loops_stopped"] == 1


def test_chip_recovers_from_old_alert_and_keeps_fractional_cap(modules, monkeypatch):
    ledger = modules["ledger"]
    ledger.open_turn("t")
    with ledger._tx() as c:
        c.execute("UPDATE turns SET deadline_at=?", (time.time() - 7200,))
    assert ledger.mark_alert_fired("t", "hang")
    cfg = {**modules["config"].DEFAULTS, "day_budget_usd": 0.5}
    monkeypatch.setattr(modules["config"], "settings", lambda: cfg)
    snap = ledger.snapshot_for_ui()
    assert snap["level"] == "ok"
    assert "$0.50" in snap["chip_label"]


def test_cache_tokens_are_billed(modules):
    s, cfg = modules["spend"], modules["config"].DEFAULTS
    a = s._cost_from_hook(
        usage={
            "input_tokens": 100,
            "cache_read_input_tokens": 200,
            "cache_creation_input_tokens": 300,
        },
        model=None,
        cfg=cfg,
    )
    assert a == pytest.approx(0.0006)  # conservative default rate for extra cache buckets


def test_running_total_cost_is_delta_not_sum(modules):
    s, ledger = modules["spend"], modules["ledger"]
    s.on_pre_llm_call(session_id="s", turn_id="t")
    for total in (0.1, 0.3, 0.3):
        s.on_post_api_request(session_id="s", turn_id="t", usage={"total_cost": total})
    assert ledger.get_turn("t")["cost_usd"] == pytest.approx(0.3)


def test_session_budget_survives_utc_midnight(modules, monkeypatch):
    ledger, s = modules["ledger"], modules["spend"]
    ledger.open_turn("t", session_id="s")
    monkeypatch.setattr(ledger, "utc_day", lambda *a: "2026-11-07")
    ledger.add_cost("t", 0.4, session_id="s")
    monkeypatch.setattr(ledger, "utc_day", lambda *a: "2026-11-08")
    assert s.over_cap({**modules["config"].DEFAULTS, "session_budget_usd": 0.3}, session_id="s")
    assert ledger.spend_for("day", "2026-11-08") == 0


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": 100, "cache_read_tokens": 200, "cache_write_tokens": 300},
        {
            "input_tokens": 100,
            "cache_read_tokens": 200,
            "cache_write_tokens": 300,
            "prompt_tokens": 600,
        },
        {"prompt_tokens": 600, "prompt_tokens_details": {"cached_tokens": 200}},
    ],
)
def test_cache_usage_shapes_are_not_double_counted(modules, usage):
    cost = modules["spend"]._cost_from_hook(usage=usage, model=None, cfg=modules["config"].DEFAULTS)
    assert cost == pytest.approx(0.0006)


def test_per_call_cost_preferred_over_running_total(modules):
    spend, ledger = modules["spend"], modules["ledger"]
    spend.on_pre_llm_call(session_id="s", turn_id="t")
    spend.on_post_api_request(
        session_id="s", turn_id="t", usage={"cost_usd": 0.1, "total_cost": 10}
    )
    spend.on_post_api_request(session_id="s", turn_id="t", usage={"total_cost": 10.2})
    assert ledger.get_turn("t")["cost_usd"] == pytest.approx(0.3)


def test_chip_budget_alert_is_not_labeled_as_hang():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for desktop render regression")
    script = Path(__file__).resolve().parents[1] / "desktop/plugin.js"
    source = script.read_text()
    face = source[source.index("function ChipFace(") : source.index("function StageRow(")]
    code = """
    const vm = require('vm');
    const scope = {
      LEVEL_COLOR: {alert: 'red'}, LEVEL_TO_TONE: {alert: 'bad'},
      levelOf: d => d.level, blockedOf: d => 0, hangIdleOf: d => 0,
      jsx: (type, props) => ({type, ...props}), jsxs: (type, props) => ({type, ...props}),
      StatusDot: 'dot', SpendBar: 'bar', SHIELD: 'shield', fmtIdle: s => s
    };
    vm.createContext(scope);
    vm.runInContext(SOURCE, scope);
    console.log(JSON.stringify(scope.ChipFace({data: {
      level: 'alert', stages: {watch: {level: 'ok'}}, spend_ratio: 1, overdue_turns: 0
    }, loading: false})));
    """.replace("SOURCE", json.dumps(face))
    result = subprocess.run([node, "-e", code], capture_output=True, text=True, check=True)
    assert '"hang"' not in result.stdout
