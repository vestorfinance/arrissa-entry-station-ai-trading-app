import { useEffect, useState } from 'react'
import { Copy, Check, Play, ExternalLink, KeyRound } from 'lucide-react'
import { Link } from 'react-router-dom'
import DashboardLayout from '../components/DashboardLayout.jsx'
import * as api from '../services/api.js'

// Full scope of the trading engine, exposed as browser-usable GET endpoints.
// Every example is a real, runnable URL pre-filled with the user's active key.
const R = 'required'
const O = 'optional'

const ENDPOINTS = [
  // ── Reads ──────────────────────────────────────────────────────────────────
  { group: 'Reads', id: 'positions', path: '/api/v1/positions', title: 'Open positions', desc: 'Every open position on the account.', params: [] },
  { group: 'Reads', id: 'orders', path: '/api/v1/orders', title: 'Pending orders', desc: 'All pending (limit/stop) orders.', params: [] },
  { group: 'Reads', id: 'price', path: '/api/v1/price', title: 'Live price', desc: 'Latest bid/ask reference price for a symbol.', params: [
    { name: 'symbol', example: 'XAUUSD', level: R, desc: 'Instrument symbol.' },
    { name: 'side', example: 'ask', level: R, desc: "MUST be 'bid' or 'ask'." },
  ] },

  // ── Market orders ──────────────────────────────────────────────────────────
  { group: 'Market orders', id: 'place-order', path: '/api/v1/place-order', title: 'Place market order', desc: 'Open a market order. SL/TP accept points (sl_points/tp_points) or absolute prices (sl/tp).', params: [
    { name: 'symbol', example: 'XAUUSD', level: R, desc: 'Instrument symbol.' },
    { name: 'side', example: 'buy', level: R, desc: "MUST be 'buy' or 'sell'." },
    { name: 'volume', example: '0.1', level: O, desc: 'Lots (default 0.1).' },
    { name: 'sl_points', example: '2000', level: O, desc: 'Stop loss in points.' },
    { name: 'tp_points', example: '4000', level: O, desc: 'Take profit in points.' },
    { name: 'sl', example: '', level: O, desc: 'SL as absolute price (alt to sl_points).' },
    { name: 'tp', example: '', level: O, desc: 'TP as absolute price (alt to tp_points).' },
    { name: 'deviation', example: '', level: O, desc: 'Max slippage tolerance.' },
  ] },
  { group: 'Market orders', id: 'place-order-bulk', path: '/api/v1/place-order-bulk', title: 'Burst (bulk market orders)', desc: 'Fire N market orders in a row. delay_ms paces them to dodge broker throttling.', params: [
    { name: 'symbol', example: 'XAUUSD', level: R, desc: 'Instrument symbol.' },
    { name: 'side', example: 'buy', level: R, desc: "MUST be 'buy' or 'sell'." },
    { name: 'count', example: '3', level: O, desc: 'How many orders (default 5).' },
    { name: 'volume', example: '0.1', level: O, desc: 'Lots each (default 0.1).' },
    { name: 'sl_points', example: '3000', level: O, desc: 'Stop loss in points.' },
    { name: 'tp_points', example: '6000', level: O, desc: 'Take profit in points.' },
    { name: 'delay_ms', example: '150', level: O, desc: 'Pause between orders (ms).' },
    { name: 'deviation', example: '', level: O, desc: 'Max slippage tolerance.' },
  ] },
  { group: 'Market orders', id: 'snipe', path: '/api/v1/snipe', title: 'Snipe (fast market order)', desc: 'Low-latency market order with a wide default deviation — for news.', params: [
    { name: 'symbol', example: 'XAUUSD', level: R, desc: 'Instrument symbol.' },
    { name: 'side', example: 'buy', level: R, desc: "MUST be 'buy' or 'sell'." },
    { name: 'volume', example: '0.1', level: O, desc: 'Lots (default 0.1).' },
    { name: 'sl_points', example: '2000', level: O, desc: 'Stop loss in points.' },
    { name: 'tp_points', example: '4000', level: O, desc: 'Take profit in points.' },
    { name: 'deviation', example: '', level: O, desc: 'Slippage tolerance (default 1000).' },
  ] },

  // ── Pending orders ─────────────────────────────────────────────────────────
  { group: 'Pending orders', id: 'pending-order', path: '/api/v1/pending-order', title: 'Place pending order', desc: 'Limit/stop order at a trigger price.', params: [
    { name: 'symbol', example: 'XAUUSD', level: R, desc: 'Instrument symbol.' },
    { name: 'side', example: 'buy_limit', level: R, desc: "MUST be buy_limit | sell_limit | buy_stop | sell_stop." },
    { name: 'price', example: '4100', level: R, desc: 'Trigger price.' },
    { name: 'volume', example: '0.1', level: O, desc: 'Lots (default 0.1).' },
    { name: 'sl_points', example: '200', level: O, desc: 'Stop loss in points.' },
    { name: 'tp_points', example: '400', level: O, desc: 'Take profit in points.' },
  ] },
  { group: 'Pending orders', id: 'pending-bulk', path: '/api/v1/pending-bulk', title: 'Pending ladder (bulk)', desc: 'N pending orders laddered step_points apart from a base price.', params: [
    { name: 'symbol', example: 'XAUUSD', level: R, desc: 'Instrument symbol.' },
    { name: 'side', example: 'buy_limit', level: R, desc: "MUST be buy_limit | sell_limit | buy_stop | sell_stop." },
    { name: 'price', example: '4100', level: R, desc: 'Base trigger price.' },
    { name: 'count', example: '3', level: O, desc: 'How many orders (default 5).' },
    { name: 'step_points', example: '-200', level: O, desc: 'Points between each order.' },
    { name: 'volume', example: '0.1', level: O, desc: 'Lots each.' },
    { name: 'sl_points', example: '', level: O, desc: 'Stop loss in points.' },
    { name: 'tp_points', example: '', level: O, desc: 'Take profit in points.' },
    { name: 'delay_ms', example: '', level: O, desc: 'Pause between orders (ms).' },
  ] },
  { group: 'Pending orders', id: 'modify-order', path: '/api/v1/modify-order', title: 'Modify pending order', desc: 'Change a pending order. Omit a field to keep it. Get the ticket from /orders.', params: [
    { name: 'ticket', example: '', level: R, desc: 'Order ticket id (from /orders).' },
    { name: 'price', example: '', level: O, desc: 'New trigger price.' },
    { name: 'sl', example: '', level: O, desc: 'New stop loss (0 removes it).' },
    { name: 'tp', example: '', level: O, desc: 'New take profit (0 removes it).' },
  ] },
  { group: 'Pending orders', id: 'cancel-order', path: '/api/v1/cancel-order', title: 'Cancel one pending order', desc: 'Cancel a single pending order by ticket (from /orders).', params: [
    { name: 'ticket', example: '', level: R, desc: 'Order ticket id.' },
  ] },
  { group: 'Pending orders', id: 'cancel-orders', path: '/api/v1/cancel-orders', title: 'Cancel pending orders (bulk)', desc: 'Versatile targeting — one order (ticket), all on a symbol (symbol), or ALL pending orders when both are omitted.', params: [
    { name: 'symbol', example: 'XAUUSD', level: O, desc: 'Cancel all pending on this symbol.' },
    { name: 'ticket', example: '', level: O, desc: 'Cancel one specific ticket.' },
  ] },

  // ── Command ────────────────────────────────────────────────────────────────
  { group: 'Command', id: 'trade', path: '/api/v1/trade', title: 'Natural-language trade', desc: 'Parse and execute a free-form command.', params: [
    { name: 'command', example: 'open 0.1 XAUUSD 2000 sl 2000 tp', level: R, desc: 'The trade in plain text.' },
  ] },
]

const GROUPS = [...new Set(ENDPOINTS.map((e) => e.group))]

function buildUrl(base, ep, apiKey) {
  const q = new URLSearchParams()
  q.set('api_key', apiKey || 'YOUR_API_KEY')
  ep.params.forEach((p) => { if (p.example) q.set(p.name, p.example) })
  return `${base}${ep.path}?${decodeURIComponent(q.toString())}`
}

export default function OrdersGuide() {
  const [apiKey, setApiKey] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const base = window.location.origin

  useEffect(() => {
    api.primaryKey().then((r) => setApiKey(r.api_key)).catch(() => setApiKey(null)).finally(() => setLoaded(true))
  }, [])

  return (
    <DashboardLayout title="Orders API Guide">
      <div className="guide">
        <div className="guide-intro card">
          <div className="card-body">
            <h2 className="card-title">Orders &amp; Positions API</h2>
            <p className="card-sub">
              Full scope of the trading engine. Every example is a real, working URL — paste it into a
              browser or click <strong>Run</strong>. Auth is the <code>api_key</code> query parameter.
            </p>
            <div className="callout">
              <strong>Versatile targeting.</strong> <code>cancel-orders</code> acts on{' '}
              <strong>one</strong> order (<code>ticket</code>), <strong>all on a symbol</strong>{' '}
              (<code>symbol</code>), or <strong>everything</strong> when you omit both. Acting on open
              positions (close, break even, lock profit…) lives in the{' '}
              <strong>Order Management</strong> guide.
            </div>
            {loaded && !apiKey && (
              <div className="alert alert--danger" style={{ marginTop: 12 }}>
                No active API key found.{' '}
                <Link to="/settings" style={{ textDecoration: 'underline' }}>Generate one in Settings</Link>{' '}
                to auto-fill these examples.
              </div>
            )}
            {apiKey && (
              <div className="key-inline">
                <KeyRound size={15} strokeWidth={1.75} />
                <span>Examples filled with your active key</span>
                <code className="key-inline-val">{`${apiKey.slice(0, 12)}…${apiKey.slice(-4)}`}</code>
              </div>
            )}
          </div>
        </div>

        {GROUPS.map((group) => (
          <div key={group} className="guide-group">
            <h2 className="guide-group-title">{group}</h2>
            {ENDPOINTS.filter((e) => e.group === group).map((ep) =>
              ep.monitor ? (
                <MonitorNote key={ep.id} ep={ep} />
              ) : (
                <Endpoint key={ep.id} ep={ep} url={buildUrl(base, ep, apiKey)} />
              ),
            )}
          </div>
        ))}
      </div>
    </DashboardLayout>
  )
}

function MonitorNote({ ep }) {
  return (
    <section className="card endpoint">
      <div className="card-body">
        <div className="endpoint-head">
          <span className="method method--monitor">MONITOR</span>
          <code className="endpoint-path">lock_profit_money</code>
        </div>
        <h3 className="endpoint-title">{ep.title}</h3>
        <p className="card-sub">{ep.desc}</p>
        <div className="code-block">
          <code className="code-text">{ep.code}</code>
        </div>
      </div>
    </section>
  )
}

function Endpoint({ ep, url }) {
  const [copied, setCopied] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const curl = `curl "${url}"`

  function copy(text, which) {
    navigator.clipboard.writeText(text)
    setCopied(which)
    setTimeout(() => setCopied(''), 1500)
  }

  async function run() {
    setRunning(true)
    setResult(null)
    try {
      const res = await fetch(url)
      const text = await res.text()
      let body
      try { body = JSON.stringify(JSON.parse(text), null, 2) } catch { body = text }
      setResult({ status: res.status, body })
    } catch (err) {
      setResult({ status: 'ERR', body: String(err) })
    } finally {
      setRunning(false)
    }
  }

  return (
    <section className="card endpoint">
      <div className="card-body">
        <div className="endpoint-head">
          <span className="method">GET</span>
          <code className="endpoint-path">{ep.path}</code>
        </div>
        <h3 className="endpoint-title">{ep.title}</h3>
        <p className="card-sub">{ep.desc}</p>

        <table className="params">
          <thead>
            <tr><th>Parameter</th><th>Example</th><th>Description</th></tr>
          </thead>
          <tbody>
            <tr>
              <td><code>api_key</code> <span className="req">required</span></td>
              <td><code>ak_live_…</code></td>
              <td>Your API key (auto-filled).</td>
            </tr>
            {ep.params.map((p) => (
              <tr key={p.name}>
                <td>
                  <code>{p.name}</code>{' '}
                  <span className={p.level === 'required' ? 'req' : 'opt'}>{p.level}</span>
                </td>
                <td>{p.example ? <code>{p.example}</code> : <span className="muted">—</span>}</td>
                <td>{p.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="field-label">Runnable URL</div>
        <div className="code-block">
          <code className="code-text">{url}</code>
          <div className="code-actions">
            <button className="btn btn--ghost btn--icon" title="Copy URL" onClick={() => copy(url, 'url')}>
              {copied === 'url' ? <Check size={16} /> : <Copy size={16} />}
            </button>
            <a className="btn btn--ghost btn--icon" title="Open in browser" href={url} target="_blank" rel="noreferrer">
              <ExternalLink size={16} />
            </a>
          </div>
        </div>

        <div className="field-label">curl</div>
        <div className="code-block">
          <code className="code-text">{curl}</code>
          <div className="code-actions">
            <button className="btn btn--ghost btn--icon" title="Copy curl" onClick={() => copy(curl, 'curl')}>
              {copied === 'curl' ? <Check size={16} /> : <Copy size={16} />}
            </button>
          </div>
        </div>

        <div className="endpoint-run">
          <button className="btn btn--primary" onClick={run} disabled={running}>
            <Play size={16} strokeWidth={2} />
            {running ? 'Running…' : 'Run'}
          </button>
        </div>

        {result && (
          <div className="response">
            <div className="response-head">
              <span className={`pill ${result.status === 200 ? 'pill--ok' : 'pill--warn'}`}>
                {result.status === 200 ? '200 OK' : `Status ${result.status}`}
              </span>
            </div>
            <pre className="response-body">{result.body}</pre>
          </div>
        )}
      </div>
    </section>
  )
}
