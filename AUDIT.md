# Excubitor security & quality audit

Date: 2026-11-05 (Africa/Lagos). Scope: `/workspace/excubitor` plugin code + live Hermes scenarios.
Method: manual review, ruff, mypy (partial), pytest-cov, live gateway/agent runs.

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
