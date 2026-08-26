import { useEffect, useState, useCallback } from 'react'
import { KeyRound, CalendarClock, Trash2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import DashboardLayout from '../components/DashboardLayout.jsx'
import { ApiEndpoint, buildUrl } from '../components/ApiEndpoint.jsx'
import * as api from '../services/api.js'

const R = 'required'
const O = 'optional'

const ENDPOINTS = [
  {
    id: 'schedule-order',
    path: '/api/v1/schedule-order',
    title: 'Schedule a market order',
    desc: 'Promise a market order the server will execute at a future time. Give an absolute run_at (ISO date & time) OR a relative hours/minutes/seconds from now.',
    params: [
      { name: 'symbol', example: 'XAUUSD', level: R, desc: 'Instrument symbol.' },
      { name: 'side', example: 'buy', level: R, desc: "MUST be 'buy' or 'sell'." },
      { name: 'volume', example: '0.1', level: O, desc: 'Lots (default 0.1).' },
      { name: 'sl_points', example: '2000', level: O, desc: 'Stop loss in points.' },
      { name: 'tp_points', example: '4000', level: O, desc: 'Take profit in points.' },
      { name: 'run_at', example: '', level: O, desc: 'Absolute time, ISO e.g. 2026-07-22T21:30:00.' },
      { name: 'hours', example: '', level: O, desc: 'Relative: hours from now.' },
      { name: 'minutes', example: '5', level: O, desc: 'Relative: minutes from now.' },
      { name: 'seconds', example: '', level: O, desc: 'Relative: seconds from now.' },
    ],
  },
  {
    id: 'scheduled-orders',
    path: '/api/v1/scheduled-orders',
    title: 'List scheduled orders',
    desc: 'All scheduled orders and their status (scheduled / executed / failed / cancelled).',
    params: [{ name: 'status', example: '', level: O, desc: 'Filter by status.' }],
  },
  {
    id: 'cancel',
    path: '/api/v1/scheduled-orders/cancel',
    title: 'Cancel scheduled order',
    desc: 'Cancel a scheduled order before it runs (id from the list).',
    params: [{ name: 'id', example: '', level: R, desc: 'Scheduled order id.' }],
  },
]

const STATUS_TONE = { scheduled: '', executing: 'pill--warn', executed: 'pill--ok', failed: 'pill--warn', cancelled: 'pill--muted' }

function countdown(runAtIso, now) {
  const ms = new Date(runAtIso).getTime() - now
  if (ms <= 0) return 'due…'
  const s = Math.floor(ms / 1000)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const pad = (n) => String(n).padStart(2, '0')
  if (h > 0) return `in ${h}h ${pad(m)}m ${pad(sec)}s`
  if (m > 0) return `in ${m}m ${pad(sec)}s`
  return `in ${sec}s`
}

export default function ScheduledGuide() {
  const [apiKey, setApiKey] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const base = window.location.origin

  useEffect(() => {
    api.primaryKey().then((r) => setApiKey(r.api_key)).catch(() => setApiKey(null)).finally(() => setLoaded(true))
  }, [])

  return (
    <DashboardLayout title="Scheduled Orders API Guide">
      <div className="guide">
        <div className="guide-intro card">
          <div className="card-body">
            <h2 className="card-title">Scheduled (time-based) orders</h2>
            <p className="card-sub">
              A scheduled order is a <strong>promise</strong> — the server opens it as a market order at
              the target time. Not a broker pending order. Time can be relative (hours/minutes/seconds)
              or an absolute date &amp; time.
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

        <Scheduler apiKey={apiKey} base={base} />

        {ENDPOINTS.map((ep) => (
          <ApiEndpoint key={ep.id} ep={ep} url={buildUrl(base, ep, apiKey)} base={base} apiKey={apiKey} />
        ))}
      </div>
    </DashboardLayout>
  )
}

function Scheduler({ apiKey, base }) {
  const [symbol, setSymbol] = useState('XAUUSD')
  const [side, setSide] = useState('buy')
  const [volume, setVolume] = useState('0.1')
  const [slPoints, setSlPoints] = useState('')
  const [tpPoints, setTpPoints] = useState('')
  const [runAt, setRunAt] = useState('')
  const [minutes, setMinutes] = useState('5')
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const [list, setList] = useState([])
  const [now, setNow] = useState(Date.now())

  const refresh = useCallback(async () => {
    if (!apiKey) return
    try {
      const res = await fetch(`${base}/api/v1/scheduled-orders?api_key=${encodeURIComponent(apiKey)}`)
      const body = await res.json()
      setList(body.scheduled_orders || [])
    } catch {
      /* ignore */
    }
  }, [apiKey, base])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [refresh])

  // tick every second so the countdown updates live
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  async function schedule(e) {
    e.preventDefault()
    setMsg(null); setBusy(true)
    try {
      const q = new URLSearchParams({ api_key: apiKey || '', symbol: symbol.trim().toUpperCase(), side, volume })
      if (slPoints) q.set('sl_points', slPoints)
      if (tpPoints) q.set('tp_points', tpPoints)
      if (runAt) q.set('run_at', runAt) // datetime-local (absolute)
      else q.set('minutes', minutes || '0')
      const res = await fetch(`${base}/api/v1/schedule-order?${q.toString()}`)
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
      await fetch(`${base}/api/v1/scheduled-orders/cancel?api_key=${encodeURIComponent(apiKey)}&id=${id}`)
      refresh()
    } catch {
      /* ignore */
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <CalendarClock size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">Schedule a trade</h2>
          <p className="card-sub">Pick a symbol and a time. Leave the date empty to use “in N minutes”.</p>
        </div>
      </div>
      <div className="card-body">
        {msg && <div className={`alert alert--${msg.type === 'ok' ? 'ok' : 'danger'}`}>{msg.text}</div>}
        <form className="form-grid" onSubmit={schedule}>
          <label className="field"><span className="field-label">Symbol</span>
            <input className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)} /></label>
          <label className="field"><span className="field-label">Side</span>
            <select className="input" value={side} onChange={(e) => setSide(e.target.value)}>
              <option value="buy">buy</option><option value="sell">sell</option>
            </select></label>
          <label className="field"><span className="field-label">Volume</span>
            <input className="input" value={volume} onChange={(e) => setVolume(e.target.value)} /></label>
          <label className="field"><span className="field-label">SL points</span>
            <input className="input" value={slPoints} onChange={(e) => setSlPoints(e.target.value)} placeholder="optional" /></label>
          <label className="field"><span className="field-label">TP points</span>
            <input className="input" value={tpPoints} onChange={(e) => setTpPoints(e.target.value)} placeholder="optional" /></label>
          <label className="field"><span className="field-label">Run at (date &amp; time)</span>
            <input className="input" type="datetime-local" value={runAt} onChange={(e) => setRunAt(e.target.value)} /></label>
          <label className="field"><span className="field-label">…or in N minutes</span>
            <input className="input" value={minutes} onChange={(e) => setMinutes(e.target.value)} disabled={!!runAt} /></label>
          <div className="field form-submit">
            <button className="btn btn--primary" type="submit" disabled={busy || !apiKey}>
              {busy ? 'Scheduling…' : 'Schedule trade'}
            </button>
          </div>
        </form>

        <div className="sym-section-title">Scheduled orders</div>
        <div className="key-list">
          {list.length === 0 ? (
            <p className="muted">No scheduled orders.</p>
          ) : (
            list.map((o) => (
              <div className="key-row" key={o.id}>
                <div className="key-row-main">
                  <span className="key-name">{o.side.toUpperCase()} {o.volume} {o.symbol}</span>
                  <span className="key-masked">{new Date(o.run_at).toLocaleString()}</span>
                </div>
                <div className="key-row-side">
                  {o.status === 'scheduled' && (
                    <span className="pill pill--warn countdown">{countdown(o.run_at, now)}</span>
                  )}
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
