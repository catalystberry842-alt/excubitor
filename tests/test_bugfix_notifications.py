"""Behavior regressions from the November audit: notifications."""

import concurrent.futures
import threading
from types import SimpleNamespace


def event(mid="one", chat="A"):
    return SimpleNamespace(
        message_id=mid,
        text="hello",
        raw_message="hello",
        internal=False,
        source=SimpleNamespace(platform="telegram", chat_id=chat),
    )


def test_receipts_send_once_on_replay_and_repeated_completion(modules, monkeypatch):
    ledger, s, r, i = [modules[k] for k in ("ledger", "spend", "receipt", "intake")]
    sent = []
    monkeypatch.setattr(r, "send_alert", lambda *a, **k: sent.append(k) or True)
    e = event()
    i.on_pre_gateway_dispatch(event=e)
    i.on_post_gateway_admission(event=e, session_key="A")
    tid = ledger.find_open_unbound_turn()["turn_id"]
    monkeypatch.setenv("HERMES_SESSION_KEY", "A")
    s.on_pre_llm_call(session_id="s", turn_id="core")
    s.on_post_llm_call(session_id="s", turn_id="core")
    for _ in range(5):
        i.on_pre_gateway_dispatch(event=event())
        s.on_post_llm_call(session_id="s", turn_id="core")
    assert len(sent) == 1
    assert ledger.get_receipt(tid)["notified"]


def test_racing_pre_dispatch_only_one_runs_and_admitted_only_ledger(modules):
    ledger, i = modules["ledger"], modules["intake"]
    barrier = threading.Barrier(2)

    def deliver(_):
        barrier.wait()
        return i.on_pre_gateway_dispatch(event=event())

    ledger._conn()  # initialize before starting workers
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(deliver, range(2)))
    assert sum(r is None for r in results) == 1
    assert ledger.summarize()["duplicates_dropped"] == 1
    assert ledger.find_open_unbound_turn() is None
    assert not ledger.already_seen(modules["hashing"].message_key("telegram", "A", "one", "hello"))
    i.on_post_gateway_admission(event=event(), session_key="A")
    i.on_post_gateway_admission(event=event(), session_key="A")
    assert ledger.summarize()["duplicates_dropped"] == 1  # admission is not a veto


def test_two_watchers_claim_before_notifying(modules, monkeypatch):
    ledger, w = modules["ledger"], modules["watch"]
    ledger.open_turn("t")
    with ledger._tx() as c:
        c.execute("UPDATE turns SET deadline_at=0")
    stale = ledger.overdue_open_turns()
    monkeypatch.setattr(ledger, "overdue_open_turns", lambda: stale)
    sent = []
    monkeypatch.setattr(w, "send_alert", lambda *a, **k: sent.append(k) or True)
    w.Watcher().scan_once()
    w.Watcher().scan_once()
    assert len(sent) == 1
    assert ledger.summarize()["alerts"] == 1


def test_receipt_claim_is_atomic_across_threads(modules, monkeypatch):
    ledger, receipt = modules["ledger"], modules["receipt"]
    ledger.open_turn("r")
    ledger.add_turn_event("r", "veto", reason="cap")
    sent = []
    monkeypatch.setattr(receipt, "send_alert", lambda *a, **k: sent.append(k) or True)
    barrier = threading.Barrier(2)
    original = ledger.save_receipt

    def store_then_wait(*a, **k):
        original(*a, **k)
        barrier.wait(timeout=10)

    monkeypatch.setattr(ledger, "save_receipt", store_then_wait)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        texts = list(pool.map(receipt.finalize, ["r", "r"]))
    assert all(texts)
    assert len(sent) == 1


def test_hang_receipt_replay_does_not_send_second_alert(modules, monkeypatch):
    ledger, receipt, watch = modules["ledger"], modules["receipt"], modules["watch"]
    ledger.open_turn("t")
    with ledger._tx() as c:
        c.execute("UPDATE turns SET deadline_at=0")
    sent = []
    monkeypatch.setattr(watch, "send_alert", lambda *a, **k: sent.append(k) or True)
    monkeypatch.setattr(receipt, "send_alert", lambda *a, **k: sent.append(k) or True)
    watch.Watcher().scan_once()
    receipt.finalize("t")
    assert len(sent) == 1


def test_dispatch_lease_expires_without_admitting_sender(modules):
    ledger, intake = modules["ledger"], modules["intake"]
    assert intake.on_pre_gateway_dispatch(event=event()) is None
    with ledger._tx() as c:
        c.execute("UPDATE dispatch_claims SET expires_at=0")
    assert intake.on_pre_gateway_dispatch(event=event()) is None
    assert ledger.find_open_unbound_turn() is None
    assert ledger._conn().execute("SELECT COUNT(*) FROM seen_messages").fetchone()[0] == 0
