"""Edge cases: missing message_id, huge args, clock skew, retention."""

import time


def test_missing_message_id_falls_back_to_raw_hash(modules):
    intake = modules["intake"]
    ledger = modules["ledger"]

    class Ev:
        message_id = None
        text = "hello-unique-raw-xyz"
        raw_message = None
        source = type("S", (), {"platform": type("P", (), {"value": "cli"})(), "chat_id": "c1"})()

    assert intake.on_pre_gateway_dispatch(event=Ev()) is None
    assert intake.on_post_gateway_admission(event=Ev()) is None
    out = intake.on_pre_gateway_dispatch(event=Ev())
    assert isinstance(out, dict) and out.get("action") == "skip"
    assert ledger.summarize()["duplicates_dropped"] >= 1


def test_huge_args_hash_stable(modules):
    hashing = modules["hashing"]
    huge = {"data": "x" * 200_000, "nested": {"a": list(range(1000))}}
    h1 = hashing.args_hash(huge)
    h2 = hashing.args_hash(dict(huge))
    assert h1 == h2
    assert isinstance(h1, str) and len(h1) >= 16


def test_clock_skew_overdue_uses_deadline_at(modules, monkeypatch):
    ledger = modules["ledger"]
    watch = modules["watch"]
    cfg = modules["config"]
    alerts = []
    monkeypatch.setattr(
        watch,
        "send_alert",
        lambda c, title="", body="", payload=None: alerts.append(title) or True,
    )
    turn_id = "skew-1"
    ledger.open_turn(turn_id, session_id="skew", deadline_minutes=60)
    with ledger._tx() as conn:
        conn.execute(
            "UPDATE turns SET opened_at = ?, deadline_at = ? WHERE turn_id = ?",
            (time.time(), time.time() - 10, turn_id),
        )
    fired = watch.Watcher().scan_once({**cfg.DEFAULTS, "notifier": "log", "enabled": True})
    assert turn_id in fired
    assert alerts
    assert ledger.get_turn(turn_id)["alerts_fired"] >= 1


def test_retention_prunes_old_seen(modules):
    ledger = modules["ledger"]
    key = "old:platform:chat:mid"
    ledger.mark_seen(key, platform="cli", chat_id="c", message_id="m")
    with ledger._tx() as conn:
        conn.execute(
            "UPDATE seen_messages SET first_seen_at = ? WHERE message_key = ?",
            (time.time() - 900_000, key),
        )
    deleted = ledger.prune_old(seen_ttl_hours=1.0, turn_ttl_hours=1.0)
    assert deleted.get("seen", 0) >= 1
    assert not ledger.already_seen(key)
