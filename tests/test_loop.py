"""No-progress loop detection (same tool+args, no new results)."""


def test_identical_tool_search_loop_blocks(modules, monkeypatch):
    work = modules["work"]
    ledger = modules["ledger"]
    cfg = modules["config"]

    monkeypatch.setattr(
        cfg,
        "settings",
        lambda: {
            **cfg.DEFAULTS,
            "loop_threshold": 3,
            "loop_approve_first": False,
            "enabled": True,
            "day_budget_usd": 100.0,
            "session_budget_usd": 100.0,
        },
    )
    turn_id = "t-loop-1"
    ledger.open_turn(turn_id, session_id="s1", deadline_minutes=5)

    args = {"query": "recon outcome get read list"}
    # Simulate: call → same result → call → same result → call (trip)
    for i in range(3):
        d = work.evaluate_loop(turn_id, "tool_search", args, session_id="s1", cfg=cfg.settings())
        # Attach identical result after each pre (except we evaluate before post)
        work.on_post_tool_call(
            tool_name="tool_search",
            args=args,
            result='{"hits": ["a", "b"]}',
            turn_id=turn_id,
            session_id="s1",
        )
        if i < 2:
            assert d is None, f"early trip at {i}: {d}"
    # Next pre after 3 identical results should block (stagnant grows)
    # After 3 posts with same hash, stagnant >= 2; threshold 3 needs another
    d = work.evaluate_loop(turn_id, "tool_search", args, session_id="s1", cfg=cfg.settings())
    # Force one more cycle if needed
    if d is None:
        work.on_post_tool_call(
            tool_name="tool_search",
            args=args,
            result='{"hits": ["a", "b"]}',
            turn_id=turn_id,
            session_id="s1",
        )
        d = work.evaluate_loop(turn_id, "tool_search", args, session_id="s1", cfg=cfg.settings())
    assert d is not None
    assert d["action"] == "block"
    assert "no-progress" in d["message"]
    assert ledger.summarize()["loops_stopped"] >= 1


def test_new_results_are_progress(modules, monkeypatch):
    work = modules["work"]
    ledger = modules["ledger"]
    cfg = modules["config"]
    monkeypatch.setattr(
        cfg,
        "settings",
        lambda: {**cfg.DEFAULTS, "loop_threshold": 3, "loop_approve_first": False, "enabled": True},
    )
    turn_id = "t-loop-2"
    ledger.open_turn(turn_id, deadline_minutes=5)
    args = {"query": "x"}
    for i in range(5):
        d = work.evaluate_loop(turn_id, "web_search", args, cfg=cfg.settings())
        work.on_post_tool_call(
            tool_name="web_search",
            args=args,
            result=f"result-version-{i}",
            turn_id=turn_id,
        )
        assert d is None, f"false positive at {i}: {d}"


def test_approve_first_then_block(modules, monkeypatch):
    work = modules["work"]
    ledger = modules["ledger"]
    cfg = modules["config"]
    monkeypatch.setattr(
        cfg,
        "settings",
        lambda: {
            **cfg.DEFAULTS,
            "loop_threshold": 2,
            "loop_approve_first": True,
            "enabled": True,
            "day_budget_usd": 100.0,
            "session_budget_usd": 100.0,
        },
    )
    turn_id = "t-loop-3"
    ledger.open_turn(turn_id, deadline_minutes=5)
    args = {"path": "/tmp/x"}
    # Seed two identical completed calls
    for _ in range(2):
        work.evaluate_loop(turn_id, "read_file", args, cfg=cfg.settings())
        work.on_post_tool_call(tool_name="read_file", args=args, result="SAME", turn_id=turn_id)
    first = work.evaluate_loop(turn_id, "read_file", args, cfg=cfg.settings())
    assert first and first["action"] == "approve"
    second = work.evaluate_loop(turn_id, "read_file", args, cfg=cfg.settings())
    assert second and second["action"] == "block"


def test_spend_tightens_threshold(modules):
    work = modules["work"]
    assert work.effective_loop_threshold(6, 0.0) == 6
    assert work.effective_loop_threshold(6, 1.0) == 2
    assert work.effective_loop_threshold(6, 0.5) == 4
