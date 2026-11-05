/**
 * Excubitor Desktop half — status-bar chip + ledger pane.
 *
 * Unified package: lives at plugins/excubitor/desktop/plugin.js.
 * Data via ctx.rest('/status') → dashboard/plugin_api.py (/api/plugins/excubitor/status).
 * Fail-open when the backend is unavailable (chip shows offline state).
 *
 * Disk plugins are uncompiled: jsx()/jsxs() only; import only
 * @hermes/plugin-sdk, react, react/jsx-runtime.
 */

import { STATUSBAR_AREAS, Tip, host } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useCallback, useEffect, useState } from 'react'

const POLL_MS = 15_000
const SHIELD = 'M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3Z'

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

function Chip({ data, onOpen, loading }) {
  const level = levelOf(data)
  const color = LEVEL_COLOR[level] || LEVEL_COLOR.ok
  const label =
    (data && data.chip_label) ||
    (loading ? '…' : level === 'offline' ? 'excubitor' : '—')

  const tip = data && data.ok !== false
    ? jsxs('span', {
        children: [
          `Day ${money(data.day_spend_usd)} / ${money(data.day_budget_usd)}`,
          jsx('br', {}),
          `${data.open_turns || 0} open · ${data.alerts || 0} alerts · ${data.loops_stopped || 0} loops`,
          jsx('br', {}),
          level === 'alert'
            ? 'Hang / overdue — click for ledger'
            : level === 'warning'
              ? 'Near cap or open turns — click for ledger'
              : 'Click for ledger summary'
        ]
      })
    : jsxs('span', {
        children: [
          'Excubitor ledger unavailable',
          jsx('br', {}),
          'Enable the agent plugin + gateway; click to retry'
        ]
      })

  return jsx(Tip, {
    label: tip,
    children: jsxs('button', {
      type: 'button',
      onClick: onOpen,
      className:
        'inline-flex h-full items-center gap-1 rounded-none px-1.5 text-[0.6875rem] tabular-nums transition-colors hover:bg-(--chrome-action-hover)',
      style: { color },
      'aria-label': 'Excubitor status',
      children: [
        jsx('svg', {
          width: 11,
          height: 11,
          viewBox: '0 0 24 24',
          'aria-hidden': 'true',
          children: jsx('path', { d: SHIELD, fill: 'currentColor' })
        }),
        jsx('span', { style: { fontVariantNumeric: 'tabular-nums' }, children: label })
      ]
    })
  })
}

function Row({ label, value }) {
  return jsxs('div', {
    className: 'flex justify-between gap-3 text-[0.75rem]',
    children: [
      jsx('span', { className: 'text-(--ui-text-tertiary)', children: label }),
      jsx('span', {
        className: 'tabular-nums text-(--ui-text-secondary)',
        children: value
      })
    ]
  })
}

function Pane({ data, error, onRefresh }) {
  if (error || !data || data.ok === false) {
    return jsxs('div', {
      className: 'flex h-full flex-col gap-2 p-3 text-sm',
      children: [
        jsx('div', { className: 'font-medium', children: 'Excubitor' }),
        jsx('div', {
          className: 'text-(--ui-text-tertiary) text-[0.75rem]',
          children:
            (data && data.message) ||
            error ||
            'Ledger backend unavailable. Is the excubitor agent plugin enabled?'
        }),
        jsx('button', {
          type: 'button',
          className: 'mt-2 self-start text-[0.75rem] text-(--ui-accent)',
          onClick: onRefresh,
          children: 'Retry'
        })
      ]
    })
  }

  const alerts = Array.isArray(data.recent_alerts) ? data.recent_alerts : []
  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-auto p-3 text-sm',
    children: [
      jsxs('div', {
        className: 'flex items-center justify-between',
        children: [
          jsx('div', { className: 'font-medium', children: 'Excubitor ledger' }),
          jsx('button', {
            type: 'button',
            className: 'text-[0.7rem] text-(--ui-accent)',
            onClick: onRefresh,
            children: 'Refresh'
          })
        ]
      }),
      jsxs('div', {
        className: 'flex flex-col gap-1.5',
        children: [
          jsx(Row, {
            label: 'Day spend',
            value: `${money(data.day_spend_usd)} / ${money(data.day_budget_usd)}`
          }),
          jsx(Row, { label: 'Open turns', value: String(data.open_turns || 0) }),
          jsx(Row, {
            label: 'Duplicates dropped',
            value: String(data.duplicates_dropped || 0)
          }),
          jsx(Row, {
            label: 'Loops stopped',
            value: String(data.loops_stopped || 0)
          }),
          jsx(Row, {
            label: 'Cap vetoes',
            value: String(data.cap_vetoes || 0)
          }),
          jsx(Row, {
            label: 'Est. $ tracked',
            value: money(data.cost_usd_total)
          }),
          jsx(Row, {
            label: 'Alerts fired',
            value: String(data.alerts || 0)
          }),
          jsx(Row, {
            label: 'Notifier',
            value: String(data.notifier || 'log')
          })
        ]
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
                      children: `${a.platform || '?'} · ${money(a.cost_usd)} · loops ${a.loop_count || 0}`
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

export default {
  id: 'excubitor',
  name: 'Excubitor',
  defaultEnabled: true,
  register(ctx) {
    const rest = ctx.rest ? (path, opts) => ctx.rest(path, opts) : null

    function openPane() {
      // Prefer host.openWorkspace (docked tab); fall back to notify.
      if (typeof host.openWorkspace === 'function') {
        host.openWorkspace('excubitor-ledger', {
          title: 'Excubitor',
          minWidth: 280,
          render: () => {
            function Live() {
              const state = useStatus(rest)
              return jsx(Pane, {
                data: state.data,
                error: state.error,
                onRefresh: state.refresh
              })
            }
            return jsx(Live, {})
          }
        })
        return
      }
      // Older hosts: tip already shows summary; toast a hint.
      try {
        host.notify({
          kind: 'info',
          message: 'Excubitor ledger — upgrade Desktop for the full pane'
        })
      } catch (_) {
        /* ignore */
      }
    }

    ctx.register({
      id: 'chip',
      area: STATUSBAR_AREAS.right,
      order: 125,
      render: () => {
        function LiveChip() {
          const state = useStatus(rest)
          return jsx(Chip, {
            data: state.data,
            loading: state.loading,
            onOpen: openPane
          })
        }
        return jsx(LiveChip, {})
      }
    })

    // Also register a right pane so users can keep it docked if they prefer.
    try {
      ctx.register({
        id: 'pane',
        area: 'panes',
        title: 'excubitor',
        data: { placement: 'right', width: '280px' },
        render: () => {
          function Live() {
            const state = useStatus(rest)
            return jsx(Pane, {
              data: state.data,
              error: state.error,
              onRefresh: state.refresh
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
