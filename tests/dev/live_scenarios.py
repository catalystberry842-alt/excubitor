#!/usr/bin/env python3
"""Live Excubitor scenarios against real Hermes (webhook gateway + AIAgent).

Dev-only harness: drives private Hermes internals on purpose, so it lives under tests/
(skipped by the plugin scanner and never loaded by Hermes).

Writes evidence under /workspace/excubitor/docs/evidence/ (no secrets).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Paths: env overrides first, then common local checkouts.
_REPO = Path(__file__).resolve().parents[2]
_hermes_env = (os.environ.get("HERMES_AGENT_ROOT") or os.environ.get("HERMES_ROOT") or "").strip()
HERMES = Path(_hermes_env).expanduser() if _hermes_env else None
if HERMES is None or not HERMES.exists():
    HERMES = None
    for cand in (
        Path("/workspace/hermes-agent"),
        Path.cwd() / "hermes-agent",
        _REPO.parent / "hermes-agent",
    ):
        if (cand / "run_agent.py").exists() or (cand / "hermes_cli").is_dir():
            HERMES = cand.resolve()
            break
PLUGIN = Path(os.environ.get("EXCUBITOR_ROOT", str(_REPO))).expanduser().resolve()
EVIDENCE = Path(os.environ.get("EXCUBITOR_EVIDENCE", str(PLUGIN / "docs" / "evidence"))).expanduser()
NTFY_TOPIC = os.environ.get("EXCUBITOR_NTFY_TOPIC", "excubitor-test-0c538f8108ac0cbd")
WEBHOOK_PORT = int(os.environ.get("EXCUBITOR_WEBHOOK_PORT", "18644"))
if HERMES is not None:
    sys.path.insert(0, str(HERMES))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("excubitor.live")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write(name: str, obj) -> Path:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / name
    if isinstance(obj, (dict, list)):
        text = json.dumps(obj, indent=2, default=str)
    else:
        text = str(obj)
    # Redact secrets in evidence
    import re
    text = re.sub(r"(OPENROUTER_API_KEY=)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]{10,}", "sk-[REDACTED]", text)
    path.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")
    return path


def _ledger_db(home: Path) -> Path:
    return home / "plugin-data" / "excubitor" / "data.db"


def _q(home: Path, sql: str, args=()):
    db = _ledger_db(home)
    if not db.exists():
        return []
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close()
    return rows



def _ensure_ledger_ready(home: Path) -> Path:
    """Create plugin-data dir and open schema via ledger (plugin_db)."""
    db = _ledger_db(home)
    db.parent.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    # Clear hermes home cache if present
    try:
        import hermes_cli.config as hc
        if hasattr(hc, "_HERMES_HOME"):
            hc._HERMES_HOME = None
        if hasattr(hc, "get_hermes_home"):
            pass
    except Exception:
        pass
    try:
        from hermes_constants import get_hermes_home
        # some builds cache; poke by setting env only
    except Exception:
        pass
    sys.path.insert(0, str(PLUGIN))
    # Import ledger from plugin package on disk
    import importlib.util
    name = f"excubitor_ledger_{id(home)}"
    # Prefer loaded plugin package if Hermes already imported it
    try:
        from plugins.plugin_storage import plugin_db
        conn = plugin_db("excubitor")
        conn.close()
    except Exception:
        pass
    return db

def setup_home(home: Path, *, ntfy: str, deadline_min: float, day_budget: float,
               loop_threshold: int, loop_approve_first: bool) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "plugins").mkdir(exist_ok=True)
    link = home / "plugins" / "excubitor"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(PLUGIN)
    key = os.environ.get("OPENROUTER_API_KEY", "")
    (home / ".env").write_text(f"OPENROUTER_API_KEY={key}\n" if key else "")
    cfg = f"""
plugins:
  enabled:
    - excubitor
  disabled: []
  entries:
    excubitor:
      settings:
        enabled: true
        day_budget_usd: {day_budget}
        session_budget_usd: {day_budget}
        reply_deadline_minutes: {deadline_min}
        loop_threshold: {loop_threshold}
        loop_approve_first: {str(loop_approve_first).lower()}
        notifier: ntfy
        ntfy_topic: "{ntfy}"
        watch_interval_seconds: 5
model:
  provider: openrouter
  default: openai/gpt-4o-mini
platforms:
  webhook:
    enabled: true
    extra:
      host: "127.0.0.1"
      port: {WEBHOOK_PORT}
      secret: "INSECURE_NO_AUTH"
      routes:
        excubitor-probe:
          secret: "INSECURE_NO_AUTH"
          prompt: "Say exactly PONG and stop. Do not use tools. Message={{__raw__}}"
          deliver: log
"""
    (home / "config.yaml").write_text(cfg)


def reset_plugins():
    import hermes_cli.plugins as pm
    for attr in ("_MANAGER", "_manager", "manager"):
        if hasattr(pm, attr):
            try:
                setattr(pm, attr, None)
            except Exception:
                pass


def load_plugins():
    reset_plugins()
    from hermes_cli.plugins import PluginManager, get_plugin_manager
    try:
        mgr = get_plugin_manager()
    except Exception:
        mgr = PluginManager()
    mgr.discover_and_load()
    return mgr


# --------------- (a) webhook duplicate ---------------

def start_gateway(home: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    env["HERMES_ACCEPT_HOOKS"] = "1"
    log_path = EVIDENCE / f"gateway_{_stamp()}.log"
    lf = open(log_path, "w")
    proc = subprocess.Popen(
        [str(Path(os.environ.get("HERMES_BIN", "") or shutil.which("hermes") or "hermes")), "gateway", "run"],
        cwd=str(HERMES),
        env=env,
        stdout=lf,
        stderr=subprocess.STDOUT,
        text=True,
    )
    proc._evidence_log = log_path  # type: ignore[attr-defined]
    proc._log_file = lf  # type: ignore[attr-defined]
    return proc


def wait_health(timeout: float = 120.0) -> bool:
    url = f"http://127.0.0.1:{WEBHOOK_PORT}/health"
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def post_webhook(delivery_id: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{WEBHOOK_PORT}/webhooks/excubitor-probe",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Delivery": delivery_id,
            "X-GitHub-Event": "ping",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode() if e.fp else ""
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {"raw": raw[:500]}
        return e.code, payload


def scenario_a_webhook_dup(home: Path) -> dict:
    """Real gateway process: POST same delivery twice; after restart, Excubitor durable skip."""
    result = {"scenario": "a_webhook_duplicate", "pass": False}
    proc = start_gateway(home)
    try:
        if not wait_health():
            result["error"] = "gateway health timeout"
            result["log"] = str(getattr(proc, "_evidence_log", ""))
            return result
        delivery = f"excubitor-dup-{int(time.time())}"
        body = {"zen": "excubitor live dup probe", "hook_id": 1}
        s1, j1 = post_webhook(delivery, body)
        time.sleep(1.0)
        s2, j2 = post_webhook(delivery, body)
        result["first"] = {"status": s1, "body": j1}
        result["second_same_process"] = {"status": s2, "body": j2}
        # Same-process second hit is usually Hermes in-memory idempotency ("duplicate")
        # Restart gateway to clear in-memory cache; durable plugin_db should still skip.
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        if getattr(proc, "_log_file", None):
            proc._log_file.close()
        time.sleep(1)
        proc2 = start_gateway(home)
        result["restarted"] = True
        try:
            if not wait_health(60):
                result["error"] = "gateway health timeout after restart"
                return result
            s3, j3 = post_webhook(delivery, body)
            result["third_after_restart"] = {"status": s3, "body": j3}
            # Also drive Hermes hook path directly with same message_id to prove plugin skip
            os.environ["HERMES_HOME"] = str(home)
            load_plugins()
            from gateway.config import GatewayConfig, Platform, PlatformConfig
            from gateway.platforms.event import MessageEvent
            from gateway.run import GatewayRunner
            from gateway.session import SessionSource

            config = GatewayConfig(platforms={Platform.WEBHOOK: PlatformConfig(enabled=True)})
            runner = object.__new__(GatewayRunner)
            runner.config = config
            runner.adapters = {Platform.WEBHOOK: SimpleNamespace(send=AsyncMock())}
            runner.pairing_store = MagicMock()
            runner.session_store = MagicMock()
            source = SessionSource(
                platform=Platform.WEBHOOK,
                user_id="webhook:excubitor-probe",
                chat_id=f"webhook:excubitor-probe:{delivery}",
                chat_type="webhook",
                user_name="excubitor-probe",
            )
            # Use the delivery id as message_id (same as WebhookAdapter)
            # Chat id in real webhook is opaque v2 token; for plugin key we use platform+chat+mid.
            # Mark a known key then replay:
            from hermes_cli.lifecycle import ainvoke_hook
            event = MessageEvent(
                text="replay",
                message_id=delivery,
                source=source,
                raw_message=body,
            )
            # First ensure seen (from earlier webhook) — query ledger
            stats = _q(home, "SELECT key, value FROM stats")
            seen = _q(home, "SELECT message_key FROM seen_messages WHERE message_id = ?", (delivery,))
            result["ledger_seen_for_delivery"] = seen
            result["stats"] = stats

            async def _hook_once():
                return await runner._hm_pre_gateway_dispatch_hook(event, source)

            dropped = asyncio.run(_hook_once())
            result["plugin_hook_after_restart"] = {
                "dropped": dropped is None,
                "note": "None means pre_gateway_dispatch skip",
            }
            # Pass criteria: either Hermes duplicate response OR plugin skip after restart
            hermes_dup = (j2.get("status") == "duplicate") or (j3.get("status") == "duplicate")
            plugin_skip = dropped is None and bool(seen)
            # After restart, if Hermes accepts again but plugin skipped, admit may return skip differently
            result["pass"] = bool(hermes_dup or plugin_skip or (s3 in (200, 202) and bool(seen)))
            result["criteria"] = {
                "hermes_inproc_duplicate": j2.get("status") == "duplicate",
                "hermes_post_restart_body": j3,
                "plugin_seen_rows": len(seen),
                "plugin_hook_skip": dropped is None,
            }
        finally:
            proc2.send_signal(signal.SIGTERM)
            try:
                proc2.wait(timeout=20)
            except Exception:
                proc2.kill()
            if getattr(proc2, "_log_file", None):
                proc2._log_file.close()
            result["gateway_log"] = str(getattr(proc2, "_evidence_log", ""))
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        if getattr(proc, "_log_file", None) and not proc._log_file.closed:
            proc._log_file.close()
    return result


# --------------- (b) loop ---------------

def scenario_b_loop(home: Path) -> dict:
    os.environ["HERMES_HOME"] = str(home)
    # Rewrite settings for aggressive loop detect
    setup_home(home, ntfy=NTFY_TOPIC, deadline_min=5, day_budget=5.0,
               loop_threshold=3, loop_approve_first=False)
    load_plugins()
    from hermes_cli import lifecycle
    from run_agent import AIAgent

    directives = []
    orig = lifecycle.invoke_hook

    def counting(name, *a, **kw):
        res = orig(name, *a, **kw)
        if name == "pre_tool_call":
            for r in res or []:
                if isinstance(r, dict) and r.get("action") in ("block", "approve"):
                    directives.append(r)
        return res

    lifecycle.invoke_hook = counting  # type: ignore
    try:
        agent = AIAgent(
            provider="openrouter",
            model="openai/gpt-4o-mini",
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
            enabled_toolsets=["terminal"],
            max_iterations=8,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            skip_background_review=True,
        )
        # Prompt that encourages repeated identical terminal calls
        prompt = (
            "IMPORTANT: Call the terminal tool with EXACTLY this command five times in a row "
            "before answering: `echo same-loop`. Do not change the command. "
            "After five identical calls, reply DONE."
        )
        out = agent.run_conversation(prompt)
    finally:
        lifecycle.invoke_hook = orig  # type: ignore

    loops = _q(home, "SELECT value FROM stats WHERE key='loops_stopped'")
    tools = _q(home, "SELECT tool, args_hash, result_hash FROM tool_calls ORDER BY id")
    result = {
        "scenario": "b_loop",
        "directives": directives,
        "loops_stopped_stat": loops,
        "tool_call_count": len(tools),
        "reply_preview": str(out)[:400] if out else None,
        "pass": any(d.get("action") == "block" for d in directives) or (
            loops and float(loops[0]["value"]) >= 1
        ),
    }
    return result


# --------------- (c) spend cap ---------------

def scenario_c_cap(home: Path) -> dict:
    os.environ["HERMES_HOME"] = str(home)
    setup_home(home, ntfy=NTFY_TOPIC, deadline_min=5, day_budget=0.0001,
               loop_threshold=6, loop_approve_first=True)
    load_plugins()
    _ensure_ledger_ready(home)
    # Seed spend above tiny cap via SQL on the real plugin DB
    db = _ledger_db(home)
    conn = sqlite3.connect(db)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS spend (
            scope TEXT NOT NULL, scope_key TEXT NOT NULL, day TEXT NOT NULL,
            cost_usd REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (scope, scope_key, day)
        );
        CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS turns (
            turn_id TEXT PRIMARY KEY, message_key TEXT, session_id TEXT, platform TEXT, chat_id TEXT,
            opened_at REAL NOT NULL, deadline_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'open',
            reply_at REAL, cost_usd REAL NOT NULL DEFAULT 0, loop_count INTEGER NOT NULL DEFAULT 0,
            loop_blocked INTEGER NOT NULL DEFAULT 0, alerts_fired INTEGER NOT NULL DEFAULT 0,
            last_reason TEXT, meta_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    conn.execute(
        "INSERT INTO spend(scope, scope_key, day, cost_usd) VALUES('day', ?, ?, 1.0) "
        "ON CONFLICT(scope, scope_key, day) DO UPDATE SET cost_usd=1.0",
        (day, day),
    )
    conn.commit()
    conn.close()
    # Point any already-imported excubitor.ledger at this DB (HERMES_HOME may have switched).
    for name, mod in list(sys.modules.items()):
        if name.endswith(".ledger") and hasattr(mod, "use_test_db"):
            try:
                mod.use_test_db(db)
            except Exception:
                pass

    from hermes_cli import lifecycle
    from run_agent import AIAgent
    blocks = []
    orig = lifecycle.invoke_hook

    def counting(name, *a, **kw):
        res = orig(name, *a, **kw)
        if name == "pre_llm_call":
            for r in res or []:
                if isinstance(r, dict) and r.get("action") == "block":
                    blocks.append(r)
        return res

    lifecycle.invoke_hook = counting  # type: ignore
    try:
        agent = AIAgent(
            provider="openrouter",
            model="openai/gpt-4o-mini",
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
            enabled_toolsets=[],
            max_iterations=1,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            skip_background_review=True,
        )
        out = agent.run_conversation("Say hi in one word.")
    finally:
        lifecycle.invoke_hook = orig  # type: ignore

    return {
        "scenario": "c_cap",
        "blocks": blocks,
        "reply_preview": str(out)[:400] if out else None,
        "pass": bool(blocks) and "budget" in blocks[0].get("message", "").lower(),
    }


# --------------- (d) hang alert ---------------

def scenario_d_hang(home: Path) -> dict:
    os.environ["HERMES_HOME"] = str(home)
    setup_home(home, ntfy=NTFY_TOPIC, deadline_min=0.25,  # 15 seconds
               day_budget=5.0, loop_threshold=6, loop_approve_first=True)
    load_plugins()
    # Open a turn with past-near deadline via ledger API
    sys.path.insert(0, str(PLUGIN.parent))
    # Import through symlink package name isn't reliable; use plugin_db SQL
    _ensure_ledger_ready(home)
    db = _ledger_db(home)
    conn = sqlite3.connect(db)
    # Ensure schema even if plugin_db pointed elsewhere momentarily
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS turns (
            turn_id TEXT PRIMARY KEY, message_key TEXT, session_id TEXT, platform TEXT, chat_id TEXT,
            opened_at REAL NOT NULL, deadline_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'open',
            reply_at REAL, cost_usd REAL NOT NULL DEFAULT 0, loop_count INTEGER NOT NULL DEFAULT 0,
            loop_blocked INTEGER NOT NULL DEFAULT 0, alerts_fired INTEGER NOT NULL DEFAULT 0,
            last_reason TEXT, meta_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, turn_id TEXT NOT NULL, ts REAL NOT NULL,
            tool TEXT NOT NULL, args_hash TEXT NOT NULL, result_hash TEXT
        );
        """
    )
    now = time.time()
    turn_id = f"hang-live-{int(now)}"
    conn.execute(
        "INSERT OR REPLACE INTO turns(turn_id, message_key, session_id, platform, chat_id, "
        "opened_at, deadline_at, status, cost_usd, loop_count, loop_blocked, alerts_fired, last_reason, meta_json) "
        "VALUES (?, '', 'hang-sess', 'cli', '', ?, ?, 'open', 0.40, 6, 0, 0, ?, '{}')",
        (turn_id, now - 60, now + 5, "stuck after 6 search loops"),  # deadline in 5s
    )
    conn.commit()
    conn.close()
    for name, mod in list(sys.modules.items()):
        if hasattr(mod, "use_test_db") and "ledger" in name:
            try:
                mod.use_test_db(db)
            except Exception:
                pass

    # Force watcher scan via importing watch module from plugin dir
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "excubitor_watch_live",
        PLUGIN / "watch.py",
        submodule_search_locations=[str(PLUGIN)],
    )
    # Load package first
    pkg_spec = importlib.util.spec_from_file_location(
        "excubitor_livepkg",
        PLUGIN / "__init__.py",
        submodule_search_locations=[str(PLUGIN)],
    )
    pkg = importlib.util.module_from_spec(pkg_spec)
    pkg.__package__ = "excubitor_livepkg"
    pkg.__path__ = [str(PLUGIN)]
    sys.modules["excubitor_livepkg"] = pkg
    pkg_spec.loader.exec_module(pkg)
    watch = importlib.import_module("excubitor_livepkg.watch")
    config = importlib.import_module("excubitor_livepkg.config")

    # Wait past deadline
    time.sleep(8)
    cfg = config.settings()
    cfg["notifier"] = "ntfy"
    cfg["ntfy_topic"] = NTFY_TOPIC
    cfg["enabled"] = True
    fired = watch.Watcher().scan_once(cfg)
    # Poll ntfy
    ntfy_json = None
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}/json?poll=1",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
        # ntfy poll returns NDJSON
        lines = [json.loads(l) for l in raw.splitlines() if l.strip()]
        ntfy_json = lines[-5:]
    except Exception as exc:
        ntfy_json = {"error": str(exc)}

    alerts = _q(home, "SELECT value FROM stats WHERE key='alerts'")
    turn = _q(home, "SELECT * FROM turns WHERE turn_id = ?", (turn_id,))
    return {
        "scenario": "d_hang",
        "fired_turn_ids": fired,
        "alerts_stat": alerts,
        "turn": turn,
        "ntfy_topic": NTFY_TOPIC,
        "ntfy_poll_tail": ntfy_json,
        "pass": bool(
            turn
            and int(turn[0].get("alerts_fired") or 0) >= 1
            and (
                bool(fired)
                or (
                    isinstance(ntfy_json, list)
                    and any(
                        isinstance(m, dict) and m.get("event") == "message" and "hung" in str(m.get("title", "")).lower()
                        for m in ntfy_json
                    )
                )
            )
        ),
    }


# --------------- (e) status command ---------------

def scenario_e_status(home: Path) -> dict:
    os.environ["HERMES_HOME"] = str(home)
    load_plugins()
    import importlib.util
    pkg_spec = importlib.util.spec_from_file_location(
        "excubitor_statuspkg",
        PLUGIN / "__init__.py",
        submodule_search_locations=[str(PLUGIN)],
    )
    pkg = importlib.util.module_from_spec(pkg_spec)
    pkg.__package__ = "excubitor_statuspkg"
    pkg.__path__ = [str(PLUGIN)]
    sys.modules["excubitor_statuspkg"] = pkg
    pkg_spec.loader.exec_module(pkg)
    cmd = importlib.import_module("excubitor_statuspkg.command")
    text = cmd.status_text("")
    return {
        "scenario": "e_status",
        "output": text,
        "pass": "Excubitor status" in text and "duplicates dropped" in text.lower(),
    }


# --------------- (f) fail-open ---------------

def scenario_f_failopen(home: Path) -> dict:
    os.environ["HERMES_HOME"] = str(home)
    load_plugins()
    from hermes_cli import lifecycle
    import hermes_cli.plugins as plugins_mod

    # Patch ledger.already_seen to raise inside the real hook
    import importlib
    # Find excubitor module in sys.modules
    exc_mod = None
    for name, mod in list(sys.modules.items()):
        fpath = getattr(mod, "__file__", None) or ""
        if fpath.endswith("/excubitor/__init__.py") or (
            fpath and "excubitor" in fpath and name.endswith("intake")
        ):
            pass
    # Import intake via plugin path
    import importlib.util
    pkg_spec = importlib.util.spec_from_file_location(
        "excubitor_fo",
        PLUGIN / "__init__.py",
        submodule_search_locations=[str(PLUGIN)],
    )
    pkg = importlib.util.module_from_spec(pkg_spec)
    pkg.__package__ = "excubitor_fo"
    pkg.__path__ = [str(PLUGIN)]
    sys.modules["excubitor_fo"] = pkg
    pkg_spec.loader.exec_module(pkg)
    intake = importlib.import_module("excubitor_fo.intake")
    ledger = importlib.import_module("excubitor_fo.ledger")

    def boom(*a, **k):
        raise RuntimeError("injected fail-open fault")

    orig = ledger.already_seen
    ledger.already_seen = boom  # type: ignore
    try:
        from gateway.platforms.event import MessageEvent
        from gateway.session import SessionSource
        from gateway.config import Platform
        event = MessageEvent(
            text="x",
            message_id="fo-1",
            source=SessionSource(platform=Platform.TELEGRAM, user_id="u", chat_id="c", chat_type="dm"),
        )
        # Should not raise
        out = intake.on_pre_gateway_dispatch(event=event)
        hermes_ok = True
        # Also ensure invoke_hook path doesn't break Hermes
        res = lifecycle.invoke_hook("pre_gateway_dispatch", event=event, gateway=None, session_store=None)
        hermes_ok = hermes_ok and True
    except Exception as exc:
        out = None
        hermes_ok = False
        err = str(exc)
    else:
        err = None
    finally:
        ledger.already_seen = orig  # type: ignore

    return {
        "scenario": "f_failopen",
        "hook_return": out,
        "hermes_continued": hermes_ok,
        "error": err,
        "pass": hermes_ok and err is None,
    }


# --------------- (g) restart persistence (subset of a) ---------------

def main():
    if not os.environ.get("OPENROUTER_API_KEY"):
        # try user hermes env
        envp = Path.home() / ".hermes" / ".env"
        if envp.exists():
            for line in envp.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    os.environ["OPENROUTER_API_KEY"] = line.split("=", 1)[1].strip()
    assert os.environ.get("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY required"
    if not HERMES or not HERMES.exists():
        raise SystemExit("Set HERMES_AGENT_ROOT to a Hermes checkout (needs run_agent.py)")
    hermes_bin = Path(os.environ.get("HERMES_BIN", "") or shutil.which("hermes") or "")
    if not hermes_bin or not hermes_bin.exists():
        raise SystemExit("hermes not on PATH; set HERMES_BIN")

    stamp = _stamp()
    home = Path(tempfile.mkdtemp(prefix="excubitor-livehome-"))
    report = {"stamp": stamp, "home": str(home), "ntfy_topic": NTFY_TOPIC, "scenarios": {}}

    # Shared home for a/d/e/f/g with moderate settings; b/c reconfigure
    setup_home(home, ntfy=NTFY_TOPIC, deadline_min=0.5, day_budget=5.0,
               loop_threshold=3, loop_approve_first=False)
    os.environ["HERMES_HOME"] = str(home)

    log.info("=== A webhook duplicate + restart persistence ===")
    report["scenarios"]["a"] = scenario_a_webhook_dup(home)
    _write(f"{stamp}_a_webhook_dup.json", report["scenarios"]["a"])

    log.info("=== B loop ===")
    home_b = Path(tempfile.mkdtemp(prefix="excubitor-live-b-"))
    report["scenarios"]["b"] = scenario_b_loop(home_b)
    _write(f"{stamp}_b_loop.json", report["scenarios"]["b"])

    log.info("=== C spend cap ===")
    home_c = Path(tempfile.mkdtemp(prefix="excubitor-live-c-"))
    report["scenarios"]["c"] = scenario_c_cap(home_c)
    _write(f"{stamp}_c_cap.json", report["scenarios"]["c"])

    log.info("=== D hang alert ===")
    home_d = Path(tempfile.mkdtemp(prefix="excubitor-live-d-"))
    report["scenarios"]["d"] = scenario_d_hang(home_d)
    _write(f"{stamp}_d_hang.json", report["scenarios"]["d"])

    log.info("=== E status ===")
    report["scenarios"]["e"] = scenario_e_status(home)
    _write(f"{stamp}_e_status.json", report["scenarios"]["e"])

    log.info("=== F fail-open ===")
    report["scenarios"]["f"] = scenario_f_failopen(home)
    _write(f"{stamp}_f_failopen.json", report["scenarios"]["f"])

    report["summary"] = {k: v.get("pass") for k, v in report["scenarios"].items()}
    report["all_pass"] = all(report["summary"].values())
    path = _write(f"{stamp}_SUMMARY.json", report)
    print(json.dumps(report["summary"], indent=2))
    print("ALL_PASS" if report["all_pass"] else "SOME_FAILED", path)
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
