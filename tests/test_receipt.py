"""Turn receipts: build, store, expose, and notify only on notable turns."""

import time


def _cfg(cfg, monkeypatch, **over):
    s = {**cfg.DEFAULTS, "enabled": True, "notifier": "log", **over}
    monkeypatch.setattr(cfg, "settings", lambda: s)
    return s


def _capture(modules, monkeypatch):
    sent = []
    monkeypatch.setattr(
        modules["receipt"],
        "send_alert",
        lambda c, title="", body="", payload=None: (
            sent.append({"title": title, "body": body, "payload": payload}) or True
        ),
    )
    return sent


def test_loop_block_receipt_notifies(modules, monkeypatch):
    m = modules
    _cfg(m["config"], monkeypatch, loop_threshold=6, loop_approve_first=False)
    sent = _capture(m, monkeypatch)
    led, pkg = m["ledger"], m["pkg"]
    tid = "t_ab12"
    led.open_turn(tid, platform="telegram", session_id="s1")
    led.add_cost(tid, 0.42, session_id="s1")
    directive = None
    for _ in range(6):
        directive = pkg.on_pre_tool_call("web_search", {"q": "x"}, turn_id=tid, session_id="s1")
        if directive:
            break
        m["work"].on_post_tool_call("web_search", {"q": "x"}, "same", turn_id=tid, session_id="s1")
    assert directive and directive["action"] == "block"
    m["spend"].on_post_llm_call(session_id="s1", turn_id=tid)
    r = led.get_receipt(tid)
    print("\nSAMPLE RECEIPT:", r["text"])
    assert r["text"].startswith("Turn t_ab12 · telegram · 6 tool calls")
    assert "looped 6× on web_search, blocked at #6" in r["text"]
    assert "spent $0.42 of $5 day cap" in r["text"]
    assert r["notable"] and r["notified"]
    assert len(sent) == 1 and sent[0]["payload"]["kind"] == "receipt"


def test_clean_turn_stays_quiet(modules, monkeypatch):
    m = modules
    _cfg(m["config"], monkeypatch)
    sent = _capture(m, monkeypatch)
    m["ledger"].open_turn("t_clean", platform="cli", session_id="s2")
    m["spend"].on_post_llm_call(session_id="s2", turn_id="t_clean")
    r = m["ledger"].get_receipt("t_clean")
    assert r and not r["notable"] and "replied" in r["text"]
    assert sent == []


def test_hang_receipt_stored_without_second_ping(modules, monkeypatch):
    m = modules
    s = _cfg(m["config"], monkeypatch)
    sent = _capture(m, monkeypatch)
    hang_alerts = []
    monkeypatch.setattr(m["watch"], "send_alert", lambda *a, **k: hang_alerts.append(k) or True)
    led = m["ledger"]
    led.open_turn("t_hang", platform="discord", session_id="s3")
    with led._tx() as conn:
        conn.execute(
            "UPDATE turns SET deadline_at = ? WHERE turn_id = 't_hang'", (time.time() - 1,)
        )
    m["watch"].Watcher().scan_once(s)
    r = led.get_receipt("t_hang")
    print("\nHANG RECEIPT:", r["text"])
    assert "went quiet" in r["text"] and "alerted" in r["text"]
    assert r["notable"] and len(hang_alerts) == 1 and sent == []


def test_veto_and_dedup_in_receipt(modules, monkeypatch):
    m = modules
    _cfg(m["config"], monkeypatch, day_budget_usd=0.1)
    sent = _capture(m, monkeypatch)
    led = m["ledger"]
    led.open_turn("t_v", session_id="s4", message_key="k1")
    led.add_cost("t_v", 0.2, session_id="s4")
    out = m["spend"].on_pre_tool_call_spend("terminal", {}, turn_id="t_v", session_id="s4")
    assert out["action"] == "block"
    m["spend"].on_post_llm_call(session_id="s4", turn_id="t_v")
    m["intake"]._note_dup("k1")  # late duplicate re-finalizes the closed turn
    r = led.get_receipt("t_v")
    assert "cap veto on tool terminal" in r["text"]
    assert "dropped 1 duplicate" in r["text"]
    assert len(sent) == 2


def test_command_and_status_api(modules, monkeypatch):
    m = modules
    _cfg(m["config"], monkeypatch)
    assert "no turn receipts yet" in m["command"].status_text("receipt")
    m["ledger"].open_turn("t_cmd_123", session_id="s5")
    m["spend"].on_post_llm_call(session_id="s5", turn_id="t_cmd_123")
    assert "Turn t_cmd_123" in m["command"].status_text("receipt")
    assert "Turn t_cmd_123" in m["command"].status_text("receipt t_cmd")
    assert "no receipt for turn nope" in m["command"].status_text("receipt nope")
    assert "last receipt:" in m["command"].status_text("")
    snap = m["ledger"].snapshot_for_ui()
    assert snap["last_receipt"]["turn_id"] == "t_cmd_123"


def test_receipt_fail_open(modules, monkeypatch):
    m = modules
    _cfg(m["config"], monkeypatch)
    monkeypatch.setattr(
        m["ledger"], "get_turn", lambda *_: (_ for _ in ()).throw(RuntimeError("x"))
    )
    assert m["receipt"].finalize("t_x") is None
    assert m["ledger"].snapshot_for_ui().get("last_receipt") is None
