import { useEffect, useState, useCallback } from 'react'
import { Timer, Trash2, RefreshCw, ChevronDown } from 'lucide-react'
import { Link } from 'react-router-dom'
import DashboardLayout from '../components/DashboardLayout.jsx'
import * as api from '../services/api.js'

const FILTERS = ['all', 'scheduled', 'executed', 'failed', 'cancelled']
const STATUS_TONE = { scheduled: '', executing: 'pill--warn', executed: 'pill--ok', failed: 'pill--warn', cancelled: 'pill--muted' }

function countdown(runAtIso, now) {
  const ms = new Date(runAtIso).getTime() - now
  if (ms <= 0) return 'due…'
  const s = Math.floor(ms / 1000)
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60
  const pad = (n) => String(n).padStart(2, '0')
  if (h > 0) return `in ${h}h ${pad(m)}m ${pad(sec)}s`
  if (m > 0) return `in ${m}m ${pad(sec)}s`
  return `in ${sec}s`
}

const summarize = (p) => Object.entries(p || {}).map(([k, v]) => `${k}=${v}`).join('  ') || '—'

export default function ScheduledActions() {
  const [apiKey, setApiKey] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const [list, setList] = useState([])
  const [filter, setFilter] = useState('all')
  const [now, setNow] = useState(Date.now())
  const [openId, setOpenId] = useState(null)
  const base = window.location.origin

  useEffect(() => {
    api.primaryKey().then((r) => setApiKey(r.api_key)).catch(() => setApiKey(null)).finally(() => setLoaded(true))
  }, [])

  const refresh = useCallback(async () => {
    if (!apiKey) return
    try {
      const res = await fetch(`${base}/api/v1/scheduled-actions?api_key=${encodeURIComponent(apiKey)}`)
      const body = await res.json()
      setList(body.scheduled_actions || [])
    } catch { /* ignore */ }
  }, [apiKey, base])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 4000)
    return () => clearInterval(t)
  }, [refresh])

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  async function cancel(id) {
    try {
      await fetch(`${base}/api/v1/scheduled-actions/cancel?api_key=${encodeURIComponent(apiKey)}&id=${id}`)
      refresh()
    } catch { /* ignore */ }
  }

  const shown = list.filter((o) => filter === 'all' || o.status === filter)
  const counts = FILTERS.reduce((acc, f) => {
    acc[f] = f === 'all' ? list.length : list.filter((o) => o.status === f).length
    return acc
  }, {})

  return (
    <DashboardLayout title="Scheduled Actions">
      <div className="settings-stack">
        <section className="card">
          <div className="card-head">
            <Timer size={18} strokeWidth={1.75} />
            <div>
              <h2 className="card-title">Scheduled actions</h2>
              <p className="card-sub">
                Everything queued to run later, with a live countdown and status.
                Create new ones in chat (“close gold in 30s”) or the{' '}
                <Link to="/scheduled-actions-guide" style={{ textDecoration: 'underline' }}>API guide</Link>.
              </p>
            </div>
            <button className="btn btn--ghost btn--icon" title="Refresh" onClick={refresh} style={{ marginLeft: 'auto' }}>
              <RefreshCw size={16} strokeWidth={1.75} />
            </button>
          </div>

          <div className="card-body">
            {loaded && !apiKey && (
              <div className="alert alert--danger">
                No active API key.{' '}
                <Link to="/settings" style={{ textDecoration: 'underline' }}>Generate one in Settings</Link>.
              </div>
            )}

            <div className="sched-filters">
              {FILTERS.map((f) => (
                <button key={f} className={'pill sched-filter' + (filter === f ? ' sched-filter--active' : '')}
                        onClick={() => setFilter(f)}>
                  {f}{counts[f] ? ` (${counts[f]})` : ''}
                </button>
              ))}
            </div>

            <div className="key-list">
              {shown.length === 0 ? (
                <p className="muted">No {filter === 'all' ? '' : filter + ' '}scheduled actions.</p>
              ) : (
                shown.map((o) => {
                  const expandable = o.status === 'executed' || o.status === 'failed'
                  const open = openId === o.id
                  return (
                    <div className="sched-item" key={o.id}>
                      <div className={'key-row' + (expandable ? ' sched-item--click' : '')}
                           onClick={() => expandable && setOpenId(open ? null : o.id)}>
                        <div className="key-row-main">
                          <span className="key-name">
                            <span className="sched-action">{o.action}</span>
                            <span className="sched-params">{summarize(o.params)}</span>
                          </span>
                          <span className="key-masked">
                            {o.account ? `acct ${o.account} · ` : ''}{new Date(o.run_at).toLocaleString()}
                          </span>
                        </div>
                        <div className="key-row-side">
                          {o.status === 'scheduled' && <span className="pill pill--warn countdown">{countdown(o.run_at, now)}</span>}
                          <span className={`pill ${STATUS_TONE[o.status] || ''}`}>{o.status}</span>
                          {o.status === 'scheduled' && (
                            <button className="btn btn--danger btn--icon" title="Cancel"
                                    onClick={(e) => { e.stopPropagation(); cancel(o.id) }}>
                              <Trash2 size={16} />
                            </button>
                          )}
                          {expandable && <ChevronDown size={15} strokeWidth={2} className={`action-caret ${open ? '' : 'closed'}`} />}
                        </div>
                      </div>
                      {open && expandable && (
                        <pre className="sched-result">{JSON.stringify(o.result, null, 2)}</pre>
                      )}
                    </div>
                  )
                })
              )}
            </div>
          </div>
        </section>
      </div>
    </DashboardLayout>
  )
}
