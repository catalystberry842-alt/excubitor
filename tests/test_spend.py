"""Spend tracking and cap veto."""

import pytest


def test_post_api_request_adds_cost(modules, monkeypatch):
    spend = modules["spend"]
    ledger = modules["ledger"]
    cfg = modules["config"]
    monkeypatch.setattr(cfg, "settings", lambda: {**cfg.DEFAULTS, "enabled": True})
    turn_id = "t-spend-1"
    ledger.open_turn(turn_id, session_id="sess-a", deadline_minutes=5)
    spend.on_post_api_request(
        session_id="sess-a",
        turn_id=turn_id,
        model="gpt-4o-mini",
        usage={"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
    )
    turn = ledger.get_turn(turn_id)
    # gpt-4o-mini: 0.15 + 0.6 = 0.75
    assert turn["cost_usd"] == pytest.approx(0.75, rel=1e-3)


def test_pre_llm_call_vetoes_over_day_cap(modules, monkeypatch):
    spend = modules["spend"]
    ledger = modules["ledger"]
    cfg = modules["config"]
    monkeypatch.setattr(
        cfg,
        "settings",
        lambda: {
            **cfg.DEFAULTS,
            "day_budget_usd": 0.5,
            "session_budget_usd": 10.0,
            "enabled": True,
        },
    )
    turn_id = "t-spend-2"
    ledger.open_turn(turn_id, session_id="sess-b", deadline_minutes=5)
    ledger.add_cost(turn_id, 0.6, session_id="sess-b")
    result = spend.on_pre_llm_call(session_id="sess-b", turn_id="t-spend-new")
    assert result and result["action"] == "block"
    assert "day budget" in result["message"]
    assert ledger.summarize()["cap_vetoes"] >= 1


def test_pre_tool_call_blocks_mid_turn(modules, monkeypatch):
    spend = modules["spend"]
    ledger = modules["ledger"]
    cfg = modules["config"]
    monkeypatch.setattr(
        cfg,
        "settings",
        lambda: {
            **cfg.DEFAULTS,
            "day_budget_usd": 10.0,
            "session_budget_usd": 0.1,
            "enabled": True,
        },
    )
    turn_id = "t-spend-3"
    ledger.open_turn(turn_id, session_id="sess-c", deadline_minutes=5)
    ledger.add_cost(turn_id, 0.2, session_id="sess-c")
    result = spend.on_pre_tool_call_spend(
        tool_name="shell", args={}, session_id="sess-c", turn_id=turn_id
    )
    assert result and result["action"] == "block"
