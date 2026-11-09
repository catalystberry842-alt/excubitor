"""Per-turn ledger persisted via plugin_db (SQLite). Fail-open on every write."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("excubitor.ledger")

PLUGIN_NAME = "excubitor"

_local = threading.local()
_test_db_path: Path | None = None
_schema_lock = threading.Lock()
_memory_uri = f"file:excubitor-{os.getpid()}-{uuid.uuid4().hex}"


def use_test_db(path: Path | str | None) -> None:
    """Point the ledger at a tempfile DB for unit tests (None restores plugin_db)."""
    global _test_db_path
    _test_db_path = Path(path) if path else None
    if hasattr(_local, "conn"):
        try:
            _local.conn.close()
        except Exception:
            pass
        try:
            del _local.conn
        except Exception:
            pass
    if hasattr(_local, "db_id"):
        try:
            del _local.db_id
        except Exception:
            pass


def _open_conn() -> sqlite3.Connection:
    if _test_db_path is not None:
        conn = sqlite3.connect(str(_test_db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    try:
        from plugins.plugin_storage import plugin_db

        conn = plugin_db(PLUGIN_NAME)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as exc:
        # Last-resort: process-local ephemeral DB so hooks still fail-open with state.
        logger.debug("excubitor: plugin_db unavailable (%s); using ephemeral memory db", exc)
        conn = sqlite3.connect(
            f"{_memory_uri}-{hash(_db_identity())}?mode=memory&cache=shared",
            uri=True,
            check_same_thread=False,
            timeout=10,
        )
        conn.row_factory = sqlite3.Row
        return conn


def _db_identity() -> str:
    """Stable id for the active DB so we reopen when HERMES_HOME / test path changes."""
    if _test_db_path is not None:
        return f"test:{_test_db_path}"
    try:
        from hermes_constants import get_hermes_home

        return f"home:{get_hermes_home()}/plugin-data/{PLUGIN_NAME}/data.db"
    except Exception:
        return f"env:{os.environ.get('HERMES_HOME', '')}"


def _conn() -> sqlite3.Connection:
    identity = _db_identity()
    conn = getattr(_local, "conn", None)
    cached_id = getattr(_local, "db_id", None)
    if conn is None or cached_id != identity:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        conn = _open_conn()
        _local.conn = conn
        _local.db_id = identity
        _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    with _schema_lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dispatch_claims (
                message_key TEXT PRIMARY KEY,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turn_aliases (
                alias TEXT PRIMARY KEY,
                turn_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cost_cursors (
                session_id TEXT PRIMARY KEY,
                total REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS seen_messages (
                message_key TEXT PRIMARY KEY,
                first_seen_at REAL NOT NULL,
                platform TEXT,
                chat_id TEXT,
                message_id TEXT
            );
            CREATE TABLE IF NOT EXISTS turns (
                turn_id TEXT PRIMARY KEY,
                message_key TEXT,
                session_id TEXT,
                platform TEXT,
                chat_id TEXT,
                opened_at REAL NOT NULL,
                deadline_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                reply_at REAL,
                cost_usd REAL NOT NULL DEFAULT 0,
                loop_count INTEGER NOT NULL DEFAULT 0,
                loop_blocked INTEGER NOT NULL DEFAULT 0,
                alerts_fired INTEGER NOT NULL DEFAULT 0,
                last_reason TEXT,
                meta_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_turns_status_deadline ON turns(status, deadline_at);
            CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, opened_at);
            CREATE TABLE IF NOT EXISTS tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_id TEXT NOT NULL,
                ts REAL NOT NULL,
                tool TEXT NOT NULL,
                args_hash TEXT NOT NULL,
                result_hash TEXT,
                FOREIGN KEY(turn_id) REFERENCES turns(turn_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tool_calls_turn ON tool_calls(turn_id, tool, args_hash);
            CREATE TABLE IF NOT EXISTS spend (
                scope TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                day TEXT NOT NULL,
                cost_usd REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (scope, scope_key, day)
            );
            CREATE TABLE IF NOT EXISTS turn_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_id TEXT NOT NULL,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_turn_events_turn ON turn_events(turn_id, ts);
            CREATE TABLE IF NOT EXISTS receipts (
                turn_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                text TEXT NOT NULL,
                data_json TEXT NOT NULL DEFAULT '{}',
                notable INTEGER NOT NULL DEFAULT 0,
                notified INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_receipts_created ON receipts(created_at);
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY,
                value REAL NOT NULL DEFAULT 0
            );
            """
        )
        conn.commit()


@contextmanager
def _tx() -> Iterator[sqlite3.Connection]:
    conn = _conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def utc_day(ts: float | None = None) -> str:
    t = ts if ts is not None else time.time()
    return datetime.fromtimestamp(t, tz=UTC).strftime("%Y-%m-%d")


def bump_stat(key: str, delta: float = 1.0) -> None:
    try:
        with _tx() as conn:
            conn.execute(
                "INSERT INTO stats(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = value + excluded.value",
                (key, float(delta)),
            )
    except Exception as exc:
        logger.debug("excubitor: bump_stat failed: %s", exc)


def get_stats() -> dict[str, float]:
    try:
        rows = _conn().execute("SELECT key, value FROM stats").fetchall()
        return {r["key"]: float(r["value"]) for r in rows}
    except Exception:
        return {}


def already_seen(message_key: str) -> bool:
    try:
        from . import config as _config
        from .hashing import is_text_fallback_key

        row = (
            _conn()
            .execute(
                "SELECT message_key, first_seen_at FROM seen_messages WHERE message_key = ?",
                (message_key,),
            )
            .fetchone()
        )
        if row is None:
            return False
        if is_text_fallback_key(message_key):
            ttl = float((_config.settings() or {}).get("text_dedup_seconds") or 120.0)
            age = time.time() - float(row["first_seen_at"] or 0)
            if age > max(1.0, ttl):
                with _tx() as conn:
                    conn.execute("DELETE FROM seen_messages WHERE message_key = ?", (message_key,))
                return False
        return True
    except Exception as exc:
        logger.debug("excubitor: already_seen failed: %s", exc)
        return False  # fail-open: treat as new


def mark_seen(
    message_key: str, *, platform: str = "", chat_id: str = "", message_id: str = ""
) -> bool:
    """Insert message_key. Returns True if newly inserted, False if duplicate/error."""
    try:
        with _tx() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO seen_messages(message_key, first_seen_at, platform, chat_id, message_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (message_key, time.time(), platform, chat_id, message_id),
            )
            ok = cur.rowcount > 0
            conn.execute("DELETE FROM dispatch_claims WHERE message_key=?", (message_key,))
        maybe_prune()
        return ok
    except Exception as exc:
        logger.debug("excubitor: mark_seen failed: %s", exc)
        return True  # fail-open: pretend new so we don't skip traffic


def open_turn(
    turn_id: str,
    *,
    message_key: str = "",
    session_id: str = "",
    platform: str = "",
    chat_id: str = "",
    deadline_minutes: float = 5.0,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    deadline = now + max(0.1, float(deadline_minutes)) * 60.0
    try:
        with _tx() as conn:
            existing = conn.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
            if existing:
                return dict(existing)
            conn.execute(
                "INSERT INTO turns(turn_id, message_key, session_id, platform, chat_id, opened_at, "
                "deadline_at, status, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                (
                    turn_id,
                    message_key,
                    session_id,
                    platform,
                    chat_id,
                    now,
                    deadline,
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )
        return get_turn(turn_id) or {}
    except Exception as exc:
        logger.debug("excubitor: open_turn failed: %s", exc)
        return {}


def get_turn(turn_id: str) -> dict[str, Any] | None:
    try:
        row = _conn().execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def find_open_turn_for_session(session_id: str) -> dict[str, Any] | None:
    if not session_id:
        return None
    try:
        row = (
            _conn()
            .execute(
                "SELECT * FROM turns WHERE session_id = ? AND status = 'open' "
                "ORDER BY opened_at DESC LIMIT 1",
                (session_id,),
            )
            .fetchone()
        )
        return dict(row) if row else None
    except Exception:
        return None


def find_open_turn_for_session_key(session_key: str) -> dict[str, Any] | None:
    """Most recent open turn whose gateway route key (meta_json.session_key) matches."""
    if not session_key:
        return None
    try:
        rows = (
            _conn()
            .execute("SELECT * FROM turns WHERE status = 'open' ORDER BY opened_at DESC")
            .fetchall()
        )
        for r in rows:
            try:
                meta = json.loads(r["meta_json"] or "{}")
            except Exception:
                continue
            if meta.get("session_key") == session_key:
                return dict(r)
        return None
    except Exception:
        return None


def find_open_unbound_turn() -> dict[str, Any] | None:
    """Most recent open turn that has no agent session_id yet (gateway-admitted)."""
    try:
        row = (
            _conn()
            .execute(
                "SELECT * FROM turns WHERE status = 'open' "
                "AND (session_id IS NULL OR session_id = '') "
                "ORDER BY opened_at DESC LIMIT 1"
            )
            .fetchone()
        )
        return dict(row) if row else None
    except Exception:
        return None


def bind_turn_session(turn_id: str, session_id: str) -> None:
    if not turn_id or not session_id:
        return
    try:
        with _tx() as conn:
            conn.execute(
                "UPDATE turns SET session_id = ? WHERE turn_id = ? "
                "AND (session_id IS NULL OR session_id = '')",
                (session_id, turn_id),
            )
    except Exception as exc:
        logger.debug("excubitor: bind_turn_session failed: %s", exc)


def resolve_turn_id(
    *,
    session_id: str = "",
    turn_id: str = "",
    task_id: str = "",
    session_key: str = "",
    platform: str = "",
    chat_id: str = "",
    start_new: bool = False,
) -> str:
    """Resolve explicit IDs first; never borrow an unrelated gateway turn.

    Hermes supplies its route via the published gateway.session_context.get_session_env("HERMES_SESSION_KEY"). A
    host with no route/source identity cannot safely associate an unbound turn.
    """
    conn = _conn()
    for ident in (turn_id, task_id):
        if not ident:
            continue
        if get_turn(str(ident)):
            return str(ident)
        alias = conn.execute(
            "SELECT turn_id FROM turn_aliases WHERE alias=?", (str(ident),)
        ).fetchone()
        if alias:
            return str(alias["turn_id"])
    if not session_key:
        try:
            from gateway.session_context import get_session_env

            session_key = get_session_env("HERMES_SESSION_KEY")
        except Exception:
            # Same legacy env var get_session_env falls back to.
            session_key = os.getenv("HERMES_SESSION_KEY", "")
    if session_key or (platform and chat_id):
        # Claim and bind in a write transaction, never by process-global recency.
        with _tx() as tx:
            tx.execute("BEGIN IMMEDIATE")
            rows = tx.execute(
                "SELECT * FROM turns WHERE status='open' AND "
                "(session_id='' OR session_id IS NULL) ORDER BY opened_at"
            ).fetchall()
            matches = [
                r
                for r in rows
                if (
                    (session_key and json.loads(r["meta_json"]).get("session_key") == session_key)
                    or (not session_key and r["platform"] == platform and r["chat_id"] == chat_id)
                )
            ]
            if len(matches) == 1:
                tid = str(matches[0]["turn_id"])
                tx.execute("UPDATE turns SET session_id=? WHERE turn_id=?", (session_id, tid))
                for alias in (turn_id, task_id):
                    if alias:
                        tx.execute("INSERT OR IGNORE INTO turn_aliases VALUES (?, ?)", (alias, tid))
                return tid
    open_t = find_open_turn_for_session(session_id)
    if open_t and (not start_new or not turn_id):
        return str(open_t["turn_id"])
    if turn_id or task_id:
        return str(turn_id or task_id)
    if session_id and not start_new:
        latest = conn.execute(
            "SELECT turn_id FROM turns WHERE session_id=? ORDER BY opened_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if latest:
            return str(latest["turn_id"])
    return f"sess:{session_id}:{uuid.uuid4().hex}" if session_id else f"anon:{uuid.uuid4().hex}"


def mark_reply(turn_id: str, *, status: str = "replied") -> None:
    try:
        with _tx() as conn:
            conn.execute(
                "UPDATE turns SET status = ?, reply_at = ? WHERE turn_id = ? AND status = 'open'",
                (status, time.time(), turn_id),
            )
    except Exception as exc:
        logger.debug("excubitor: mark_reply failed: %s", exc)


def set_turn_reason(turn_id: str, reason: str) -> None:
    try:
        with _tx() as conn:
            conn.execute("UPDATE turns SET last_reason = ? WHERE turn_id = ?", (reason, turn_id))
    except Exception as exc:
        logger.debug("excubitor: set_turn_reason failed: %s", exc)


def add_cost(turn_id: str, amount: float, *, session_id: str = "") -> float:
    """Add USD to the turn and day/session spend counters. Returns new turn cost."""
    amount = max(0.0, float(amount or 0.0))
    if amount <= 0:
        turn = get_turn(turn_id)
        return float((turn or {}).get("cost_usd") or 0.0)
    day = utc_day()
    try:
        with _tx() as conn:
            conn.execute(
                "UPDATE turns SET cost_usd = cost_usd + ? WHERE turn_id = ?",
                (amount, turn_id),
            )
            for scope, key in (("day", day), ("session", session_id or turn_id)):
                if not key:
                    continue
                conn.execute(
                    "INSERT INTO spend(scope, scope_key, day, cost_usd) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(scope, scope_key, day) DO UPDATE SET "
                    "cost_usd = cost_usd + excluded.cost_usd",
                    (scope, key, day, amount),
                )
            conn.execute(
                "INSERT INTO stats(key, value) VALUES('cost_usd_total', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = value + excluded.value",
                (amount,),
            )
            row = conn.execute(
                "SELECT cost_usd FROM turns WHERE turn_id = ?", (turn_id,)
            ).fetchone()
            return float(row["cost_usd"]) if row else amount
    except Exception as exc:
        logger.debug("excubitor: add_cost failed: %s", exc)
        return 0.0


def spend_for(scope: str, scope_key: str, day: str | None = None) -> float:
    day = day or utc_day()
    try:
        if scope == "session":
            row = (
                _conn()
                .execute(
                    "SELECT SUM(cost_usd) AS cost_usd FROM spend WHERE scope=? AND scope_key=?",
                    (scope, scope_key),
                )
                .fetchone()
            )
        else:
            row = (
                _conn()
                .execute(
                    "SELECT cost_usd FROM spend WHERE scope=? AND scope_key=? AND day=?",
                    (scope, scope_key, day),
                )
                .fetchone()
            )
        return float(row["cost_usd"] or 0) if row else 0.0
    except Exception:
        return 0.0


def record_tool_pre(turn_id: str, tool: str, args_hash: str) -> int:
    """Insert a pre-call row (result_hash NULL). Returns row id or -1."""
    try:
        with _tx() as conn:
            cur = conn.execute(
                "INSERT INTO tool_calls(turn_id, ts, tool, args_hash, result_hash) VALUES (?, ?, ?, ?, NULL)",
                (turn_id, time.time(), tool, args_hash),
            )
            rid = int(cur.lastrowid or -1)
        try:
            from . import config as _config

            cap_tool_rows(turn_id, int(_config.settings().get("max_tool_rows_per_turn") or 500))
        except Exception:
            pass
        return rid
    except Exception as exc:
        logger.debug("excubitor: record_tool_pre failed: %s", exc)
        return -1


def record_tool_post(turn_id: str, tool: str, args_hash: str, result_hash: str) -> None:
    """Fill the newest matching open result_hash, or insert a completed row."""
    try:
        with _tx() as conn:
            row = conn.execute(
                "SELECT id FROM tool_calls WHERE turn_id = ? AND tool = ? AND args_hash = ? "
                "AND result_hash IS NULL ORDER BY id DESC LIMIT 1",
                (turn_id, tool, args_hash),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE tool_calls SET result_hash = ? WHERE id = ?",
                    (result_hash, row["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO tool_calls(turn_id, ts, tool, args_hash, result_hash) VALUES (?, ?, ?, ?, ?)",
                    (turn_id, time.time(), tool, args_hash, result_hash),
                )
    except Exception as exc:
        logger.debug("excubitor: record_tool_post failed: %s", exc)


def tool_history(turn_id: str, tool: str, args_hash: str) -> list[dict[str, Any]]:
    try:
        rows = (
            _conn()
            .execute(
                "SELECT * FROM tool_calls WHERE turn_id = ? AND tool = ? AND args_hash = ? ORDER BY id ASC",
                (turn_id, tool, args_hash),
            )
            .fetchall()
        )
        return [dict(r) for r in rows]
    except Exception:
        return []


def all_tool_calls(turn_id: str) -> list[dict[str, Any]]:
    try:
        rows = (
            _conn()
            .execute("SELECT * FROM tool_calls WHERE turn_id = ? ORDER BY id ASC", (turn_id,))
            .fetchall()
        )
        return [dict(r) for r in rows]
    except Exception:
        return []


def bump_loop(turn_id: str, *, blocked: bool = False) -> int:
    try:
        with _tx() as conn:
            conn.execute(
                "UPDATE turns SET loop_count = loop_count + 1, "
                "loop_blocked = loop_blocked + ? WHERE turn_id = ?",
                (1 if blocked else 0, turn_id),
            )
            row = conn.execute(
                "SELECT loop_count FROM turns WHERE turn_id = ?", (turn_id,)
            ).fetchone()
            return int(row["loop_count"]) if row else 0
    except Exception:
        return 0


def overdue_open_turns(now: float | None = None) -> list[dict[str, Any]]:
    now = now if now is not None else time.time()
    try:
        rows = (
            _conn()
            .execute(
                "SELECT * FROM turns WHERE status = 'open' AND deadline_at <= ? AND alerts_fired = 0",
                (now,),
            )
            .fetchall()
        )
        return [dict(r) for r in rows]
    except Exception:
        return []


def mark_alert_fired(turn_id: str, reason: str) -> bool:
    """Atomically claim a hang before sending (at most one attempt, even on failure)."""
    try:
        with _tx() as conn:
            cur = conn.execute(
                "UPDATE turns SET alerts_fired=1, last_reason=?, status='alerted' "
                "WHERE turn_id=? AND status='open' AND alerts_fired=0 AND deadline_at<=?",
                (reason, turn_id, time.time()),
            )
            if not cur.rowcount:
                return False
            conn.execute(
                "INSERT INTO stats(key,value) VALUES('alerts',1) "
                "ON CONFLICT(key) DO UPDATE SET value=value+1"
            )
        return True
    except Exception as exc:
        logger.debug("excubitor: alert claim failed: %s", exc)
        return False


def summarize() -> dict[str, Any]:
    stats = get_stats()
    day = utc_day()
    try:
        day_spend = spend_for("day", day, day)
    except Exception:
        day_spend = 0.0
    open_n = 0
    try:
        row = _conn().execute("SELECT COUNT(*) AS c FROM turns WHERE status = 'open'").fetchone()
        open_n = int(row["c"]) if row else 0
    except Exception:
        pass
    return {
        "duplicates_dropped": int(stats.get("duplicates_dropped", 0)),
        "loops_stopped": int(stats.get("loops_stopped", 0)),
        "cap_vetoes": int(stats.get("cap_vetoes", 0)),
        "alerts": int(stats.get("alerts", 0)),
        "cost_usd_total": float(stats.get("cost_usd_total", 0.0)),
        "day_spend_usd": float(day_spend),
        "open_turns": open_n,
        "day": day,
    }


def recent_alerts(limit: int = 5) -> list[dict[str, Any]]:
    """Newest alerted turns (fail-open → [])."""
    try:
        limit = max(1, min(20, int(limit)))
        rows = (
            _conn()
            .execute(
                "SELECT turn_id, session_id, platform, status, cost_usd, loop_count, "
                "alerts_fired, last_reason, opened_at, deadline_at, reply_at "
                "FROM turns WHERE alerts_fired > 0 OR status = 'alerted' "
                "ORDER BY COALESCE(deadline_at, opened_at) DESC LIMIT ?",
                (limit,),
            )
            .fetchall()
        )
        out = []
        for r in rows:
            out.append({k: r[k] for k in r.keys()})
        return out
    except Exception as exc:
        logger.debug("excubitor: recent_alerts failed: %s", exc)
        return []


def _relative_age(ts: float | None, now: float) -> str:
    """Human relative age; fail-open -> empty string."""
    try:
        if ts is None:
            return ""
        age = max(0, int(now - float(ts)))
        if age < 60:
            return f"{age}s ago"
        if age < 3600:
            return f"{age // 60}m ago"
        if age < 86400:
            return f"{age // 3600}h ago"
        return f"{age // 86400}d ago"
    except Exception:
        return ""


def _open_turns_detail(now: float, limit: int = 8) -> list[dict[str, Any]]:
    """Open turns with idle seconds (fail-open -> [])."""
    try:
        limit = max(1, min(20, int(limit)))
        rows = (
            _conn()
            .execute(
                "SELECT turn_id, session_id, platform, cost_usd, loop_count, "
                "opened_at, deadline_at FROM turns WHERE status = 'open' "
                "ORDER BY opened_at ASC LIMIT ?",
                (limit,),
            )
            .fetchall()
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            opened = float(r["opened_at"] or now)
            out.append(
                {
                    "turn_id": r["turn_id"],
                    "session_id": r["session_id"] or "",
                    "platform": r["platform"] or "",
                    "cost_usd": float(r["cost_usd"] or 0),
                    "loop_count": int(r["loop_count"] or 0),
                    "opened_at": r["opened_at"],
                    "deadline_at": r["deadline_at"],
                    "idle_seconds": max(0, int(now - opened)),
                }
            )
        return out
    except Exception as exc:
        logger.debug("excubitor: open_turns_detail failed: %s", exc)
        return []


def _stage_level(
    name: str,
    *,
    ratio: float,
    dupes: int,
    loops: int,
    open_n: int,
    overdue: int,
    recent_alert: bool,
) -> str:
    if name == "intake":
        return "warning" if dupes > 0 else "ok"
    if name == "work":
        return "warning" if loops > 0 else "ok"
    if name == "spend":
        if ratio >= 1.0:
            return "alert"
        if ratio >= 0.8:
            return "warning"
        return "ok"
    if name == "watch":
        if overdue > 0 or recent_alert:
            return "alert"
        if open_n > 0:
            return "warning"
        return "ok"
    return "ok"


def _read_spend_saved() -> float:
    try:
        row = _conn().execute("SELECT value FROM stats WHERE key = 'spend_saved_usd'").fetchone()
        if row is None:
            return 0.0
        return float(row["value"] if hasattr(row, "keys") else row[0])
    except Exception:
        return 0.0


def snapshot_for_ui() -> dict[str, Any]:
    """JSON-safe ledger snapshot for Desktop/Dashboard ``plugin_api``.

    Levels:
      - alert: any recent hang alert, or open turns past deadline
      - warning: day spend ≥ 80% of cap, or open turns with loop pressure
      - ok: otherwise

    Additive rich-chip fields (backward compatible): blocked_count,
    hang_idle_seconds, longest_silent_seconds, spend_saved_usd, stages,
    open_turns_detail, and per-alert alerted_at/relative. Fail-open.
    """
    try:
        from . import config as _config
    except ImportError:
        import config as _config  # type: ignore

    cfg = _config.settings()
    summary = summarize()
    day_cap = float(_config.budget(cfg, "day_budget_usd", 5.0))
    sess_cap = float(_config.budget(cfg, "session_budget_usd", 2.0))
    day_spend = float(summary.get("day_spend_usd") or 0.0)
    ratio = (day_spend / day_cap) if day_cap > 0 else 1.0
    alerts = recent_alerts(5)
    now = time.time()
    overdue = 0
    try:
        row = (
            _conn()
            .execute(
                "SELECT COUNT(*) AS c FROM turns WHERE status = 'open' AND deadline_at < ?",
                (now,),
            )
            .fetchone()
        )
        overdue = int(row["c"]) if row else 0
    except Exception:
        overdue = 0

    recent_alert = False
    for a in alerts[:3]:
        ts = a.get("deadline_at") or a.get("opened_at") or 0
        try:
            if float(ts) >= now - 3600:  # alerted in the last hour → red
                recent_alert = True
                break
        except Exception:
            pass

    if overdue > 0 or recent_alert or ratio >= 1.0:
        level = "alert"
    elif ratio >= 0.8 or int(summary.get("open_turns") or 0) > 0:
        level = "warning"
    else:
        level = "ok"

    open_detail = _open_turns_detail(now)
    hang_idle = 0
    try:
        hang_idle = max((int(t.get("idle_seconds") or 0) for t in open_detail), default=0)
    except Exception:
        hang_idle = 0

    dupes = int(summary.get("duplicates_dropped") or 0)
    loops = int(summary.get("loops_stopped") or 0)
    vetoes = int(summary.get("cap_vetoes") or 0)
    blocked = loops + vetoes
    saved = _read_spend_saved()
    open_n = int(summary.get("open_turns") or 0)
    stage_kw = dict(
        ratio=ratio,
        dupes=dupes,
        loops=loops,
        open_n=open_n,
        overdue=overdue,
        recent_alert=recent_alert,
    )
    stages = {
        "intake": {
            "level": _stage_level("intake", **stage_kw),
            "value": dupes,
            "label": "dupes dropped",
        },
        "work": {
            "level": _stage_level("work", **stage_kw),
            "value": loops,
            "label": "loops broken",
        },
        "spend": {
            "level": _stage_level("spend", **stage_kw),
            "value": round(day_spend, 4),
            "cap": day_cap,
            "saved": round(float(saved or 0.0), 4),
            "label": "$ spent/cap",
        },
        "watch": {
            "level": _stage_level("watch", **stage_kw),
            "value": hang_idle,
            "label": "longest silent turn (s)",
        },
    }

    return {
        "ok": True,
        "plugin": "excubitor",
        "version": "0.1.0",
        "level": level,
        "enabled": bool(cfg.get("enabled", True)),
        "day": summary.get("day"),
        "day_spend_usd": day_spend,
        "day_budget_usd": day_cap,
        "session_budget_usd": sess_cap,
        "spend_ratio": round(ratio, 4),
        "duplicates_dropped": dupes,
        "loops_stopped": loops,
        "cap_vetoes": vetoes,
        "blocked_count": blocked,
        "alerts": int(summary.get("alerts") or 0),
        "cost_usd_total": float(summary.get("cost_usd_total") or 0.0),
        "open_turns": open_n,
        "overdue_turns": overdue,
        "hang_idle_seconds": hang_idle,
        "longest_silent_seconds": hang_idle,
        "spend_saved_usd": round(float(saved or 0.0), 4),
        "reply_deadline_minutes": float(cfg.get("reply_deadline_minutes") or 5.0),
        "loop_threshold": int(cfg.get("loop_threshold") or 6),
        "notifier": str(cfg.get("notifier") or "log"),
        "stages": stages,
        "open_turns_detail": open_detail,
        "recent_alerts": [
            {
                "turn_id": a.get("turn_id"),
                "session_id": a.get("session_id") or "",
                "platform": a.get("platform") or "",
                "cost_usd": float(a.get("cost_usd") or 0),
                "loop_count": int(a.get("loop_count") or 0),
                "reason": (a.get("last_reason") or "")[:240],
                "opened_at": a.get("opened_at"),
                "deadline_at": a.get("deadline_at"),
                "alerted_at": a.get("deadline_at") or a.get("opened_at"),
                "relative": _relative_age(a.get("deadline_at") or a.get("opened_at"), now),
            }
            for a in alerts
        ],
        "chip_label": _chip_label(day_spend, day_cap, summary, overdue, level, recent_alert),
        "last_receipt": _last_receipt_for_ui(),
    }


def _last_receipt_for_ui() -> dict[str, Any] | None:
    try:
        r = get_receipt("")
        if not r:
            return None
        return {
            "turn_id": r["turn_id"],
            "text": r["text"][:500],
            "created_at": r["created_at"],
            "notable": r["notable"],
        }
    except Exception:
        return None


def _chip_label(day_spend, day_cap, summary, overdue, level, recent_alert) -> str:
    cap = f"{day_cap:g}" if float(day_cap).is_integer() else f"{day_cap:.2f}"
    spend = f"${day_spend:.2f}/${cap}"
    open_n = int(summary.get("open_turns") or 0)
    if level == "alert":
        return f"⚠ {spend} · {'hang' if overdue or recent_alert else 'cap'}"
    if open_n:
        return f"{spend} · {open_n} open"
    return spend


_ops_since_prune = 0


def maybe_prune(cfg: dict | None = None) -> None:
    """Periodic retention: drop stale seen_messages / closed turns. Fail-open."""
    global _ops_since_prune
    try:
        from . import config as _config

        cfg = cfg or _config.settings()
        every = max(1, int(cfg.get("prune_every_n_ops") or 50))
        _ops_since_prune += 1
        if _ops_since_prune < every:
            return
        _ops_since_prune = 0
        prune_old(
            seen_ttl_hours=float(cfg.get("seen_ttl_hours") or 168.0),
            turn_ttl_hours=float(cfg.get("turn_ttl_hours") or 72.0),
        )
    except Exception as exc:
        logger.debug("excubitor: prune skipped: %s", exc)


def prune_old(*, seen_ttl_hours: float = 168.0, turn_ttl_hours: float = 72.0) -> dict[str, int]:
    """Delete aged rows. Never touches open turns newer than deadline window."""
    now = time.time()
    seen_cut = now - max(1.0, seen_ttl_hours) * 3600.0
    turn_cut = now - max(1.0, turn_ttl_hours) * 3600.0
    deleted = {"seen": 0, "turns": 0, "tool_calls": 0}
    try:
        with _tx() as conn:
            cur = conn.execute("DELETE FROM seen_messages WHERE first_seen_at < ?", (seen_cut,))
            deleted["seen"] = cur.rowcount or 0
            # Closed turns only (replied/alerted/stopped)
            old = conn.execute(
                "SELECT turn_id FROM turns WHERE status != 'open' AND opened_at < ?",
                (turn_cut,),
            ).fetchall()
            ids = [r["turn_id"] for r in old]
            for tid in ids:
                conn.execute("DELETE FROM tool_calls WHERE turn_id = ?", (tid,))
                conn.execute("DELETE FROM turn_events WHERE turn_id = ?", (tid,))
                conn.execute("DELETE FROM receipts WHERE turn_id = ?", (tid,))
                conn.execute("DELETE FROM turn_aliases WHERE turn_id = ?", (tid,))
                deleted["tool_calls"] += 1
            if ids:
                conn.execute(
                    f"DELETE FROM turns WHERE turn_id IN ({','.join('?' * len(ids))})",
                    ids,
                )
                deleted["turns"] = len(ids)
        return deleted
    except Exception as exc:
        logger.debug("excubitor: prune_old failed: %s", exc)
        return deleted


def cap_tool_rows(turn_id: str, max_rows: int = 500) -> None:
    """Keep only the newest N tool_calls for a turn (bounds DB under loop storms)."""
    try:
        max_rows = max(50, int(max_rows))
        with _tx() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM tool_calls WHERE turn_id = ?", (turn_id,)
            ).fetchone()
            if not row or int(row["c"]) <= max_rows:
                return
            # Delete oldest overflow
            overflow = int(row["c"]) - max_rows
            conn.execute(
                "DELETE FROM tool_calls WHERE id IN ("
                "SELECT id FROM tool_calls WHERE turn_id = ? ORDER BY id ASC LIMIT ?"
                ")",
                (turn_id, overflow),
            )
    except Exception as exc:
        logger.debug("excubitor: cap_tool_rows failed: %s", exc)


# ---------------------------------------------------------------- turn receipts


def add_turn_event(turn_id: str, kind: str, **detail: Any) -> None:
    """Record a per-turn event (dedup / loop_block / loop_approve / veto). Fail-open."""
    if not turn_id:
        return
    try:
        with _tx() as conn:
            conn.execute(
                "INSERT INTO turn_events(turn_id, ts, kind, detail_json) VALUES (?, ?, ?, ?)",
                (turn_id, time.time(), kind, json.dumps(detail, ensure_ascii=False, default=str)),
            )
    except Exception as exc:
        logger.debug("excubitor: add_turn_event failed: %s", exc)


def turn_events(turn_id: str) -> list[dict[str, Any]]:
    try:
        rows = (
            _conn()
            .execute(
                "SELECT ts, kind, detail_json FROM turn_events WHERE turn_id = ? ORDER BY ts, id",
                (turn_id,),
            )
            .fetchall()
        )
        out = []
        for r in rows:
            try:
                detail = json.loads(r["detail_json"] or "{}")
            except Exception:
                detail = {}
            out.append(
                {"ts": r["ts"], "kind": r["kind"], **(detail if isinstance(detail, dict) else {})}
            )
        return out
    except Exception:
        return []


def find_turn_by_message_key(message_key: str) -> dict[str, Any] | None:
    if not message_key:
        return None
    try:
        row = (
            _conn()
            .execute(
                "SELECT * FROM turns WHERE message_key = ? ORDER BY opened_at DESC LIMIT 1",
                (message_key,),
            )
            .fetchone()
        )
        return dict(row) if row else None
    except Exception:
        return None


def save_receipt(turn_id: str, text: str, data: dict[str, Any], *, notable: bool) -> None:
    try:
        with _tx() as conn:
            conn.execute(
                "INSERT INTO receipts(turn_id, created_at, text, data_json, notable, notified) "
                "VALUES (?, ?, ?, ?, ?, 0) ON CONFLICT(turn_id) DO UPDATE SET "
                "created_at = excluded.created_at, text = excluded.text, "
                "data_json = excluded.data_json, notable = excluded.notable",
                (
                    turn_id,
                    time.time(),
                    text,
                    json.dumps(data, ensure_ascii=False, default=str),
                    1 if notable else 0,
                ),
            )
    except Exception as exc:
        logger.debug("excubitor: save_receipt failed: %s", exc)


def mark_receipt_notified(turn_id: str) -> bool:
    try:
        with _tx() as conn:
            cur = conn.execute(
                "UPDATE receipts SET notified=1 WHERE turn_id=? AND notified=0", (turn_id,)
            )
            return cur.rowcount == 1
    except Exception as exc:
        logger.debug("excubitor: mark_receipt_notified failed: %s", exc)
        return False


def _receipt_row(row: Any) -> dict[str, Any]:
    try:
        data = json.loads(row["data_json"] or "{}")
    except Exception:
        data = {}
    return {
        "turn_id": row["turn_id"],
        "created_at": row["created_at"],
        "text": row["text"],
        "notable": bool(row["notable"]),
        "notified": bool(row["notified"]),
        "data": data if isinstance(data, dict) else {},
    }


def get_receipt(turn_id: str = "") -> dict[str, Any] | None:
    """Receipt for ``turn_id`` (exact, then prefix match), or the latest when empty."""
    try:
        conn = _conn()
        if turn_id:
            row = conn.execute("SELECT * FROM receipts WHERE turn_id = ?", (turn_id,)).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM receipts WHERE substr(turn_id, 1, ?) = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (len(turn_id), turn_id),
                ).fetchone()
        else:
            row = conn.execute("SELECT * FROM receipts ORDER BY created_at DESC LIMIT 1").fetchone()
        return _receipt_row(row) if row else None
    except Exception:
        return None


def claim_dispatch(message_key: str) -> bool:
    """Reserve pre-auth deliveries separately: only admission writes seen/turns.

    A 30s lease bounds suppression if auth fails or the process dies before
    admission. Only a caller receiving False may count a dropped delivery.
    """
    try:
        from . import config
        from .hashing import is_text_fallback_key

        now = time.time()
        ttl = float(config.settings()["text_dedup_seconds"])
        with _tx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM dispatch_claims WHERE expires_at<=?", (now,))
            if is_text_fallback_key(message_key):
                conn.execute(
                    "DELETE FROM seen_messages WHERE message_key=? AND first_seen_at<?",
                    (message_key, now - ttl),
                )
            seen = conn.execute(
                "SELECT 1 FROM seen_messages WHERE message_key=?", (message_key,)
            ).fetchone()
            if seen:
                return False
            cur = conn.execute(
                "INSERT OR IGNORE INTO dispatch_claims VALUES (?,?)", (message_key, now + 30)
            )
            return cur.rowcount == 1
    except Exception as exc:
        logger.debug("excubitor: dispatch claim failed open: %s", exc)
        return True


def cumulative_cost_delta(session_id: str, total: float) -> float:
    """Session-cumulative total: atomically charge only increases; reset on decrease.

    A decrease is treated as a provider counter reset, charging its new value.
    """
    with _tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT total FROM cost_cursors WHERE session_id=?", (session_id,)
        ).fetchone()
        previous = float(row["total"]) if row else 0.0
        delta = total - previous if total >= previous else total
        conn.execute(
            "INSERT INTO cost_cursors VALUES (?,?) ON CONFLICT(session_id) "
            "DO UPDATE SET total=excluded.total",
            (session_id, total),
        )
    return max(0.0, delta)
