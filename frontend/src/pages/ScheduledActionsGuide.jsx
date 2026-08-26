import { useEffect, useState, useCallback } from 'react'
import { KeyRound, Timer, Trash2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import DashboardLayout from '../components/DashboardLayout.jsx'
import { ApiEndpoint, buildUrl } from '../components/ApiEndpoint.jsx'
import * as api from '../services/api.js'

const R = 'required'
const O = 'optional'

const ACTIONS = ['close', 'place_order', 'pending_order', 'break_even', 'lock_profit',
  'delete_sltp', 'modify_position', 'cancel_orders']

// sensible params template per action (versatile — edit freely)
const TEMPLATES = {
  close: '{\n  "symbol": "gold"\n}',
  place_order: '{\n  "symbol": "gold",\n  "side": "sell",\n  "volume": 0.1,\n  "sl_points": 3000,\n  "tp_points": 5000\n}',
  pending_order: '{\n  "symbol": "gold",\n  "side": "sell_limit",\n  "price": 2500,\n  "volume": 0.1\n}',
  break_even: '{\n  "symbol": "gold"\n}',
  lock_profit: '{\n  "percent": 60,\n  "symbol": "gold"\n}',
  delete_sltp: '{\n  "symbol": "gold",\n  "which": "both"\n}',
  modify_position: '{\n  "position_id": "123456",\n  "sl": 2000,\n  "tp": 2100\n}',
  cancel_orders: '{\n  "symbol": "gold"\n}',
}

const ENDPOINTS = [
  {
    id: 'schedule-action',
    path: '/api/v1/schedule-action',
    title: 'Schedule any action',
    desc: 'Run ANY trading action at a future time. Give an absolute run_at (ISO) OR relative hours/minutes/seconds. e.g. close gold in 30 seconds.',
    params: [
      { name: 'action', example: 'close', level: R, desc: 'close | place_order | pending_order | break_even | lock_profit | delete_sltp | modify_position | cancel_orders.' },
      { name: 'params', example: '{"symbol":"XAUUSD"}', level: R, desc: "JSON object of that action's arguments (minus account)." },
      { name: 'account', example: '', level: O, desc: 'Account number (defaults to the active account).' },
      { name: 'run_at', example: '', level: O, desc: 'Absolute time, ISO e.g. 2026-07-23T21:30:00.' },
      { name: 'hours', example: '', level: O, desc: 'Relative: hours from now.' },
      { name: 'minutes', example: '', level: O, desc: 'Relative: minutes from now.' },
      { name: 'seconds', example: '30', level: O, desc: 'Relative: seconds from now.' },
    ],
  },
  {
    id: 'scheduled-actions',
    path: '/api/v1/scheduled-actions',
    title: 'List scheduled actions',
    desc: 'All scheduled actions and their status (scheduled / executed / failed / cancelled).',
    params: [{ name: 'status', example: '', level: O, desc: 'Filter by status.' }],
  },
  {
    id: 'cancel',
    path: '/api/v1/scheduled-actions/cancel',
    title: 'Cancel scheduled action',
    desc: 'Cancel a scheduled action before it runs (id from the list).',
    params: [{ name: 'id', example: '', level: R, desc: 'Scheduled action id.' }],
  },
]

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

export default function ScheduledActionsGuide() {
  const [apiKey, setApiKey] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const base = window.location.origin

  useEffect(() => {
    api.primaryKey().then((r) => setApiKey(r.api_key)).catch(() => setApiKey(null)).finally(() => setLoaded(true))
  }, [])

  return (
    <DashboardLayout title="Scheduled Actions API Guide">
      <div className="guide">
        <div className="guide-intro card">
          <div className="card-body">
            <h2 className="card-title">Scheduled actions (any action, any time)</h2>
            <p className="card-sub">
              Schedule <strong>any</strong> trading action to run later on the server — close, place,
              break-even, lock-profit, delete SL/TP, cancel orders. Say <em>“close gold in 30 seconds”</em>
              in chat and the agent schedules it; or build one below. Time can be relative
              (hours/minutes/seconds) or an absolute date &amp; time.
            </p>
            {loaded && !apiKey && (
              <div className="alert alert--danger" style={{ marginTop: 12 }}>
                No active API key found.{' '}
                <Link to="/settings" style={{ textDecoration: 'underline' }}>Generate one in Settings</Link>.
              </div>
            )}
            {apiKey && (
              <div className="key-inline">
                <KeyRound size={15} strokeWidth={1.75} />
                <span>Using your active key</span>
                <code className="key-inline-val">{`${apiKey.slice(0, 12)}…${apiKey.slice(-4)}`}</code>
              </div>
            )}
          </div>
        </div>

        <ActionScheduler apiKey={apiKey} base={base} />

        {ENDPOINTS.map((ep) => (
          <ApiEndpoint key={ep.id} ep={ep} url={buildUrl(base, ep, apiKey)} base={base} apiKey={apiKey} />
        ))}
      </div>
    </DashboardLayout>
  )
}

function ActionScheduler({ apiKey, base }) {
  const [action, setAction] = useState('close')
  const [params, setParams] = useState(TEMPLATES.close)
  const [account, setAccount] = useState('')
  const [runAt, setRunAt] = useState('')
  const [seconds, setSeconds] = useState('30')
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const [list, setList] = useState([])
  const [now, setNow] = useState(Date.now())

  function onActionChange(a) {
    setAction(a)
    setParams(TEMPLATES[a] || '{}')
  }

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
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [refresh])

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  async function schedule(e) {
    e.preventDefault()
    setMsg(null)
    let parsed
    try {
      parsed = JSON.parse(params)
    } catch {
      return setMsg({ type: 'danger', text: 'Params must be valid JSON.' })
    }
    setBusy(true)
    try {
      const q = new URLSearchParams({ api_key: apiKey || '', action, params: JSON.stringify(parsed) })
      if (account) q.set('account', account)
      if (runAt) q.set('run_at', runAt)
      else q.set('seconds', seconds || '0')
      const res = await fetch(`${base}/api/v1/schedule-action?${q.toString()}`)
      const body = await res.json()
      if (!res.ok) throw new Error(body.detail || `Failed (${res.status})`)
      setMsg({ type: 'ok', text: `Scheduled for ${new Date(body.run_at).toLocaleString()}` })
      refresh()
    } catch (err) {
      setMsg({ type: 'danger', text: err.message })
    } finally {
      setBusy(false)
    }
  }

  async function cancel(id) {
    try {
      await fetch(`${base}/api/v1/scheduled-actions/cancel?api_key=${encodeURIComponent(apiKey)}&id=${id}`)
      refresh()
    } catch { /* ignore */ }
  }

  const summarize = (p) => Object.entries(p || {}).map(([k, v]) => `${k}=${v}`).join(' ') || '—'

  return (
    <section className="card">
      <div className="card-head">
        <Timer size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">Schedule an action</h2>
          <p className="card-sub">Pick an action, edit its params, and choose when. Leave the date empty to use “in N seconds”.</p>
        </div>
      </div>
      <div className="card-body">
        {msg && <div className={`alert alert--${msg.type === 'ok' ? 'ok' : 'danger'}`}>{msg.text}</div>}
        <form className="form-grid" onSubmit={schedule}>
          <label className="field"><span className="field-label">Action</span>
            <select className="input" value={action} onChange={(e) => onActionChange(e.target.value)}>
              {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
            </select></label>
          <label className="field"><span className="field-label">Account</span>
            <input className="input" value={account} onChange={(e) => setAccount(e.target.value)} placeholder="active account" /></label>
          <label className="field"><span className="field-label">Run at (date &amp; time)</span>
            <input className="input" type="datetime-local" value={runAt} onChange={(e) => setRunAt(e.target.value)} /></label>
          <label className="field"><span className="field-label">…or in N seconds</span>
            <input className="input" value={seconds} onChange={(e) => setSeconds(e.target.value)} disabled={!!runAt} /></label>
          <label className="field field--full"><span className="field-label">Params (JSON)</span>
            <textarea className="memory-editor" style={{ minHeight: 130 }} value={params}
                      onChange={(e) => setParams(e.target.value)} spellCheck={false} /></label>
          <div className="field form-submit">
            <button className="btn btn--primary" type="submit" disabled={busy || !apiKey}>
              {busy ? 'Scheduling…' : 'Schedule action'}
            </button>
          </div>
        </form>

        <div className="sym-section-title">Scheduled actions</div>
        <div className="key-list">
          {list.length === 0 ? (
            <p className="muted">No scheduled actions.</p>
          ) : (
            list.map((o) => (
              <div className="key-row" key={o.id}>
                <div className="key-row-main">
                  <span className="key-name">{o.action} · {summarize(o.params)}</span>
                  <span className="key-masked">
                    {o.account ? `acct ${o.account} · ` : ''}{new Date(o.run_at).toLocaleString()}
                  </span>
                </div>
                <div className="key-row-side">
                  {o.status === 'scheduled' && <span className="pill pill--warn countdown">{countdown(o.run_at, now)}</span>}
                  <span className={`pill ${STATUS_TONE[o.status] || ''}`}>{o.status}</span>
                  {o.status === 'scheduled' && (
                    <button className="btn btn--danger btn--icon" title="Cancel" onClick={() => cancel(o.id)}>
                      <Trash2 size={16} />
                    </button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  )
}
