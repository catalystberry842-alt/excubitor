# Bugfix sweep evidence — 2026-11-08 (Africa/Lagos)

Base: rich-chip `320d1c7`. Changes are isolated on `bugfix-sweep`; main and the catalog pin are unchanged.

## Reproduction and verification

The first 29 audit regression cases were run against the unchanged base before implementation:
**29 failed**. They covered each confirmed finding, likely lifecycle/race issue and the smaller
accounting, notifier and documentation findings. Ten additional compatibility/concurrency cases
and one real Node-rendered chip case were added while hardening the fixes.

Current total: **92 passed**, including the existing 52 tests and 40 new cases. The existing
receipt test now expects one notification rather than the previous erroneous two. The gateway
close test now provides the actual task-local route identity, not an unsafe global newest-turn guess.

Commands:

```sh
python -m pytest -q
uvx ruff check .
node --check desktop/plugin.js
hermes plugins validate .
hermes plugins doctor . --ci
```

Results: pytest passes (two upstream Hermes deprecation warnings), Ruff clean, Node syntax clean,
manifest validation passes, doctor confirms **0 tools / 8 hooks**. Doctor must target `.` or the
absolute checkout path: the installed `excubitor` ID on this box resolves to an older checkout.

## Explicit boundaries

- `post_llm_call` is a turn-end hook in the available Hermes source:
  `agent/turn_finalizer.py::_apply_output_hooks` runs it once after the tools. Keep this closure;
  ignore explicit intermediate tool calls / `is_final=false` rather than removing the hook.
- Gateway route matching optionally uses `hermes_cli.plugin_session.session_key()`. Current
  validation flags this as a **private-core import / catalog rule 9 warning**, although it is the
  host's task-local route accessor. Without that accessor or explicit source/session-key kwargs,
  no cross-chat association is guessed. A supported `ctx` route accessor or richer hook payload
  is needed to remove this compatibility warning without regressing isolation/false-hang handling.
- Validation also reports existing private-core references in the live scenario helper script;
  no new host runtime rebinding was added.
- Pre-auth dispatch claims live in a separate table, expire after 30 seconds, and never create
  seen-message or turn rows until admission. A crash/rejection can suppress a retry within that
  lease; a delay beyond it can allow another delivery. Exactly-once ingress needs host support.
- Receipt/hang claims guarantee at most one notification **attempt**, not guaranteed delivery
  across crashes or transport errors.
- Cache buckets are counted without double counting canonical/OpenAI prompt totals, at the
  input-token rate. Provider-specific cache discounts/write premiums are not modeled.
- Ambiguous `total_cost` is treated as a cumulative session counter; explicit per-call USD wins.
  Providers with per-call `total_cost` semantics should expose `cost_usd` instead.
- Shared-memory fallback is shared between threads, scoped to the current DB identity, and
  process-local/non-durable; it is not a cross-process replacement for `plugin_db`.
- `/status` authentication remains the host's responsibility, now documented prominently.
- No real signed-in desktop session was exercised; the rendering regression evaluates the
  actual `ChipFace` function in Node with JSX/SDK stubs. No outbound notifier was sent.
