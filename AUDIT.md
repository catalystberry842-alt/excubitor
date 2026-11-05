# Excubitor security & quality audit

Date: 2026-11-05 (Africa/Lagos). Scope: plugin code + live Hermes scenarios.
Method: manual review, ruff, mypy (partial), pytest-cov, live gateway/agent runs.
Independent recheck: fresh `/workspace/recheck` clone + Hermes `main` f87c541 + fresh venv/HERMES_HOME (see below).

## Summary

| Area | Result |
|---|---|
| Live scenarios A–G | **PASS** (see `docs/evidence/SUMMARY.json`) |
| Unit tests | 32 passed |
| Coverage | **~71%** line (`docs/evidence/pytest_cov.txt`) |
| Ruff | clean after config + autofix |
| mypy | soft check on leaf modules; `config.bind_ctx` typed as `Any` |

## Findings

| ID | Severity | Finding | Status |
|---|---|---|---|
| S1 | Medium | Alert/log bodies could leak secrets if tool args or user text were forwarded. | **Fixed** — `notify._sanitize_text` redacts `api_key`/`token`/`sk-`/`gho_`/`ghp_`; strips `user_message`/`args`/`result`/`prompt` from outbound payloads. |
| S2 | Medium | `webhook_url` / `ntfy_topic` accepted arbitrary strings (SSRF / path injection risk). | **Fixed** — HTTPS/HTTP-only URL validation; reject userinfo-in-URL; ntfy topic `[A-Za-z0-9_-]{1,64}` only. |
| S3 | Low | Ledger connection cached on `threading.local` could stick to a stale `HERMES_HOME` after env change (tests / multi-home). | **Fixed** — `_db_identity()` reopens when home/test path changes; `use_test_db` clears `db_id`. |
| S4 | Low | Unbounded `tool_calls` / `seen_messages` growth under loop storms or replay floods. | **Fixed earlier** — `max_tool_rows_per_turn` (default 500), `seen_ttl_hours` / `turn_ttl_hours` + `prune_old`. |
| S5 | Low | Blind `except Exception` in hooks. | **Accepted** — fail-open is a product requirement; ruff `BLE001` ignored intentionally. Logged at debug. |
| S6 | Info | Cost figures are estimates unless hooks provide USD. | **Documented** — README limitations + FAQ. |
| S7 | Info | ntfy.sh public topics are world-readable; rate limits (HTTP 429) observed under rapid retest. | **Documented** — TESTING.md warns to use a random topic; notifier fails open to log on 429. |
| S8 | Info | Concurrent chats share one SQLite WAL DB; Hermes `plugin_db` already uses WAL. Per-thread connections + short transactions. | **Accepted** — no cross-chat locks beyond SQLite; turn keys are session/message scoped. |
| S9 | Low | Missing `message_id` relied on raw hash; huge args could be expensive to hash. | **Covered** — intake fallback tested; `args_hash` / result hashing caps large blobs in `hashing.py`. |
| Q1 | Low | `notify.py` was under-tested (23% → ~64% after new tests). | **Fixed** — `tests/test_notify_security.py`. |
| Q2 | Low | Dead / unused paths minimal; naming consistent (`intake`/`work`/`spend`/`watch`). | **OK** |
| Q3 | Info | No type stubs for Hermes `PluginContext`. | **Accepted** — `Any` for ctx; mypy `--ignore-missing-imports`. |

## What was not claimed

- Excubitor does **not** fix Hermes core hangs or internal-event bypasses.
- Dedup across restart is durable in `plugin_db`; Hermes may also keep its own delivery idempotency — both observed in live A.
- CI badge is only valid once GitHub Actions on `main` is green after push.

## Evidence index

- `docs/evidence/SUMMARY.json` — pass/fail per scenario
- `docs/evidence/*_a_webhook_dup.json` … `*_f_failopen.json`
- `docs/evidence/ntfy_poll_verify.ndjson` — live hang alert on ntfy
- `docs/evidence/pytest_cov.txt`, `ruff.txt`


## Independent recheck (2026-11-05 ~20:00 Africa/Lagos)

| Check | Result |
|---|---|
| Fresh clone from GitHub | OK |
| Fresh Hermes `main` + venv (Python 3.14) | OK (`hermes` vgit.f87c541) |
| `hermes plugins validate` | PASS |
| `hermes plugins doctor excubitor --ci` | PASS (0 tools, 7 hooks) |
| Unit tests | 32 passed, ~71% coverage, ruff clean |
| Live A–F | ALL PASS (`docs/evidence/20261105T190238Z_*.json`) |
| CI `gh run list` | success on `main` (latest pin + evidence commits) |

### Fixes found during recheck

| ID | Severity | Finding | Status |
|---|---|---|---|
| D1 | High (docs) | README/TESTING said `hermes plugins install github:owner/repo`, which Hermes resolves to `github.com/github:owner/repo` (clone 404). | **Fixed** — docs now use `owner/repo` or full git URL. |
| D2 | Medium (docs) | TESTING.md §5 pointed at box-only paths (`/workspace/hermes-venv`, `/workspace/excubitor-devtools/...`). | **Fixed** — portable `scripts/live_scenarios.py` + env vars. |
| D3 | Low (tests) | `tests/test_hermes_live.py` hard-coded `/workspace/hermes-agent`. | **Fixed** — discovers via `HERMES_AGENT_ROOT` / common checkouts. |
| N1 | Info | Scenario A synthetic `MessageEvent` hook-skip was `false` while durable `seen_messages` + Hermes duplicate both held — chat_id shape differs from live webhook v2 tokens. | **Documented** — not a product false-fail; pass criteria already accepted Hermes-dup OR seen row. |

No product logic regressions found vs prior evidence.


## Desktop UI (2026-11-05)

Added unified Desktop half:

- `desktop/plugin.js` — status-bar chip + ledger pane (`STATUSBAR_AREAS.right`, optional `panes`, `host.openWorkspace`)
- `dashboard/plugin_api.py` — `GET /status` snapshot (shared Desktop/Dashboard mount)
- `ledger.snapshot_for_ui()` — level `ok|warning|alert`, recent alerts, chip label
- Tests: `tests/test_desktop_api.py` (5)
- `hermes plugins validate` reports `desktop surface` OK; doctor OK
- Agent hooks/commands unchanged when Desktop is absent
