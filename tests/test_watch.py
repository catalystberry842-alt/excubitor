"""Hang alert when deadline passes with no reply."""

import time


def test_hang_alert_fires_once(modules, monkeypatch):
    watch = modules["watch"]
    ledger = modules["ledger"]
    cfg = modules["config"]
    alerts = []

    monkeypatch.setattr(
        cfg,
        "settings",
        lambda: {**cfg.DEFAULTS, "notifier": "log", "enabled": True, "watch_interval_seconds": 5},
    )
    monkeypatch.setattr(
        watch,
        "send_alert",
        lambda c, title="", body="", payload=None: alerts.append({"title": title, "body": body, "payload": payload}) or True,
    )

    turn_id = "t-hang-1"
    ledger.open_turn(turn_id, session_id="s-hang", deadline_minutes=5)
    with ledger._tx() as conn:
        conn.execute(
            "UPDATE turns SET deadline_at = ?, loop_count = 6, cost_usd = 0.40, last_reason = ? WHERE turn_id = ?",
            (time.time() - 1, "stuck after 6 search loops", turn_id),
        )

    fired = watch.Watcher().scan_once(cfg.settings())
    assert turn_id in fired
    assert len(alerts) == 1
    assert "no reply" in alerts[0]["body"]
    assert "0.40" in alerts[0]["body"]

    fired2 = watch.Watcher().scan_once(cfg.settings())
    assert fired2 == []
    assert len(alerts) == 1
    assert ledger.summarize()["alerts"] >= 1


def test_reply_clears_hang(modules, monkeypatch):
    watch = modules["watch"]
    spend = modules["spend"]
    ledger = modules["ledger"]
    cfg = modules["config"]
    monkeypatch.setattr(cfg, "settings", lambda: {**cfg.DEFAULTS, "enabled": True})
    turn_id = "t-hang-2"
    ledger.open_turn(turn_id, session_id="s-hang-2", deadline_minutes=5)
    with ledger._tx() as conn:
        conn.execute("UPDATE turns SET deadline_at = ? WHERE turn_id = ?", (time.time() - 1, turn_id))
    spend.on_post_llm_call(session_id="s-hang-2", turn_id=turn_id)
    fired = watch.Watcher().scan_once(cfg.settings())
    assert fired == []
