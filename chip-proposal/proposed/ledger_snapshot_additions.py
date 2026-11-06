"""MERGE into ledger.py (replace recent_alerts-adjacent region through snapshot_for_ui).

Verified under /workspace/chip-proposal/verify/excubitor (pytest 43 passed,
ruff clean on touched files, node --check desktop/plugin.js, hermes plugins
validate + doctor OK). Insert these helpers immediately before snapshot_for_ui
and replace snapshot_for_ui with the version below. Keep existing recent_alerts
and _chip_label unchanged.
"""

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
        rows = _conn().execute(
            "SELECT turn_id, session_id, platform, cost_usd, loop_count, "
            "opened_at, deadline_at FROM turns WHERE status = 'open' "
            "ORDER BY opened_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
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
        row = _conn().execute(
            "SELECT value FROM stats WHERE key = 'spend_saved_usd'"
        ).fetchone()
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
    day_cap = float(cfg.get("day_budget_usd") or 5.0)
    sess_cap = float(cfg.get("session_budget_usd") or 2.0)
    day_spend = float(summary.get("day_spend_usd") or 0.0)
    ratio = (day_spend / day_cap) if day_cap > 0 else 0.0
    alerts = recent_alerts(5)
    now = time.time()
    overdue = 0
    try:
        row = _conn().execute(
            "SELECT COUNT(*) AS c FROM turns WHERE status = 'open' AND deadline_at < ?",
            (now,),
        ).fetchone()
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

    if overdue > 0 or recent_alert:
        level = "alert"
    elif ratio >= 0.8 or int(summary.get("open_turns") or 0) > 0 or int(summary.get("alerts") or 0) > 0:
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
                "relative": _relative_age(
                    a.get("deadline_at") or a.get("opened_at"), now
                ),
            }
            for a in alerts
        ],
        "chip_label": _chip_label(day_spend, day_cap, summary, overdue, level),
    }


