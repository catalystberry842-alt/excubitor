"""Duplicate inbound replay → skip; turns open only after admission."""

import time
from types import SimpleNamespace


def _event(message_id="m-1", text="hello", internal=False, platform="telegram", chat_id="42"):
    plat = SimpleNamespace(value=platform) if platform == "telegram" else platform
    return SimpleNamespace(
        message_id=message_id,
        text=text,
        raw_message={"id": message_id, "text": text} if message_id else text,
        internal=internal,
        source=SimpleNamespace(platform=plat, chat_id=chat_id),
    )


def test_duplicate_message_skipped(modules):
    intake = modules["intake"]
    ledger = modules["ledger"]

    event = _event()
    first = intake.on_pre_gateway_dispatch(event=event)
    assert first is None
    assert ledger.summarize()["duplicates_dropped"] == 0
    # Unapproved senders never reach admission, so pre-dispatch must not mark seen.
    assert ledger.find_open_unbound_turn() is None

    assert intake.on_post_gateway_admission(event=event, session_key="sk1") is None
    assert ledger.find_open_unbound_turn() is not None

    second = intake.on_pre_gateway_dispatch(event=event)
    assert isinstance(second, dict)
    assert second["action"] == "skip"
    assert "duplicate" in second["reason"]
    assert ledger.summarize()["duplicates_dropped"] >= 1


def test_missing_message_id_falls_back_to_raw_hash(modules):
    intake = modules["intake"]
    event = SimpleNamespace(
        message_id=None,
        text="same body",
        raw_message="same body",
        internal=False,
        source=SimpleNamespace(platform="discord", chat_id="c1"),
    )
    assert intake.on_pre_gateway_dispatch(event=event) is None
    assert intake.on_post_gateway_admission(event=event) is None
    again = intake.on_pre_gateway_dispatch(event=event)
    assert again and again["action"] == "skip"


def test_text_only_dedup_expires_after_short_window(modules):
    intake = modules["intake"]
    ledger = modules["ledger"]
    hashing = modules["hashing"]
    event = SimpleNamespace(
        message_id=None,
        text="yes",
        raw_message="yes",
        internal=False,
        source=SimpleNamespace(platform="telegram", chat_id="9"),
    )
    assert intake.on_pre_gateway_dispatch(event=event) is None
    assert intake.on_post_gateway_admission(event=event) is None
    key = hashing.message_key("telegram", "9", None, raw="yes")
    assert ledger.already_seen(key)
    with ledger._tx() as conn:
        conn.execute(
            "UPDATE seen_messages SET first_seen_at = ? WHERE message_key = ?",
            (time.time() - 200, key),
        )
    assert ledger.already_seen(key) is False
    assert intake.on_pre_gateway_dispatch(event=event) is None


def test_internal_event_not_touched(modules):
    intake = modules["intake"]
    event = SimpleNamespace(
        message_id="internal-1",
        text="wake",
        raw_message=None,
        internal=True,
        source=SimpleNamespace(platform="telegram", chat_id="1"),
    )
    assert intake.on_pre_gateway_dispatch(event=event) is None
    # Not marked seen — a later non-internal with same id would still pass first time
    assert modules["ledger"].already_seen("telegram:1:internal-1") is False


def test_gateway_flow_closes_without_false_hang_alert(modules, monkeypatch):
    """pre_gateway_dispatch → post_gateway_admission → post_llm_call must not alert.

    Core looks the turn up by its own turn_id, which differs from the gw: ledger id.
    """
    intake = modules["intake"]
    spend = modules["spend"]
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
        lambda c, title="", body="", payload=None: (
            alerts.append({"title": title, "body": body, "payload": payload}) or True
        ),
    )

    event = _event(message_id="m-gw", text="hello there")
    assert intake.on_pre_gateway_dispatch(event=event) is None
    assert intake.on_post_gateway_admission(event=event, session_key="gw-sk") is None
    opened = ledger.find_open_unbound_turn()
    assert opened is not None

    # The host supplies a task-local route key, not an arbitrary newest turn.
    from hermes_cli import plugin_session
    monkeypatch.setattr(plugin_session, "session_key", lambda: "gw-sk")
    spend.on_pre_llm_call(session_id="agent-sess", turn_id="core-turn-99")
    spend.on_post_llm_call(session_id="agent-sess", turn_id="core-turn-99")

    with ledger._tx() as conn:
        conn.execute("UPDATE turns SET deadline_at = ? WHERE status = 'open'", (time.time() - 1,))

    fired = watch.Watcher().scan_once(cfg.settings())
    assert fired == []
    assert alerts == []
    closed = ledger.get_turn(str(opened["turn_id"]))
    assert closed is not None
    assert closed["status"] != "open"
