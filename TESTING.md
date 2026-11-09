# Testing Excubitor yourself

You do **not** need to be a Hermes expert. A laptop (Windows, macOS, or Linux) is enough. Phone optional for ntfy alerts.

## 0. What you need

- Python 3.11+
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) installed (`hermes` on your PATH)
- An OpenRouter API key (for the loop + cap scenarios that call a model) — free-tier / cheap models are fine
- Optional: [ntfy app](https://ntfy.sh) on your phone

## 1. Install Hermes (if you do not have it)

Follow the official install for your OS, then confirm:

```bash
hermes --version
```

Put your key in `~/.hermes/.env`:

```bash
echo 'OPENROUTER_API_KEY=sk-or-...' >> ~/.hermes/.env
```

## 2. Install Excubitor

```bash
# Correct forms for current Hermes:
hermes plugins install catalystberry842-alt/excubitor
# or: hermes plugins install https://github.com/catalystberry842-alt/excubitor.git

hermes plugins enable excubitor
hermes plugins doctor excubitor --ci
```

> Do **not** use `github:owner/repo` — Hermes turns that into `github.com/github:owner/repo` and the clone fails.

Or from a git clone (recommended while developing):

```bash
git clone https://github.com/catalystberry842-alt/excubitor.git
cd excubitor
mkdir -p ~/.hermes/plugins
# Windows (PowerShell): New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.hermes\plugins\excubitor" -Target (Get-Location)
ln -sfn "$(pwd)" ~/.hermes/plugins/excubitor
hermes plugins enable excubitor
hermes plugins doctor excubitor --ci
```

## 3. Configure ntfy on your phone (scenario D)

1. Install **ntfy** (App Store / Play Store / F-Droid).
2. Pick a random topic, e.g. `excubitor-$(openssl rand -hex 8)` → something like `excubitor-a1b2c3d4e5f60708`.
3. In the app: **Subscribe to topic** → paste that exact string.
4. Edit Hermes config (`~/.hermes/config.yaml`) — merge carefully under the existing tree:

```yaml
plugins:
  enabled:
    - excubitor
  entries:
    excubitor:
      settings:
        enabled: true
        day_budget_usd: 5.0
        session_budget_usd: 2.0
        reply_deadline_minutes: 5.0
        loop_threshold: 6
        loop_approve_first: true
        notifier: ntfy
        ntfy_topic: "excubitor-YOUR-RANDOM-TOPIC"
        watch_interval_seconds: 15
```

5. Restart the Hermes gateway / agent so the plugin reloads.

## 4. Four scenarios

### A — Duplicate drop (webhook)

Add a webhook platform to `~/.hermes/config.yaml` (loopback only — the secret below is intentionally insecure):

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
# expect accepted / 202
curl -sS -X POST "http://127.0.0.1:18644/webhooks/probe" \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Delivery: $DELIVERY" \
  -H "X-GitHub-Event: ping" \
  -d '{"zen":"excubitor"}'
# expect {"status":"duplicate",...}
```

**Expected:** second POST is `duplicate`. After restarting the gateway, Excubitor’s `seen_messages` row for that delivery should still exist under `~/.hermes/plugin-data/excubitor/data.db` (Hermes may also keep its own delivery idempotency).

### B — Loop block

In a Hermes chat / CLI with tools enabled and Excubitor on (`loop_threshold: 3`, `loop_approve_first: false` for a quick demo):

> Call the terminal tool with exactly `echo same-loop` five times in a row, then say DONE.

**Expected:** after a few identical no-progress calls, the agent shows a block like:

`excubitor: no-progress loop on terminal (stagnant=3, threshold=3, …)`

Then `/excubitor` shows `loops stopped` ≥ 1.

### C — Dollar cap

Temporarily set `day_budget_usd: 0.0001`, restart/reload, send any short chat message.

**Expected:** turn vetoed immediately with something like:

`excubitor: day budget exhausted ($… / $0.00)`

Restore a normal budget afterward.

### D — Hang alarm → phone

Temporarily set `reply_deadline_minutes: 0.5` (30s) and `watch_interval_seconds: 10`. Start a turn that never replies (or run the automated hang lab in §5). Wait ~1 minute.

**Expected:**

- One push on your phone: title `Excubitor: turn hung`
- Verify from a laptop:

```bash
curl -sS "https://ntfy.sh/excubitor-YOUR-RANDOM-TOPIC/json?poll=1"
```

You should see a JSON line with `"title":"Excubitor: turn hung"`. Only **one** alert per turn (`alerts_fired`).

### Bonus checks

```text
/excubitor
```

Shows spend, duplicates, loops, alerts, open turns.

Fail-open: if you break the plugin on purpose, Hermes should keep answering (Excubitor logs a debug error and returns).

## 5. Automated live suite (optional)

From a clone, with Hermes importable and `hermes` on PATH:

```bash
export OPENROUTER_API_KEY=...          # or rely on ~/.hermes/.env
export HERMES_AGENT_ROOT=/path/to/hermes-agent   # checkout with run_agent.py
export EXCUBITOR_NTFY_TOPIC=excubitor-$(openssl rand -hex 8)
# optional: export HERMES_BIN=$(which hermes)

python tests/dev/live_scenarios.py
```

Evidence lands in `docs/evidence/` (no secrets). Unit tests only:

```bash
cd /path/to/excubitor
pip install pytest pytest-cov ruff
pytest -q --ignore=tests/test_hermes_live.py
```

Optional Hermes-integration tests (skipped unless a checkout is found / `HERMES_AGENT_ROOT` is set):

```bash
export HERMES_AGENT_ROOT=/path/to/hermes-agent
pytest -q tests/test_hermes_live.py
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `github.com/github:…` clone error | Use `owner/repo` or a full `https://github.com/…git` URL — not `github:owner/repo` |
| Plugin not loaded | `hermes plugins list`; ensure symlink/path + `enabled` |
| Gateway “aiohttp not installed” | Install aiohttp into Hermes’s **runtime** (the env that runs `hermes`) |
| ntfy 429 | Topic rate-limited; wait or change topic |
| No loop blocks | Lower `loop_threshold`; set `loop_approve_first: false`; ensure tools actually run |
| Cap not vetoing | Confirm settings under `plugins.entries.excubitor.settings` and reload |
| `python: install failed` during `plugins install` | Box/PM SSL issues — fall back to the symlink method in §2 |
