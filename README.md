# Excubitor

**Latin: night sentry.** A Hermes Agent plugin that watches each turn through **one shared ledger**.

Hackathon entry for the [Nous Research Hermes Agent Plugin Hackathon](https://hermes-agent.nousresearch.com/) (due Nov 11 2026 5PM EST). Judged on usefulness, reliability, polish, creativity.

> Disclosure — network: optional only. Default notifier is **log**. If you set `notifier: ntfy` or `webhook`, Excubitor POSTs alert payloads to `https://ntfy.sh/<topic>` or your webhook URL. No credentials are stored. Background: one daemon thread scans open turns. State: SQLite under Hermes `plugin-data/excubitor/`. No telemetry. Fail-open on every internal error.

## The problem

Real Hermes incidents the sentry is aimed at:

| Issue | What users saw |
|---|---|
| [#143749](https://github.com/NousResearch/hermes-agent/issues/143749) | Cross-session re-delivery — the same inbound message runs a fresh turn (side effects, cost, trust loss). Class A asks for a durable inbound dedup ledger. |
| [#131145](https://github.com/NousResearch/hermes-agent/issues/131145) | First inbound turn wedges forever: typing forever, no provider call, no error, `active_agents` stuck. Users need an out-of-band hang signal. |
| [#71239](https://github.com/NousResearch/hermes-agent/issues/71239) | Telegram stays "connected" while the dispatcher is dead — silent no-reply. |
| [#96247](https://github.com/NousResearch/hermes-agent/issues/96247) | **1,523 successful `tool_search` calls** with no answer; identical-call stubs are advisory-only; failure-keyed guardrails never fire. |

Excubitor does not claim to fix those core bugs. It is a **plugin-side night sentry**: drop obvious replays it can see, stop no-progress tool loops, cap spend, and **alert** when a turn hangs without a reply — with reasons drawn from the same ledger.

## One ledger, four faces

```
                 ┌─────────────────────────────────────────┐
  inbound ──►    │              TURN LEDGER                 │
                 │  message_key · tools · cost · deadline   │
                 └─────────────────────────────────────────┘
                        │         │         │         │
                     Intake     Work      Spend     Watch
```

1. **Intake** (`pre_gateway_dispatch`) — key = `platform + chat_id + message_id` (fallback: hash of `raw_message` / text). Duplicate → `{"action":"skip"}`. New → open ledger entry + reply deadline timer.
2. **Work** (`pre_tool_call` / `post_tool_call`) — append each call (tool, normalized args hash, result hash). A **loop** is repeated calls with **no new results** (stagnant result hashes), not merely identical text. `tool_search` is included: the host unwraps `tool_call` bridges before hooks, and bare `tool_search` still hits `pre_tool_call`.
3. **Spend** (`post_api_request`, `pre_llm_call`, `pre_tool_call`) — add estimated USD to the same entry and day/session totals. Loop sensitivity **tightens** as budget is consumed; a loop that is also burning money escalates faster (`approve` → `block`). `pre_llm_call` vetoes new turns over the cap; `pre_tool_call` blocks mid-turn.
4. **Watch** (background scanner) — if the deadline passes with no outbound reply (`post_llm_call` never marked the turn), fire **one** alert via the configured notifier with a ledger-drawn reason, e.g. `stuck after 6 search loops, $0.40 spent, no reply in 5 min`.

Status: `/excubitor` or `/watch` prints duplicates dropped, loops stopped, $ tracked, alerts, open turns.

## Install (after you publish)

```bash
# from a public repo at a pinned commit (catalog) or local path while developing:
hermes plugins install /path/to/excubitor
hermes plugins enable excubitor
hermes plugins doctor excubitor --ci
```

Local hackathon checkout:

```bash
mkdir -p ~/.hermes/plugins
ln -s /workspace/excubitor ~/.hermes/plugins/excubitor
hermes plugins enable excubitor
```

## Config

Under `plugins.entries.excubitor.settings` (Desktop Plugins form is driven by `config_schema`):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Master kill switch |
| `day_budget_usd` | `5.0` | Day spend cap |
| `session_budget_usd` | `2.0` | Session spend cap |
| `reply_deadline_minutes` | `5.0` | Hang alert deadline |
| `loop_threshold` | `6` | Base no-progress count (tightens with spend) |
| `loop_approve_first` | `true` | First loop → human `approve`; later/`burning` → `block` |
| `notifier` | `log` | `log` \| `ntfy` \| `webhook` |
| `ntfy_topic` | `""` | ntfy.sh topic when `notifier=ntfy` |
| `webhook_url` | `""` | HTTPS URL when `notifier=webhook` |
| `price_table` | `{}` | Optional model → `{input_per_mTok, output_per_mTok}` overrides |
| `watch_interval_seconds` | `30` | Background scan period |

Cost: when `post_api_request` usage has no dollar field, Excubitor estimates from token counts × a small built-in price table (overridable).

## Limitations (honest)

- **`event.internal` bypasses gateway hooks** (`gateway/run_inbound.py`). Background wakes / kanban notifies that are marked internal never reach `pre_gateway_dispatch`. Documented Hermes behavior — Excubitor cannot cover that path.
- **Cannot fix a hang** — only detect and alert. Clearing a wedged gateway still needs an operator (or an out-of-band watchdog that restarts the gateway).
- **Dedup is best-effort on the hook surface.** CLI/TUI paths do not fire `pre_gateway_dispatch`; those turns open the ledger at `pre_llm_call` instead (no inbound dedup there).
- **Cost is estimated** unless the hook payload carries an explicit USD figure.
- **Desktop status chip** not shipped in v0.1 (would need the separate Desktop Plugin SDK surface).

## Tests

```bash
pip install 'pytest>=8,<9'
pytest -q
```

## Validate against Hermes

```bash
hermes plugins validate /path/to/excubitor --install-deps
hermes plugins doctor /path/to/excubitor --ci
```

## Catalog submission (not done in this workspace)

Entry shape lives in `catalog-entry.draft.yaml`. Submission = PR adding `plugin-catalog/excubitor.yaml` to `NousResearch/hermes-agent` pinning a **40-char commit SHA** of a **public** plugin repo, plus a &lt;90s demo video (see `DEMO.md`). This workspace does **not** open that PR.

## License

MIT
