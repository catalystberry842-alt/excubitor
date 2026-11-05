"""Duplicate inbound replay → skip."""

from types import SimpleNamespace


def test_duplicate_message_skipped(modules):
    intake = modules["intake"]
    ledger = modules["ledger"]

    event = SimpleNamespace(
        message_id="m-1",
        text="hello",
        raw_message={"id": "m-1", "text": "hello"},
        internal=False,
        source=SimpleNamespace(platform=SimpleNamespace(value="telegram"), chat_id="42"),
    )
    first = intake.on_pre_gateway_dispatch(event=event)
    assert first is None
    assert ledger.summarize()["duplicates_dropped"] == 0
    # Same message again
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
    again = intake.on_pre_gateway_dispatch(event=event)
    assert again and again["action"] == "skip"


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
