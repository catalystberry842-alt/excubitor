# Excubitor demo script (< 90 seconds)

Target: one take, ~75–85 s. Show usefulness + reliability + polish.

## Setup before recording

1. Hermes with Excubitor enabled (`hermes plugins enable excubitor`).
2. Notifier = `log` (or a visible ntfy topic on your phone).
3. Lower knobs for the demo so loops trip fast:
   ```yaml
   plugins:
     entries:
       excubitor:
         settings:
           loop_threshold: 3
           loop_approve_first: false
           reply_deadline_minutes: 0.15   # ~9s for hang beat
           day_budget_usd: 5
           session_budget_usd: 2
           notifier: log
   ```
4. Terminal A: gateway logs (`hermes logs -f` or the agent log).
5. Terminal B: chat surface (CLI or Telegram).

## Beat sheet

| t | Visual | Say |
|---|---|---|
| 0–8 s | Title card / README one-liner | "Excubitor — Latin for night sentry. One ledger watches every Hermes turn." |
| 8–22 s | Diagram or four words on screen: Intake · Work · Spend · Watch | "Not four plugins — one spine. Dedup, loop break, spend cap, hang alert." |
| 22–38 s | Send the same Telegram message twice (or simulate redelivery). Second vanishes; log shows `pre_gateway_dispatch skip` / `/excubitor` duplicates++ | "Intake: duplicate inbound is dropped before a second billable turn." |
| 38–58 s | Prompt that triggers repeated `tool_search` / same tool+args. Show block message in chat | "Work: no-progress loops — same tool, same args, no new results — get blocked. Spend tightens the threshold as budget burns." |
| 58–75 s | Start a turn, kill/pause the agent so no reply lands; wait for deadline; show ALERT log line (or ntfy) | "Watch: if nothing replies before the deadline, you get one alert with the ledger reason — loops, dollars, minutes." |
| 75–85 s | `/excubitor` status output | "One command: duplicates dropped, loops stopped, dollars tracked, alerts fired. Fail-open — we never break Hermes." |

## Closing line

"Excubitor doesn't pretend to patch core hangs — it stands watch, stops the waste it can, and wakes you when a turn goes dark."

## Recording tips

- Hard-cut between beats; keep UI zoomed so logs are readable.
- Prefer a real gateway surface for Intake; CLI alone cannot show `pre_gateway_dispatch` dedup.
- If live `tool_search` is slow, use a scripted stub agent or a tiny local model — the block message is the hero shot.
- Export ≤ 90 s, 1080p, no copyrighted music.

## Out of scope for the video

- Opening the catalog PR
- Desktop status chip (not in v0.1)
- Claiming internal-event coverage
