"""Watch: background deadline scanner that alerts when a turn hangs without a reply."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import config, ledger
from .notify import send_alert

logger = logging.getLogger("excubitor.watch")

_watcher: Watcher | None = None
_lock = threading.Lock()


class Watcher:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="excubitor-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                cfg = config.settings()
                interval = float(cfg.get("watch_interval_seconds") or 30.0)
                if cfg.get("enabled", True):
                    self.scan_once(cfg)
            except Exception as exc:
                logger.debug("excubitor: watch loop error: %s", exc)
                interval = 30.0
            self._stop.wait(interval)

    def scan_once(self, cfg: dict[str, Any] | None = None) -> list[str]:
        """Check overdue open turns and fire one alert each. Returns fired turn_ids."""
        cfg = cfg or config.settings()
        fired: list[str] = []
        for turn in ledger.overdue_open_turns():
            try:
                reason = self._reason_for(turn)
                if not ledger.mark_alert_fired(str(turn["turn_id"]), reason):
                    continue
                title = "Excubitor: turn hung"
                body = reason
                send_alert(
                    cfg,
                    title=title,
                    body=body,
                    payload={
                        "plugin": "excubitor",
                        "kind": "hang",
                        "turn_id": turn.get("turn_id"),
                        "session_id": turn.get("session_id"),
                        "platform": turn.get("platform"),
                        "chat_id": turn.get("chat_id"),
                        "cost_usd": turn.get("cost_usd"),
                        "loop_count": turn.get("loop_count"),
                        "reason": reason,
                    },
                )
                try:
                    from . import receipt

                    tid = str(turn["turn_id"])
                    ledger.add_turn_event(tid, "hang")
                    # The hang alert above already pinged; store the receipt without a 2nd ping.
                    text = receipt.finalize(tid, cfg, notify=False)
                    if text:
                        logger.info("excubitor receipt: %s", text)
                except Exception as exc:
                    logger.debug("excubitor: hang receipt failed open: %s", exc)
                fired.append(str(turn["turn_id"]))
            except Exception as exc:
                logger.debug("excubitor: alert for %s failed open: %s", turn.get("turn_id"), exc)
        return fired

    @staticmethod
    def _reason_for(turn: dict[str, Any]) -> str:
        opened = float(turn.get("opened_at") or time.time())
        minutes = max(0.0, (time.time() - opened) / 60.0)
        loops = int(turn.get("loop_count") or 0)
        cost = float(turn.get("cost_usd") or 0.0)
        prior = turn.get("last_reason") or ""
        bits = [f"no reply in {minutes:.1f} min", f"${cost:.2f} spent"]
        if loops:
            bits.insert(0, f"stuck after {loops} loop stop(s)")
        if prior:
            bits.append(f"ledger note: {prior}")
        calls = ledger.all_tool_calls(str(turn.get("turn_id") or ""))
        if calls:
            last = calls[-1]
            bits.append(f"last tool={last.get('tool')}")
        return "excubitor: " + ", ".join(bits)


def ensure_watcher() -> Watcher:
    global _watcher
    with _lock:
        if _watcher is None:
            _watcher = Watcher()
            try:
                _watcher.start()
            except Exception as exc:
                logger.debug("excubitor: watcher start failed: %s", exc)
        return _watcher


def stop_watcher() -> None:
    global _watcher
    with _lock:
        if _watcher is not None:
            _watcher.stop()
            _watcher = None


def on_agent_loop_stopped(session_id="", reason="", **kwargs):
    """If the agent was interrupted, close the open turn without alerting as a hang."""
    try:
        cfg = config.settings()
        if not cfg.get("enabled", True):
            return None
        turn = ledger.find_open_turn_for_session(session_id or "")
        if turn:
            note = f"agent_loop_stopped: {reason or 'interrupted'}"
            ledger.set_turn_reason(str(turn["turn_id"]), note)
            ledger.mark_reply(str(turn["turn_id"]), status="stopped")
            from . import receipt

            receipt.finalize(str(turn["turn_id"]), cfg)
        return None
    except Exception as exc:
        logger.debug("excubitor: agent_loop_stopped failed open: %s", exc)
        return None
