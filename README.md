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

Expected: doctor reports `OK` with **7 hooks**, validate ends with **Validation passed**.

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
# expect: 37 passed
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

## Config

Under `plugins.entries.excubitor.settings` (also drives Desktop → Settings → Plugins via `config_schema`):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Master kill switch |
| `day_budget_usd` | `5.0` | Day spend cap (hard veto) |
| `session_budget_usd` | `2.0` | Per-session cap |
| `reply_deadline_minutes` | `5.0` | Hang-alert deadline |
| `loop_threshold` | `6` | Base no-progress count (tightens with spend) |
| `loop_approve_first` | `true` | First loop → approve; later → block |
| `notifier` | `log` | `log` \| `ntfy` \| `webhook` |
| `ntfy_topic` | `""` | Topic when `notifier=ntfy` |
| `webhook_url` | `""` | HTTPS URL when `notifier=webhook` |
| `watch_interval_seconds` | `30` | Background scan period |

Kill switch: `enabled: false` or `hermes plugins disable excubitor`.

## Desktop status chip (optional)

Unified package: `desktop/plugin.js` + `dashboard/plugin_api.py`.

- Status-bar chip: day spend vs cap, open-turn count, color by level (`ok` / `warning` / `alert`)
- Click → ledger pane: duplicates, loops, vetoes, alerts
- Enable the **Desktop** half under **Capabilities → Plugins** (keep the agent plugin enabled so `/api/plugins/excubitor/status` mounts)

CLI/gateway alone still get hooks + `/excubitor` — Desktop is optional polish.

## Limitations

- `event.internal` bypasses gateway hooks — Excubitor never sees those paths.
- Detects hangs; does not unwedge them — you still restart / clear the stuck turn.
- Cost is estimated unless the hook payload carries an explicit USD figure.
- CLI/TUI may not fire `pre_gateway_dispatch`; those turns open the ledger later (no inbound dedup there).
- Public ntfy topics are world-readable — use a long random topic.

## License

MIT
