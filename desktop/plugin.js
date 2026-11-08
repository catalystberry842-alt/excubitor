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
      jsxs('section', {
        className: 'flex flex-col gap-1',
        'aria-label': 'Latest turn receipt',
        children: [
          jsx('div', {
            className: 'text-[0.7rem] font-medium text-(--ui-text-secondary)',
            children: 'Latest turn receipt'
          }),
          jsx('p', {
            className: 'whitespace-pre-wrap break-words text-[0.7rem] text-(--ui-text-tertiary)',
            children: data.last_receipt?.text || 'No turn receipt yet.'
          })
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
