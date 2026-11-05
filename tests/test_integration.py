"""End-to-end-ish flow across intake → work → spend → watch on one ledger."""

import time
from types import SimpleNamespace


def test_register_wires_hooks(modules):
    pkg = modules["pkg"]
    registered = {"hooks": [], "commands": []}

    class FakeCtx:
        def register_hook(self, name, fn):
            registered["hooks"].append(name)

        def register_command(self, name, handler, description=""):
            registered["commands"].append(name)

        def get_config(self, key, default=None):
            return default

    pkg.register(FakeCtx())
    for needed in (
        "pre_gateway_dispatch",
        "pre_llm_call",
        "post_llm_call",
        "pre_tool_call",
        "post_tool_call",
        "post_api_request",
        "agent_loop_stopped",
    ):
        assert needed in registered["hooks"], registered
    assert "excubitor" in registered["commands"]
    assert "watch" in registered["commands"]


def test_full_ledger_story(modules, monkeypatch):
    intake = modules["intake"]
    work = modules["work"]
    spend = modules["spend"]
    watch = modules["watch"]
    ledger = modules["ledger"]
    cfg = modules["config"]
    cmd = modules["command"]

    monkeypatch.setattr(
        cfg,
        "settings",
        lambda: {
            **cfg.DEFAULTS,
            "loop_threshold": 3,
            "loop_approve_first": False,
            "day_budget_usd": 5.0,
            "session_budget_usd": 2.0,
            "enabled": True,
            "notifier": "log",
        },
    )
    alerts = []
    monkeypatch.setattr(
        watch,
        "send_alert",
        lambda c, title="", body="", payload=None: alerts.append(body) or True,
    )

    event = SimpleNamespace(
        message_id="story-1",
        text="do the thing",
        raw_message={"id": "story-1"},
        internal=False,
        source=SimpleNamespace(platform=SimpleNamespace(value="telegram"), chat_id="99"),
    )
    assert intake.on_pre_gateway_dispatch(event=event) is None
    assert intake.on_pre_gateway_dispatch(event=event)["action"] == "skip"

    # Bind a session turn and burn some money + loop
    turn_id = "story-turn"
    ledger.open_turn(
        turn_id, session_id="story-sess", platform="telegram", chat_id="99", deadline_minutes=5
    )
    spend.on_post_api_request(
        session_id="story-sess",
        turn_id=turn_id,
        model="gpt-4o-mini",
        usage={"prompt_tokens": 500_000, "completion_tokens": 100_000},
    )
    args = {"query": "find variance"}
    blocked = None
    for _ in range(6):
        blocked = work.evaluate_loop(
            turn_id, "tool_search", args, session_id="story-sess", cfg=cfg.settings()
        )
        work.on_post_tool_call(
            tool_name="tool_search",
            args=args,
            result='{"hits":[1,2,3]}',
            turn_id=turn_id,
            session_id="story-sess",
        )
        if blocked and blocked.get("action") == "block":
            break
    assert blocked and blocked["action"] == "block"

    with ledger._tx() as conn:
        conn.execute(
            "UPDATE turns SET deadline_at = ? WHERE turn_id = ?", (time.time() - 1, turn_id)
        )
    watch.Watcher().scan_once(cfg.settings())
    assert alerts, "expected hang alert"
    assert "tool_search" in alerts[0] or "loop" in alerts[0] or "no reply" in alerts[0]

    text = cmd.status_text()
    assert "duplicates dropped" in text.lower() or "Duplicates" in text or "duplicates" in text
    summary = ledger.summarize()
    assert summary["duplicates_dropped"] >= 1
    assert summary["loops_stopped"] >= 1
    assert summary["alerts"] >= 1


def test_status_command_failopen(modules, monkeypatch):
    cmd = modules["command"]
    monkeypatch.setattr(
        modules["ledger"],
        "summarize",
        lambda: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    assert "unavailable" in cmd.status_text().lower()
