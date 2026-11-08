# Excubitor

**Night sentry for Hermes Agent.**

Agents waste money and attention in four familiar ways: platforms re-deliver the same message and a second billable turn starts; the model calls the same tool with the same args and gets nothing new; spend creeps past what you meant to allow; a turn wedges with no reply and nobody notices. Excubitor watches every turn through **one SQLite ledger** and handles each:

- **Intake** — drop inbound replays before a second turn opens
- **Work** — block no-progress tool loops mid-turn
- **Spend** — hard-veto when the day/session USD cap is hit
- **Watch** — one alert if a turn stays silent past the deadline

```text
inbound → Intake → ledger → Work / Spend / Watch
                ↘ duplicate? skip
                ↘ loop?      block
                ↘ over cap?  veto
                ↘ hung?      alert (log / ntfy / webhook)
```

Fail-open on every hook — an internal error never takes Hermes down. Default notifier is `log` (offline). Optional `ntfy` / `webhook` only if you opt in. Requires Hermes `>=0.21.0`.

## Test it in 2 minutes

Hermes must already be on your PATH (`hermes --version`).

```bash
git clone https://github.com/catalystberry842-alt/excubitor.git
cd excubitor
mkdir -p ~/.hermes/plugins
ln -sfn "$(pwd)" ~/.hermes/plugins/excubitor
hermes plugins enable excubitor
hermes plugins doctor excubitor --ci
hermes plugins validate .
```

Expected: doctor reports `OK` with **8 hooks**, validate ends with **Validation passed**.

Or, when Hermes’s package manager can reach the network:

```bash
hermes plugins install catalystberry842-alt/excubitor
hermes plugins enable excubitor
hermes plugins doctor excubitor --ci
```

Use `owner/repo` or a full `https://github.com/...git` URL — not `github:owner/repo` (that resolves to the wrong host).

Unit tests (no live gateway needed):

```bash
pip install pytest ruff
pytest -q --ignore=tests/test_hermes_live.py
# expect: 92 passed
ruff check .
```

Then in any Hermes chat: `/excubitor` — you should see spend, duplicates, loops, alerts, and open turns.

## Try each guard

Temporarily tighten knobs under `plugins.entries.excubitor.settings` in `~/.hermes/config.yaml`, then reload/restart the gateway.

### 1. Intake — drop inbound replays

Enable a loopback webhook (secret below is intentionally insecure, local only):

```yaml
platforms:
  webhook:
    enabled: true
    extra:
      host: "127.0.0.1"
      port: 18644
      secret: "INSECURE_NO_AUTH"
      routes:
        probe:
          secret: "INSECURE_NO_AUTH"
          prompt: "Say PONG. No tools. {{__raw__}}"
          deliver: log
```

```bash
hermes gateway run
# other terminal:
DELIVERY="test-$(date +%s)"
curl -sS -X POST "http://127.0.0.1:18644/webhooks/probe" \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Delivery: $DELIVERY" \
  -H "X-GitHub-Event: ping" \
  -d '{"zen":"excubitor"}'
curl -sS -X POST "http://127.0.0.1:18644/webhooks/probe" \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Delivery: $DELIVERY" \
  -H "X-GitHub-Event: ping" \
  -d '{"zen":"excubitor"}'
```

**Expect:** first POST accepted; second is `duplicate` / skipped. `/excubitor` → `duplicates dropped` ≥ 1. Key survives a gateway restart in `~/.hermes/plugin-data/excubitor/data.db`.

### 2. Work — stop no-progress loops

Set `loop_threshold: 3`, `loop_approve_first: false`. In chat with tools on:

> Call the terminal tool with exactly `echo same-loop` five times in a row, then say DONE.

**Expect:** a block like  
`excubitor: no-progress loop on terminal (stagnant=3, threshold=3, …)`  
and `/excubitor` → `loops stopped` ≥ 1.

### 3. Spend — USD day cap

Set `day_budget_usd: 0.0001`, reload, send any short message.

**Expect:**  
`excubitor: day budget exhausted ($… / $0.00)`  
Restore a normal budget after.

### 4. Watch — silent-hang alarm

Set `reply_deadline_minutes: 0.5`, `watch_interval_seconds: 10`, `notifier: log` (or `ntfy` + a random topic). Start a turn that never replies; wait ~1 minute.

**Expect:** one alert titled `Excubitor: turn hung` (log line, or a single ntfy push). `/excubitor` → `alerts fired` ≥ 1.

## Turn receipts

When a turn ends, Excubitor writes a one-line receipt from its ledger: tool calls, duplicates dropped, loops and what was blocked, spend against the day cap, any cap veto, and idle or hang time. A receipt from the test suite:

```
Turn t_ab12 · telegram · 6 tool calls · looped 6× on web_search, blocked at #6 · spent $0.42 of $5 day cap · replied 21:09 (0s)
```

- `/excubitor receipt` shows the latest receipt. `/excubitor receipt <turn_id>` shows one turn (a prefix of the id works).
- The dashboard status API includes it as `last_receipt`.
- Only turns where something happened (a dedup, loop block, cap veto, or hang) go through your notifier. Clean turns are stored but stay quiet. A hang already sends its own alert, so its receipt is stored without a second ping.
- Turn it off with `receipts_enabled: false`.

## Config

Under `plugins.entries.excubitor.settings` (also drives Desktop → Settings → Plugins via `config_schema`):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Master kill switch |
| `day_budget_usd` | `5.0` | Day spend cap (hard veto) |
| `session_budget_usd` | `2.0` | Lifetime session cap (does not reset at UTC midnight) |
| `reply_deadline_minutes` | `5.0` | Hang-alert deadline |
| `loop_threshold` | `6` | Base no-progress count (tightens with spend) |
| `loop_approve_first` | `true` | First detection per tool per turn → approve; later → block |
| `receipts_enabled` | `true` | Store a receipt per turn; notify only notable turns |
| `notifier` | `log` | `log` \| `ntfy` \| `webhook` |
| `ntfy_topic` | `""` | Topic when `notifier=ntfy` |
| `webhook_url` | `""` | HTTPS URL when `notifier=webhook` |
| `watch_interval_seconds` | `30` | Background scan period |
| `price_table` | `{}` | Model → input/output USD per million token overrides; longest model match wins |
| `text_dedup_seconds` | `120` | Short replay window when no platform message ID exists |
| `seen_ttl_hours` | `168` | Retention of admitted message IDs |
| `turn_ttl_hours` | `72` | Retention of closed turns and their receipts/history |
| `max_tool_rows_per_turn` | `500` | Bound retained tool history per turn |
| `prune_every_n_ops` | `50` | Retention sweep frequency |
| `allow_private_webhooks` | `false` | Opt in to private HTTPS destinations (HTTP only on loopback); no redirects/proxies |

Kill switch: `enabled: false` or `hermes plugins disable excubitor`.
A malformed setting falls back independently with a warning; it cannot reset other settings.
Budget `0` is a hard stop, not an unset value. Day budgets reset at UTC midnight; session budgets last for the session.

Webhook URLs require HTTPS, except `http://localhost` / loopback IPs for local tests.
Non-public literal and resolved addresses are rejected unless `allow_private_webhooks` is true;
transport pins the validated address while preserving TLS certificate verification. Redirects and proxies are disabled.

## Desktop status chip (optional)

Unified package: `desktop/plugin.js` + `dashboard/plugin_api.py`.

- Status-bar chip: day spend vs cap, open-turn count, color by level (`ok` / `warning` / `alert`)
- Click → ledger pane: duplicates, loops, vetoes, alerts
- Enable the **Desktop** half under **Capabilities → Plugins** (keep the agent plugin enabled so `/api/plugins/excubitor/status` mounts)

CLI/gateway alone still get hooks + `/excubitor` — Desktop is optional polish.

**Authentication:** `GET /api/plugins/excubitor/status` (the plugin's `/status` route)
returns session IDs, turn metadata and spend. It relies entirely on the Hermes host's
authentication and access controls; Excubitor adds no separate API credential.
Do not expose this route publicly without host authentication.

## Limitations

- `event.internal` bypasses gateway hooks — Excubitor never sees those paths.
- Detects hangs; does not unwedge them — you still restart / clear the stuck turn.
- Cost is estimated unless the hook payload carries an explicit per-call USD figure. Cache tokens are included at the input rate (no provider-specific cache discounts/write premiums). `total_cost` alone is treated as a running session total and only its delta is charged; a decrease starts a new counter. Providers with different semantics should supply `cost_usd` instead.
- Pre-dispatch duplicates use a separate atomic 30-second reservation, not an admitted ledger row. If auth rejects a message or a process dies, the reservation expires; retries during that lease can be skipped. Durable dedup/turns begin only after admission.
- Receipt and hang claims happen before sending: at most one notification attempt per turn, not guaranteed delivery across a crash/transport failure.
- Gateway association requires the host's task-local route key (`hermes_cli.plugin_session.session_key`) or explicit source identity. Without it Excubitor does not guess across chats; an unbound gateway row may remain open.
- Current Hermes fires `post_llm_call` once after the tool loop, so it closes a turn. Explicit intermediate `tool_calls` / `is_final=false` signals are ignored; other hook semantics need a host adapter.
- Without `plugin_db`, fallback state is shared between threads within this process, but is neither durable nor shared across processes.
- CLI/TUI may not fire `pre_gateway_dispatch`; those turns open the ledger later (no inbound dedup there).
- Public ntfy topics are world-readable — use a long random topic.

## License

MIT
