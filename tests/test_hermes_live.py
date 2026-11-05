"""Integration against real Hermes fire-sites (gateway mixin + AIAgent).

Gateway dedup always runs when Hermes is importable.
The OpenRouter agent turn is skipped unless OPENROUTER_API_KEY is set.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _find_hermes() -> Path | None:
    env = (os.environ.get("HERMES_AGENT_ROOT") or os.environ.get("HERMES_ROOT") or "").strip()
    candidates = []
    if env:
        candidates.append(Path(env).expanduser())
    candidates.extend(
        [
            Path("/workspace/hermes-agent"),
            Path("/workspace/recheck/hermes-agent-fresh"),
            ROOT.parent / "hermes-agent",
            Path.cwd() / "hermes-agent",
        ]
    )
    for cand in candidates:
        if (cand / "run_agent.py").exists() or (cand / "hermes_cli").is_dir():
            return cand.resolve()
    return None


HERMES = _find_hermes() or Path("/workspace/hermes-agent")

pytestmark = pytest.mark.skipif(
    HERMES is None or not HERMES.exists(),
    reason="Hermes checkout missing (set HERMES_AGENT_ROOT)",
)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    home.mkdir()
    (home / "plugins").mkdir()
    (home / "plugins" / "excubitor").symlink_to(ROOT)
    (home / "config.yaml").write_text("plugins:\n  enabled:\n    - excubitor\n  disabled: []\n")
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        (home / ".env").write_text(f"OPENROUTER_API_KEY={key}\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    # Ensure hermes is importable
    if str(HERMES) not in sys.path:
        sys.path.insert(0, str(HERMES))
    # Reset plugin manager singleton(s)
    import hermes_cli.plugins as plugins_mod

    for attr in ("_MANAGER", "_manager", "manager"):
        if hasattr(plugins_mod, attr):
            try:
                setattr(plugins_mod, attr, None)
            except Exception:
                pass
    yield home


def _load_plugins():
    from hermes_cli.plugins import PluginManager, get_plugin_manager

    try:
        mgr = get_plugin_manager()
    except Exception:
        mgr = PluginManager()
    mgr.discover_and_load()
    return mgr


def test_gateway_dedup_via_real_hm_hook(hermes_home):
    mgr = _load_plugins()
    assert mgr.has_hook("pre_gateway_dispatch")

    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session import SessionSource

    config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True)})
    runner = object.__new__(GatewayRunner)
    runner.config = config
    runner.adapters = {Platform.TELEGRAM: SimpleNamespace(send=AsyncMock())}
    runner.pairing_store = MagicMock()
    runner.session_store = MagicMock()
    runner._running_agents = {}

    source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c-live",
        user_name="t",
        chat_type="dm",
    )
    event = MessageEvent(
        text="hello live",
        message_id="live-dup-1",
        source=source,
        raw_message={"id": "live-dup-1"},
    )

    first = asyncio.run(runner._hm_pre_gateway_dispatch_hook(event, source))
    second = asyncio.run(runner._hm_pre_gateway_dispatch_hook(event, source))
    assert first is not None
    assert second is None

    db = hermes_home / "plugin-data" / "excubitor" / "data.db"
    assert db.exists()
    import sqlite3

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT value FROM stats WHERE key='duplicates_dropped'").fetchone()[0] >= 1
    conn.close()


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set")
def test_agent_hooks_fire_via_real_aigent(hermes_home):
    mgr = _load_plugins()
    for name in (
        "pre_llm_call",
        "post_llm_call",
        "post_api_request",
        "pre_tool_call",
        "post_tool_call",
    ):
        assert mgr.has_hook(name), name

    from hermes_cli import lifecycle
    from run_agent import AIAgent

    counts = {
        k: 0
        for k in (
            "pre_llm_call",
            "post_llm_call",
            "post_api_request",
            "pre_tool_call",
            "post_tool_call",
        )
    }
    orig = lifecycle.invoke_hook

    def counting(name, *a, **kw):
        if name in counts:
            counts[name] += 1
        return orig(name, *a, **kw)

    lifecycle.invoke_hook = counting  # type: ignore
    try:
        agent = AIAgent(
            provider="openrouter",
            model="openai/gpt-4o-mini",
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
            enabled_toolsets=["terminal"],
            max_iterations=4,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            skip_background_review=True,
        )
        agent.run_conversation(
            "Call terminal once with `echo excubitor-pytest` then reply OK only."
        )
    finally:
        lifecycle.invoke_hook = orig  # type: ignore

    assert counts["pre_llm_call"] >= 1
    assert counts["post_api_request"] >= 1
    assert counts["post_llm_call"] >= 1
    assert counts["pre_tool_call"] >= 1
    assert counts["post_tool_call"] >= 1

    import sqlite3

    db = hermes_home / "plugin-data" / "excubitor" / "data.db"
    conn = sqlite3.connect(db)
    tools = conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]
    cost = conn.execute("SELECT SUM(cost_usd) FROM turns").fetchone()[0] or 0
    conn.close()
    assert tools >= 1
    assert cost > 0
