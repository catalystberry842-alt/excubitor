"""Fail-open: internal errors never raise into Hermes."""

import pytest


def test_intake_failopen_on_bad_event(modules):
    intake = modules["intake"]
    # Completely broken event object
    assert intake.on_pre_gateway_dispatch(event=object()) is None


def test_hooks_wrapped_never_raise(modules, monkeypatch):
    pkg = modules["pkg"]
    ledger = modules["ledger"]

    def boom(*a, **k):
        raise RuntimeError("simulated")

    monkeypatch.setattr(ledger, "already_seen", boom)
    wrapped = pkg._safe(modules["intake"].on_pre_gateway_dispatch)
    assert wrapped(event=None) is None


def test_spend_bad_usage_failopen(modules, monkeypatch):
    spend = modules["spend"]
    cfg = modules["config"]
    monkeypatch.setattr(cfg, "settings", lambda: {**cfg.DEFAULTS, "enabled": True})
    assert spend.on_post_api_request(usage="not-a-dict", model=None, turn_id="x") is None


def test_evaluate_loop_with_corrupt_history(modules, monkeypatch):
    work = modules["work"]
    ledger = modules["ledger"]
    cfg = modules["config"]
    monkeypatch.setattr(cfg, "settings", lambda: {**cfg.DEFAULTS, "enabled": True})
    monkeypatch.setattr(
        ledger, "tool_history", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db"))
    )
    # Should not raise — evaluate_loop itself doesn't catch, but register path uses _safe.
    # Direct call: we still want ledger.record_tool_pre to fail open.
    monkeypatch.setattr(
        ledger, "record_tool_pre", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db"))
    )
    with pytest.raises(RuntimeError):
        # evaluate_loop is not itself fail-open; the registered wrapper is.
        work.evaluate_loop("t", "x", {}, cfg=cfg.settings())
    wrapped = modules["pkg"]._safe(
        lambda **kw: work.evaluate_loop("t", "x", {}, cfg=cfg.settings())
    )
    assert wrapped() is None
