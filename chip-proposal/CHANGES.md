# Excubitor richer status chip — implementation proposal

**Status:** proposal only (files under `/workspace/chip-proposal/`).  
**Do not apply** to `/workspace/excubitor` from this write-up without an explicit implement pass.

**Baseline:** unified-package Desktop half as shipped in `desktop/plugin.js` +
`dashboard/plugin_api.py` + `ledger.snapshot_for_ui` / `recent_alerts`
(current tree that has those files: `/workspace/recheck/excubitor` @ `bd1d56b`;
`/workspace/excubitor` at proposal time still lacks `desktop/` / `dashboard/` —
same relative paths once synced). Hermes Desktop SDK conventions from
`/workspace/hermes-agent` (`STATUSBAR_AREAS`, `Popover*`, `StatusDot`,
`Tip`, `ctx.rest`, `ctx.os.writeClipboard`, disk ESM: `jsx`/`jsxs` only,
imports limited to `@hermes/plugin-sdk` / `react` / `react/jsx-runtime`).

**Evidence numbers** (from `docs/evidence/desktop_api_status.json`):  
`level=alert`, `$0.42/$5`, `spend_ratio=0.084`, `duplicates_dropped=3`,
`loops_stopped=2`, `cap_vetoes=1`, `alerts=1`, `open_turns=0`,
`chip_label="⚠ $0.42/$5 · hang"`, one recent alert reason
`"stuck after 6 search loops"`.

---

## 1. Product shape

### Status-bar chip (always visible)

| Element | Source field(s) | Notes |
|---|---|---|
| Health dot | `level` → `StatusDot` tone | `ok→good` (green), `warning→warn` (yellow), `alert→bad` (red), offline→`muted` |
| Mini spend bar | `day_spend_usd` / `day_budget_usd` (`spend_ratio`) | ~28×4px track; fill clamped 0–100%; color follows level |
| Open-turn count | `open_turns` | tabular numeral |
| Blocked count | **new** `blocked_count` (= `loops_stopped + cap_vetoes`) | tabular; tip explains |
| Hang indicator | **new** `hang_idle_seconds` + `level` | clock glyph + `Ns` / `Nm` when >0; bare "hang" when `level==alert` and idle is 0 |

Click opens a **Popover** (Radio pattern: `Popover` + `PopoverTrigger` +
`PopoverContent`, `side:'top'`, `align:'end'`), not a workspace pane. Keep the
optional right `panes` contribution for docked ledger; chip no longer depends on
`host.openWorkspace`.

### Dropdown contents

1. **Four stage rows** — Intake / Work / Spend / Watch  
   Each: colored `StatusDot` + stage name + key number.
2. **Open turns** — list from **new** `open_turns_detail[]` (`turn_id`, `platform`, `idle_seconds`, `loop_count`, `cost_usd`). Empty → "None open."
3. **Last 5 alerts** — existing `recent_alerts` + **new** `relative` string (and optional `alerted_at`). Reason + relative time.
4. **Actions** — Refresh (re-hit `ctx.rest('/status')`) and Copy status (`ctx.os.writeClipboard` with a one-line summary; fall back to `navigator.clipboard`).

Fail-open: any missing new field → treat as `0` / `[]` / omit stage color as `ok`. Old Desktop builds that ignore unknown JSON keep working; old backends without new fields still drive the chip via existing `level` / spend / open / alerts.

---

## 2. Status API payload additions (backward compatible)

`GET /api/plugins/excubitor/status` continues to return everything in today's
`snapshot_for_ui()`. **Additive only** — never rename/remove existing keys.
`dashboard/plugin_api._snapshot` fail-open stub gains the same keys with safe zeros.

### New / extended keys

```jsonc
{
  // --- existing (unchanged) ---
  "ok": true,
  "plugin": "excubitor",
  "version": "0.1.0",
  "level": "alert",
  "enabled": true,
  "day": "2026-11-05",
  "day_spend_usd": 0.42,
  "day_budget_usd": 5.0,
  "session_budget_usd": 2.0,
  "spend_ratio": 0.084,
  "duplicates_dropped": 3,
  "loops_stopped": 2,
  "cap_vetoes": 1,
  "alerts": 1,
  "cost_usd_total": 0.42,
  "open_turns": 0,
  "overdue_turns": 0,
  "reply_deadline_minutes": 5.0,
  "loop_threshold": 6,
  "notifier": "log",
  "chip_label": "⚠ $0.42/$5 · hang",
  "recent_alerts": [ /* … */ ],

  // --- NEW (fail-open defaults shown) ---
  "blocked_count": 3,              // loops_stopped + cap_vetoes
  "hang_idle_seconds": 0,          // max idle among open turns; else 0
  "spend_saved_usd": 0.0,          // from stats.spend_saved_usd (0 if unset)
  "longest_silent_seconds": 0,     // alias used by Watch stage (= hang_idle_seconds)
  "stages": {
    "intake": { "level": "warning", "value": 3,    "label": "dupes dropped" },
    "work":   { "level": "warning", "value": 2,    "label": "loops broken" },
    "spend":  { "level": "ok",      "value": 0.42, "cap": 5.0, "saved": 0.0, "label": "$ spent/cap" },
    "watch":  { "level": "alert",   "value": 0,    "label": "longest silent turn (s)" }
  },
  "open_turns_detail": []
}
```

`recent_alerts[]` items gain optional:

```jsonc
{
  "turn_id": "demo",
  "session_id": "sess",
  "platform": "cli",
  "cost_usd": 0.42,
  "loop_count": 6,
  "reason": "stuck after 6 search loops",
  "opened_at": 1793905837.2529702,
  "deadline_at": 1793905927.2529702,
  "alerted_at": 1793905927.2529702,
  "relative": "12m ago"
}
```

### Stage level rules (server)

| Stage | `alert` | `warning` | `ok` |
|---|---|---|---|
| Intake | — | `duplicates_dropped > 0` | else |
| Work | — | `loops_stopped > 0` | else |
| Spend | `spend_ratio ≥ 1` | `spend_ratio ≥ 0.8` | else |
| Watch | `overdue_turns > 0` or recent hang alert | `open_turns > 0` | else |

Overall `level` logic stays as today (alert > warning > ok); chip `StatusDot` maps that.

### `spend_saved_usd`

Optional ledger stat. When a cap veto fires, callers may bump `spend_saved_usd`
with the estimated avoided spend (best-effort). Snapshot reads it; missing → `0.0`.
No requirement to backfill historical vetoes.

---

## 3. Ledger changes (`ledger.py`)

See `/workspace/chip-proposal/proposed/ledger_snapshot_patch.py` for the full
helper + `snapshot_for_ui` body to merge, and section 5–6 of this doc for the
desktop half + tests. Summary of additive surface:

- `_relative_age(ts, now) -> str`
- `_open_turns_detail(now, limit=8) -> list[dict]`
- `_stage_level(...) -> str`
- `snapshot_for_ui()` gains `blocked_count`, `hang_idle_seconds`,
  `longest_silent_seconds`, `spend_saved_usd`, `stages`, `open_turns_detail`,
  and per-alert `alerted_at` / `relative`.
- Every new path wrapped fail-open (empty / zero on exception).

Optional in `spend.py` after `bump_stat("cap_vetoes")`: also bump
`spend_saved_usd` by estimated turn cost (extend `bump_stat` to accept float
deltas if needed).

---

## 4. `dashboard/plugin_api.py` fail-open defaults

Additive keys on the except-path stub in `_snapshot()`:

- `blocked_count: 0`
- `hang_idle_seconds: 0` / `longest_silent_seconds: 0`
- `spend_saved_usd: 0.0` / `spend_ratio: 0.0`
- `stages` with four `ok` / zero rows
- `open_turns_detail: []`

No route changes — still `GET /status` and `GET /health`.
Full patched module: `/workspace/chip-proposal/proposed/dashboard/plugin_api.py`.

---

## 5. Proposed `desktop/plugin.js`

Full replacement at `/workspace/chip-proposal/proposed/desktop/plugin.js`
(also inlined below as the canonical proposal).

Conventions matched:

- `STATUSBAR_AREAS.right`, `order: 125`
- `Popover` / `PopoverTrigger` / `PopoverContent` like Radio
- `StatusDot` tones `good|warn|bad|muted`
- `Tip` on the trigger
- `ctx.rest('/status')` poll 15s; fail-open offline chip
- `ctx.os.writeClipboard` for Copy status (navigator fallback)
- No JSX; only `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`

Chip face: health dot · shield · mini spend bar · open count · blocked count · hang idle.
Dropdown: Intake/Work/Spend/Watch rows · open turns · last 5 alerts · Refresh / Copy status.

---

## 6. Test additions

Full additions at `/workspace/chip-proposal/proposed/tests/test_desktop_api_rich.py`
(merge into `tests/test_desktop_api.py`):

- `test_snapshot_rich_chip_fields`
- `test_snapshot_open_turn_idle`
- `test_snapshot_stages_from_evidence_shape` (seeds evidence counters)
- `test_desktop_plugin_js_rich_chip_markers`

Existing tests stay green (additive keys; fail-open stub still valid).

---

## 7. Compatibility / rollout

1. Land ledger + plugin_api additions first (API-only; old chip ignores extras).
2. Replace `desktop/plugin.js` with the Popover chip.
3. Keep `chip_label` populated for any external consumer.
4. No Hermes core changes; SDK already exports `StatusDot` / `Popover*`.
5. Visual check: Capabilities → Plugins → enable Excubitor; status bar right.

---

## 8. Preview

- Rendered mock: `/workspace/chip-proposal/preview.png`
- HTML source: `/workspace/chip-proposal/preview-src/index.html`
- Labeled **PREVIEW MOCK**; numbers from `docs/evidence/desktop_api_status.json`
  (plus derived `blocked_count=3`, empty open-turn list, stages from those counters).

---

## 9. File checklist when implementing

| Path | Action |
|---|---|
| `ledger.py` | helpers + extend `snapshot_for_ui` / alert relative |
| `dashboard/plugin_api.py` | fail-open defaults for new keys |
| `desktop/plugin.js` | full replace with richer chip + dropdown |
| `tests/test_desktop_api.py` | four new tests |
| `spend.py` (optional) | bump `spend_saved_usd` on veto |
| `docs/evidence/desktop_api_status.json` | refresh after live verify |

Proposed artifacts under `/workspace/chip-proposal/proposed/`.


---



---

## 10. Verification (2026-11-05, Africa/Lagos)

Ran against an isolated copy (did **not** modify `/workspace/excubitor`):

- Base: `/workspace/recheck/excubitor` (has `desktop/` + `dashboard/` + `snapshot_for_ui`;
  `/workspace/excubitor` still lacks those paths at proposal time).
- Applied tree: `/workspace/chip-proposal/verify/excubitor`
- Applied: `desktop/plugin.js`, `dashboard/plugin_api.py`, ledger rich-chip helpers +
  `snapshot_for_ui`, `tests/test_desktop_api_rich.py`, `scripts/local_validate.py`
  (exclude `dashboard/` from coarse `sys.modules[` ban + define missing `_report`).

| Check | Result |
|---|---|
| `pytest` (hermes-venv) | **43 passed**, 0 failed |
| `ruff check` on touched py | **clean** |
| `node --check desktop/plugin.js` | **OK** |
| `hermes plugins validate .` | **passed** (pre-existing warnings on `scripts/live_scenarios.py` only) |
| `hermes plugins doctor . --ci` | **OK** (7 hooks) |
| `scripts/local_validate.py` | **OK** after exclude/`_report` fix |

Repo-wide `ruff check .` still reports pre-existing issues in `scripts/live_scenarios.py`
(not part of this chip proposal). Electron Desktop UI not launched (preview.png remains the mock).

## Appendix A — full proposed `desktop/plugin.js`

```javascript
/**
 * Excubitor Desktop half — richer status-bar chip + click dropdown.
 *
 * Unified package: plugins/excubitor/desktop/plugin.js
 * Data via ctx.rest('/status') → dashboard/plugin_api.py
 * Fail-open when the backend is unavailable.
 *
 * Disk plugins are uncompiled: jsx()/jsxs() only; import only
 * @hermes/plugin-sdk, react, react/jsx-runtime.
 */

import {
  Button,
  Popover,
  PopoverContent,
  PopoverTrigger,
  STATUSBAR_AREAS,
  StatusDot,
  Tip,
  host
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useCallback, useEffect, useState } from 'react'

const POLL_MS = 15_000
const SHIELD = 'M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3Z'

const LEVEL_TO_TONE = {
  ok: 'good',
  warning: 'warn',
  alert: 'bad',
  offline: 'muted'
}

const LEVEL_COLOR = {
  ok: 'var(--ui-accent)',
  warning: '#fbbf24',
  alert: 'var(--destructive, #f87171)',
  offline: 'var(--ui-text-quaternary)'
}

function money(n) {
  const v = Number(n)
  if (!Number.isFinite(v)) return '$0.00'
  return `$${v.toFixed(2)}`
}

function levelOf(data) {
  if (!data || data.ok === false) return 'offline'
  const lvl = data.level
  if (lvl === 'alert' || lvl === 'warning' || lvl === 'ok') return lvl
  return 'ok'
}

function fmtIdle(sec) {
  const s = Math.max(0, Number(sec) || 0)
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return `${Math.floor(s / 3600)}h`
}

function blockedOf(data) {
  if (!data) return 0
  if (Number.isFinite(Number(data.blocked_count))) return Number(data.blocked_count)
  return (Number(data.loops_stopped) || 0) + (Number(data.cap_vetoes) || 0)
}

function hangIdleOf(data) {
  if (!data) return 0
  const v = data.hang_idle_seconds ?? data.longest_silent_seconds
  return Number.isFinite(Number(v)) ? Number(v) : 0
}

function SpendBar({ ratio, level }) {
  const pct = Math.max(0, Math.min(100, (Number(ratio) || 0) * 100))
  const color = LEVEL_COLOR[level] || LEVEL_COLOR.ok
  return jsxs('span', {
    'aria-hidden': 'true',
    className: 'relative inline-block h-[4px] w-[28px] overflow-hidden rounded-sm',
    style: { background: 'color-mix(in oklab, var(--ui-text-quaternary) 35%, transparent)' },
    children: [
      jsx('span', {
        className: 'absolute inset-y-0 left-0 rounded-sm',
        style: { width: `${pct}%`, background: color }
      })
    ]
  })
}

function ChipFace({ data, loading }) {
  const level = levelOf(data)
  const color = LEVEL_COLOR[level] || LEVEL_COLOR.ok
  const tone = LEVEL_TO_TONE[level] || 'muted'
  const openN = (data && data.open_turns) || 0
  const blocked = blockedOf(data)
  const hangIdle = hangIdleOf(data)
  const ratio = (data && data.spend_ratio) || 0
  const showHang = level === 'alert' || hangIdle > 0

  return jsxs('span', {
    className:
      'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] tabular-nums',
    style: { color },
    children: [
      jsx(StatusDot, { tone }),
      jsx('svg', {
        width: 11,
        height: 11,
        viewBox: '0 0 24 24',
        'aria-hidden': 'true',
        children: jsx('path', { d: SHIELD, fill: 'currentColor' })
      }),
      loading && !data
        ? jsx('span', { children: '…' })
        : jsxs('span', {
            className: 'inline-flex items-center gap-1',
            children: [
              jsx(SpendBar, { ratio, level }),
              jsx('span', { title: 'Open turns', children: String(openN) }),
              jsx('span', { className: 'opacity-70', children: '·' }),
              jsx('span', {
                title: 'Blocked (loops + spend vetoes)',
                children: String(blocked)
              }),
              showHang
                ? jsxs('span', {
                    className: 'inline-flex items-center gap-0.5',
                    title: 'Hang / longest silent open turn',
                    children: [
                      jsx('span', { children: '⏱' }),
                      jsx('span', {
                        children: hangIdle > 0 ? fmtIdle(hangIdle) : 'hang'
                      })
                    ]
                  })
                : null
            ]
          })
    ]
  })
}

function StageRow({ name, stage }) {
  const s = stage || { level: 'ok', value: 0, label: '' }
  const tone = LEVEL_TO_TONE[s.level] || 'good'
  let valueText
  if (name === 'Spend') {
    valueText = `${money(s.value)}/${money(s.cap)} · saved ${money(s.saved)}`
  } else if (name === 'Watch') {
    valueText = fmtIdle(s.value)
  } else {
    valueText = String(s.value ?? 0)
  }
  return jsxs('div', {
    className: 'flex items-center justify-between gap-3 text-[0.75rem]',
    children: [
      jsxs('span', {
        className: 'inline-flex items-center gap-1.5 text-(--ui-text-tertiary)',
        children: [jsx(StatusDot, { tone }), jsx('span', { children: name })]
      }),
      jsx('span', {
        className: 'tabular-nums text-(--ui-text-secondary)',
        title: s.label || '',
        children: valueText
      })
    ]
  })
}

function Dropdown({ data, error, onRefresh, ctx }) {
  if (error || !data || data.ok === false) {
    return jsxs('div', {
      className: 'flex w-[280px] flex-col gap-2 p-3 text-sm',
      children: [
        jsx('div', { className: 'font-medium', children: 'Excubitor' }),
        jsx('div', {
          className: 'text-[0.75rem] text-(--ui-text-tertiary)',
          children:
            (data && data.message) ||
            error ||
            'Ledger backend unavailable. Is the excubitor agent plugin enabled?'
        }),
        jsx(Button, {
          type: 'button',
          variant: 'ghost',
          size: 'micro',
          className: 'self-start',
          onClick: onRefresh,
          children: 'Retry'
        })
      ]
    })
  }

  const stages = data.stages || {}
  const openDetail = Array.isArray(data.open_turns_detail) ? data.open_turns_detail : []
  const alerts = Array.isArray(data.recent_alerts) ? data.recent_alerts : []

  const copyStatus = () => {
    const line = [
      `Excubitor ${data.level}`,
      `${money(data.day_spend_usd)}/${money(data.day_budget_usd)}`,
      `open ${data.open_turns || 0}`,
      `blocked ${blockedOf(data)}`,
      `hang ${fmtIdle(hangIdleOf(data))}`,
      `dupes ${data.duplicates_dropped || 0}`,
      `loops ${data.loops_stopped || 0}`,
      `vetoes ${data.cap_vetoes || 0}`,
      `alerts ${data.alerts || 0}`
    ].join(' · ')
    const write =
      (ctx && ctx.os && typeof ctx.os.writeClipboard === 'function' && ctx.os.writeClipboard) ||
      null
    Promise.resolve()
      .then(() => (write ? write(line) : navigator.clipboard.writeText(line)))
      .then(() => {
        try {
          host.notify({ kind: 'info', message: 'Excubitor status copied' })
        } catch (_) {}
      })
      .catch(() => {
        try {
          host.notify({ kind: 'error', message: 'Copy failed' })
        } catch (_) {}
      })
  }

  return jsxs('div', {
    className: 'flex w-[300px] flex-col gap-3 p-3 text-sm',
    'data-plugin': 'excubitor-dropdown',
    children: [
      jsxs('div', {
        className: 'flex items-center justify-between gap-2',
        children: [
          jsx('div', { className: 'font-medium', children: 'Excubitor' }),
          jsxs('div', {
            className: 'flex items-center gap-1',
            children: [
              jsx(Button, {
                type: 'button',
                variant: 'ghost',
                size: 'micro',
                onClick: onRefresh,
                children: 'Refresh'
              }),
              jsx(Button, {
                type: 'button',
                variant: 'ghost',
                size: 'micro',
                onClick: copyStatus,
                children: 'Copy status'
              })
            ]
          })
        ]
      }),
      jsxs('div', {
        className: 'flex flex-col gap-1.5',
        children: [
          jsx(StageRow, { name: 'Intake', stage: stages.intake }),
          jsx(StageRow, { name: 'Work', stage: stages.work }),
          jsx(StageRow, { name: 'Spend', stage: stages.spend }),
          jsx(StageRow, { name: 'Watch', stage: stages.watch })
        ]
      }),
      jsx('div', {
        className: 'text-[0.7rem] font-medium text-(--ui-text-secondary)',
        children: 'Open turns'
      }),
      openDetail.length === 0
        ? jsx('div', {
            className: 'text-[0.7rem] text-(--ui-text-quaternary)',
            children: 'None open.'
          })
        : jsx('ul', {
            className: 'flex flex-col gap-1.5',
            children: openDetail.slice(0, 8).map((t, i) =>
              jsxs(
                'li',
                {
                  className:
                    'flex items-center justify-between gap-2 rounded border border-(--ui-stroke-secondary) px-2 py-1 text-[0.7rem]',
                  children: [
                    jsx('span', {
                      className: 'truncate text-(--ui-text-secondary)',
                      children: `${t.platform || '?'} · ${(t.turn_id || '').slice(0, 10)}`
                    }),
                    jsx('span', {
                      className: 'tabular-nums text-(--ui-text-quaternary)',
                      children: `idle ${fmtIdle(t.idle_seconds)}`
                    })
                  ]
                },
                t.turn_id || i
              )
            )
          }),
      jsx('div', {
        className: 'text-[0.7rem] font-medium text-(--ui-text-secondary)',
        children: 'Recent alerts'
      }),
      alerts.length === 0
        ? jsx('div', {
            className: 'text-[0.7rem] text-(--ui-text-quaternary)',
            children: 'None yet.'
          })
        : jsx('ul', {
            className: 'flex flex-col gap-2',
            children: alerts.slice(0, 5).map((a, i) =>
              jsxs(
                'li',
                {
                  className:
                    'rounded border border-(--ui-stroke-secondary) p-2 text-[0.7rem]',
                  children: [
                    jsx('div', {
                      className: 'text-(--ui-text-secondary)',
                      children: (a.reason || 'alert').slice(0, 160)
                    }),
                    jsx('div', {
                      className: 'mt-1 text-(--ui-text-quaternary)',
                      children: `${a.platform || '?'} · ${a.relative || ''}`.trim()
                    })
                  ]
                },
                a.turn_id || i
              )
            )
          })
    ]
  })
}

function useStatus(rest) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(() => {
    if (typeof rest !== 'function') {
      setError('ctx.rest unavailable')
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    Promise.resolve()
      .then(() => rest('/status'))
      .then((json) => {
        if (cancelled) return
        setData(json || null)
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        setError(String((err && err.message) || err || 'request failed'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [rest])

  useEffect(() => {
    const cancel = refresh()
    const id = setInterval(() => refresh(), POLL_MS)
    return () => {
      if (typeof cancel === 'function') cancel()
      clearInterval(id)
    }
  }, [refresh])

  return { data, error, loading, refresh }
}

function ChipWithDropdown({ rest, ctx }) {
  const state = useStatus(rest)
  const [open, setOpen] = useState(false)
  const level = levelOf(state.data)
  const tip =
    state.data && state.data.ok !== false
      ? jsxs('span', {
          children: [
            `Day ${money(state.data.day_spend_usd)} / ${money(state.data.day_budget_usd)}`,
            jsx('br', {}),
            `${state.data.open_turns || 0} open · ${blockedOf(state.data)} blocked · hang ${fmtIdle(hangIdleOf(state.data))}`,
            jsx('br', {}),
            level === 'alert'
              ? 'Hang / overdue — click for stages'
              : level === 'warning'
                ? 'Near cap or activity — click for stages'
                : 'Click for stage breakdown'
          ]
        })
      : jsxs('span', {
          children: [
            'Excubitor ledger unavailable',
            jsx('br', {}),
            'Enable the agent plugin + gateway; click to retry'
          ]
        })

  return jsxs(Popover, {
    open,
    onOpenChange: setOpen,
    children: [
      jsx(PopoverTrigger, {
        asChild: true,
        children: jsx(Tip, {
          label: tip,
          children: jsx('button', {
            type: 'button',
            className:
              'inline-flex h-full items-center rounded-none transition-colors hover:bg-(--chrome-action-hover)',
            'aria-label': 'Excubitor status',
            'aria-expanded': open,
            children: jsx(ChipFace, { data: state.data, loading: state.loading })
          })
        })
      }),
      jsx(PopoverContent, {
        side: 'top',
        align: 'end',
        className: 'p-0',
        'aria-label': 'Excubitor stages',
        'data-plugin': 'excubitor-panel',
        children: jsx(Dropdown, {
          data: state.data,
          error: state.error,
          onRefresh: state.refresh,
          ctx
        })
      })
    ]
  })
}

export default {
  id: 'excubitor',
  name: 'Excubitor',
  defaultEnabled: true,
  register(ctx) {
    const rest = ctx.rest ? (path, opts) => ctx.rest(path, opts) : null

    ctx.register({
      id: 'chip',
      area: STATUSBAR_AREAS.right,
      order: 125,
      render: () => jsx(ChipWithDropdown, { rest, ctx })
    })

    try {
      ctx.register({
        id: 'pane',
        area: 'panes',
        title: 'excubitor',
        data: { placement: 'right', width: '300px' },
        render: () => {
          function Live() {
            const state = useStatus(rest)
            return jsx(Dropdown, {
              data: state.data,
              error: state.error,
              onRefresh: state.refresh,
              ctx
            })
          }
          return jsx(Live, {})
        }
      })
    } catch (_) {
      /* panes optional on older hosts */
    }
  }
}

```

## Appendix B — proposed `dashboard/plugin_api.py` (full)

```python
"""Excubitor backend — mounted at /api/plugins/excubitor/.

Shared by Hermes Desktop (ctx.rest) and the web dashboard. Loaded via
importlib as a standalone module; sibling package imports are bootstrapped.
Fail-open: handlers return JSON and never raise to the client.

``fastapi`` is provided by the Hermes host at mount time. Import is lazy so
unit tests without FastAPI installed can still import sibling helpers.
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path
from typing import Any

logger = logging.getLogger("excubitor.plugin_api")

_ROOT = Path(__file__).resolve().parents[1]  # .../plugins/excubitor

try:
    from fastapi import APIRouter
except ImportError:  # pragma: no cover - host always has fastapi
    APIRouter = None  # type: ignore[misc, assignment]

router = APIRouter() if APIRouter is not None else None


def _ensure_ledger() -> Any:
    """Return the excubitor.ledger module."""
    try:
        from excubitor import ledger  # type: ignore

        return ledger
    except Exception:
        pass

    parent = str(_ROOT.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)

    pkg_name = "excubitor"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(_ROOT)]  # type: ignore[attr-defined]
        pkg.__file__ = str(_ROOT / "__init__.py")
        sys.modules[pkg_name] = pkg

    try:
        from excubitor import ledger  # type: ignore

        return ledger
    except Exception as exc:
        import importlib.util

        logger.debug("excubitor: package import failed (%s); loading modules", exc)
        for mod_name, filename in (
            ("excubitor.hashing", "hashing.py"),
            ("excubitor.config", "config.py"),
            ("excubitor.ledger", "ledger.py"),
        ):
            if mod_name in sys.modules:
                continue
            path = _ROOT / filename
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            mod.__package__ = "excubitor"
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        from excubitor import ledger  # type: ignore

        return ledger


def _snapshot() -> dict[str, Any]:
    try:
        return _ensure_ledger().snapshot_for_ui()
    except Exception as exc:
        logger.debug("excubitor: snapshot failed: %s", exc)
        return {
            "ok": False,
            "error": type(exc).__name__,
            "message": "Excubitor ledger unavailable",
            "level": "ok",
            "chip_label": "excubitor",
            "day_spend_usd": 0.0,
            "day_budget_usd": 5.0,
            "open_turns": 0,
            "duplicates_dropped": 0,
            "loops_stopped": 0,
            "cap_vetoes": 0,
            "alerts": 0,
            "recent_alerts": [],
            "enabled": True,
            "blocked_count": 0,
            "hang_idle_seconds": 0,
            "longest_silent_seconds": 0,
            "spend_saved_usd": 0.0,
            "spend_ratio": 0.0,
            "stages": {
                "intake": {"level": "ok", "value": 0, "label": "dupes dropped"},
                "work": {"level": "ok", "value": 0, "label": "loops broken"},
                "spend": {
                    "level": "ok",
                    "value": 0.0,
                    "cap": 5.0,
                    "saved": 0.0,
                    "label": "$ spent/cap",
                },
                "watch": {
                    "level": "ok",
                    "value": 0,
                    "label": "longest silent turn (s)",
                },
            },
            "open_turns_detail": [],
        }


if router is not None:

    @router.get("/status")
    async def status() -> dict[str, Any]:
        """Ledger snapshot for the Desktop status-bar chip / pane."""
        return _snapshot()

    @router.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "plugin": "excubitor"}
else:
    # Test harness without fastapi: keep callables importable.
    async def status() -> dict[str, Any]:  # type: ignore[misc]
        return _snapshot()

    async def health() -> dict[str, Any]:  # type: ignore[misc]
        return {"ok": True, "plugin": "excubitor"}

```

## Appendix C — ledger merge helpers (verified)

```python
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



```

## Appendix D — test additions

```python
"""Additive tests for richer Desktop chip payload — merge into test_desktop_api.py."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_snapshot_rich_chip_fields(ledger_mod):
    snap = ledger_mod.snapshot_for_ui()
    assert snap["ok"] is True
    assert "blocked_count" in snap
    assert snap["blocked_count"] == snap["loops_stopped"] + snap["cap_vetoes"]
    assert "hang_idle_seconds" in snap
    assert "longest_silent_seconds" in snap
    assert snap["hang_idle_seconds"] == snap["longest_silent_seconds"]
    assert "spend_saved_usd" in snap
    assert isinstance(snap["stages"], dict)
    for key in ("intake", "work", "spend", "watch"):
        assert key in snap["stages"]
        assert snap["stages"][key]["level"] in ("ok", "warning", "alert")
    assert isinstance(snap["open_turns_detail"], list)
    for a in snap["recent_alerts"]:
        assert "relative" in a
        assert "alerted_at" in a


def test_snapshot_open_turn_idle(ledger_mod):
    now = time.time()
    with ledger_mod._tx() as conn:
        conn.execute(
            "INSERT INTO turns(turn_id, message_key, session_id, platform, chat_id, "
            "opened_at, deadline_at, status, cost_usd, loop_count, loop_blocked, "
            "alerts_fired, last_reason, meta_json) "
            "VALUES ('open1','','s','cli','', ?, ?, 'open', 0.05, 1, 0, 0, '', '{}')",
            (now - 47, now + 200),
        )
    snap = ledger_mod.snapshot_for_ui()
    assert snap["open_turns"] >= 1
    assert len(snap["open_turns_detail"]) >= 1
    idle = snap["open_turns_detail"][0]["idle_seconds"]
    assert 40 <= idle <= 120
    assert snap["hang_idle_seconds"] >= 40
    assert snap["stages"]["watch"]["value"] == snap["hang_idle_seconds"]


def test_snapshot_stages_from_evidence_shape(ledger_mod):
    """Seed counters matching docs/evidence/desktop_api_status.json."""
    now = time.time()
    day = ledger_mod.utc_day()
    with ledger_mod._tx() as conn:
        conn.execute(
            "INSERT INTO spend(scope, scope_key, day, cost_usd) VALUES('day', ?, ?, 0.42) "
            "ON CONFLICT(scope, scope_key, day) DO UPDATE SET cost_usd=0.42",
            (day, day),
        )
        for key, val in (
            ("duplicates_dropped", 3),
            ("loops_stopped", 2),
            ("cap_vetoes", 1),
            ("alerts", 1),
            ("cost_usd_total", 0.42),
        ):
            conn.execute(
                "INSERT INTO stats(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, val),
            )
        conn.execute(
            "INSERT INTO turns(turn_id, message_key, session_id, platform, chat_id, "
            "opened_at, deadline_at, status, cost_usd, loop_count, loop_blocked, "
            "alerts_fired, last_reason, meta_json) "
            "VALUES ('demo','','sess','cli','', ?, ?, 'alerted', 0.42, 6, 0, 1, "
            "'stuck after 6 search loops', '{}')",
            (now - 90, now - 30),
        )
    snap = ledger_mod.snapshot_for_ui()
    assert snap["level"] == "alert"
    assert snap["duplicates_dropped"] == 3
    assert snap["loops_stopped"] == 2
    assert snap["cap_vetoes"] == 1
    assert snap["blocked_count"] == 3
    assert snap["stages"]["intake"]["value"] == 3
    assert snap["stages"]["work"]["value"] == 2
    assert snap["stages"]["spend"]["value"] == pytest.approx(0.42, abs=0.01)
    assert snap["stages"]["watch"]["level"] == "alert"
    assert "stuck" in snap["recent_alerts"][0]["reason"]
    assert snap["recent_alerts"][0]["relative"].endswith("ago")


def test_desktop_plugin_js_rich_chip_markers():
    js = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert "STATUSBAR_AREAS" in js
    assert "StatusDot" in js
    assert "Popover" in js
    assert "PopoverContent" in js
    assert "blocked_count" in js or "blockedOf" in js
    assert "hang_idle" in js or "hangIdle" in js
    assert "stages" in js
    assert "open_turns_detail" in js
    assert "writeClipboard" in js or "Copy status" in js
    assert "</" not in js
    assert "@hermes/plugin-sdk" in js

```

## Appendix E — `scripts/local_validate.py` (false-positive fix)

```python
#!/usr/bin/env python3
"""Offline stand-in for parts of `hermes plugins validate` when Hermes isn't installed.

Checks: plugin.yaml present, register(ctx) succeeds, declared hooks match what
register() wires, no obvious core-override patterns. Exit 0 on pass.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _report(errors: list[str], warnings: list[str]) -> None:
    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    manifest_path = ROOT / "plugin.yaml"
    if not manifest_path.exists():
        errors.append("missing plugin.yaml")
        _report(errors, warnings)
        return 1

    text = manifest_path.read_text(encoding="utf-8")
    name_m = re.search(r"^name:\s*(\S+)", text, re.M)
    hooks_block = re.search(r"^provides_hooks:\n((?:[ \t]*-[ \t]*.+\n)+)", text, re.M)
    declared = re.findall(r"-[ \t]*(\S+)", hooks_block.group(1)) if hooks_block else []
    if not name_m:
        errors.append("plugin.yaml missing name")
    if not (ROOT / "__init__.py").exists():
        errors.append("missing __init__.py")

    ban = re.compile(
        r"(AIAgent\.\w+\s*=|setattr\(\s*(?:sys\.modules|AIAgent)|sys\.modules\[)"
    )
    for path in ROOT.rglob("*.py"):
        # dashboard/plugin_api.py bootstraps sibling imports via sys.modules —
        # allowed by hermes plugins validate; exclude from this coarse ban.
        if (
            ".venv" in path.parts
            or "tests" in path.parts
            or "scripts" in path.parts
            or "dashboard" in path.parts
        ):
            continue
        if ban.search(path.read_text(encoding="utf-8")):
            errors.append(f"possible core override in {path.relative_to(ROOT)}")

    spec = importlib.util.spec_from_file_location(
        "excubitor_validate",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "excubitor_validate"
    mod.__path__ = [str(ROOT)]  # type: ignore[attr-defined]
    sys.modules["excubitor_validate"] = mod
    assert spec.loader
    spec.loader.exec_module(mod)

    registered_hooks: list[str] = []
    registered_commands: list[str] = []

    class Ctx:
        def register_hook(self, name, fn):
            registered_hooks.append(name)

        def register_command(self, name, handler, description="", **kw):
            registered_commands.append(name)

        def get_config(self, key, default=None):
            return default

        def set_config(self, key, value):
            return None

    try:
        mod.register(Ctx())
    except Exception as exc:
        errors.append(f"register(ctx) raised: {exc}")

    missing = sorted(set(declared) - set(registered_hooks))
    extra = sorted(set(registered_hooks) - set(declared))
    if missing:
        errors.append(f"declared but not registered hooks: {missing}")
    if extra:
        warnings.append(f"registered but undeclared hooks: {extra}")
    if "excubitor" not in registered_commands:
        errors.append("/excubitor command not registered")

    if not errors:
        print("OK: local validate passed (still run `hermes plugins validate` before catalog PR)")
        print("     hooks:", ", ".join(registered_hooks))
        print("     commands:", ", ".join(registered_commands))
    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    return 1 if errors else 0


if __name__ == "__main__":
    # Keep registered list visible on success via a second pass message
    raise SystemExit(main())

```
