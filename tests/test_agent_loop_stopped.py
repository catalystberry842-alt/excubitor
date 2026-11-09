"""agent_loop_stopped closes the open turn using the kwargs Hermes actually passes."""

import time


def _cfg(cfg, monkeypatch):
    monkeypatch.setattr(
        cfg,
        "settings",
        lambda: {**cfg.DEFAULTS, "notifier": "log", "enabled": True, "watch_interval_seconds": 5},
    )


def test_stop_matches_gateway_session_key(modules, monkeypatch):
    watch, ledger, cfg = modules["watch"], modules["ledger"], modules["config"]
    _cfg(cfg, monkeypatch)
    alerts = []
    monkeypatch.setattr(watch, "send_alert", lambda *a, **k: alerts.append(k) or True)

    key = "agent:main:telegram:dm:42"
    ledger.open_turn("t-stop-1", platform="telegram", chat_id="42", meta={"session_key": key})
    ledger.open_turn("t-other", platform="telegram", chat_id="43", meta={"session_key": "agent:main:telegram:dm:43"})

    # Exactly what gateway/run_agent_cache.py passes upstream (no session_id).
    watch.on_agent_loop_stopped(
        session_key=key,
        platform="telegram",
        reason="user_stop",
        invalidation_reason="session_interrupt",
    )

    stopped = ledger.get_turn("t-stop-1")
    assert stopped["status"] == "stopped"
    assert "user_stop" in (stopped.get("last_reason") or "")
    assert ledger.get_turn("t-other")["status"] == "open"

    with ledger._tx() as conn:
        conn.execute("UPDATE turns SET deadline_at = ? WHERE turn_id = 't-stop-1'", (time.time() - 1,))
    assert "t-stop-1" not in watch.Watcher().scan_once(cfg.settings())


def test_stop_matches_bound_turn_by_session_key(modules, monkeypatch):
    watch, ledger, cfg = modules["watch"], modules["ledger"], modules["config"]
    _cfg(cfg, monkeypatch)
    key = "agent:main:discord:chan:9"
    ledger.open_turn("t-bound", session_id="sess-9", meta={"session_key": key})
    watch.on_agent_loop_stopped(session_key=key, platform="discord", reason="new_session", invalidation_reason="")
    assert ledger.get_turn("t-bound")["status"] == "stopped"


def test_stop_session_id_fallback(modules, monkeypatch):
    watch, ledger, cfg = modules["watch"], modules["ledger"], modules["config"]
    _cfg(cfg, monkeypatch)
    ledger.open_turn("t-legacy", session_id="s-legacy")
    watch.on_agent_loop_stopped(session_id="s-legacy", reason="interrupted")
    assert ledger.get_turn("t-legacy")["status"] == "stopped"


def test_stop_unknown_key_is_noop(modules, monkeypatch):
    watch, ledger, cfg = modules["watch"], modules["ledger"], modules["config"]
    _cfg(cfg, monkeypatch)
    ledger.open_turn("t-keep", meta={"session_key": "a"})
    watch.on_agent_loop_stopped(session_key="b", platform="tui", reason="user_stop", invalidation_reason="x")
    assert ledger.get_turn("t-keep")["status"] == "open"
