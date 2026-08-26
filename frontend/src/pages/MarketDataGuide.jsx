import { useEffect, useState } from 'react'
import { KeyRound, CandlestickChart, Activity } from 'lucide-react'
import { Link } from 'react-router-dom'
import DashboardLayout from '../components/DashboardLayout.jsx'
import { ApiEndpoint, buildUrl } from '../components/ApiEndpoint.jsx'
import * as api from '../services/api.js'

const R = 'required'
const O = 'optional'

// Replay. Every read below accepts these: the API answers as of that UTC moment,
// hiding anything published later. A scheduled release still shows — it WAS known
// — but its actual is blanked until the moment it printed.
const PRETEND = [
  { name: 'pretend_date', example: '', level: O,
    desc: 'Answer as if the request were made on this UTC date — e.g. ?pretend_date=2026-08-25&pretend_time=13:12. For backtests and agent reruns that must not see the outcome.' },
  { name: 'pretend_time', example: '', level: O,
    desc: 'UTC time of day to pair with pretend_date, e.g. 13:12. Omitted, the moment is midnight — not the whole day.' },
]


const TFS = ['M1', 'M3', 'M5', 'M10', 'M15', 'M30', 'H1', 'H2', 'H4', 'D1', 'W1', 'MN1']

const ENDPOINTS = [
  {
    id: 'candles',
    path: '/api/v1/market/candles',
    title: 'Get candles',
    desc: "OHLC price history straight off the live Exness session, oldest first. Nothing is cached — every call is live, so there is no staleness to worry about. Symbols are forgiving: gold, nasdaq, btc and cable all resolve against the account's own instrument list.",
    params: [
      { name: 'symbol', example: 'gold', level: R, desc: 'Instrument — a real symbol (XAUUSD) or a nickname (gold, nasdaq, cable).' },
      { name: 'timeframe', example: 'M15', level: O, desc: `One of ${TFS.join(', ')} — or the equivalent in minutes (15, 240…), or 1h / 4h / daily. Default M15.` },
      { name: 'count', example: '100', level: O, desc: 'How many candles back (default 100, max 5000).' },
      { name: 'price', example: '', level: O, desc: 'bid (default) or ask series.' },
      { name: 'end', example: '', level: O, desc: 'Walk back from a past moment instead of now — ISO (2026-07-20T00:00:00Z) or epoch ms.' },
          ...PRETEND,
    ],
  },
  {
    id: 'chart',
    path: '/api/v1/market/chart',
    title: 'Chart payload',
    desc: "Candles plus the account's own trades on that instrument — entry, stop and target, each flagged in_range when it falls inside the candles' price band. This is what the chat renders as a live chart; ask it “show me a chart of gold” and it draws this, updating from the tick stream.",
    params: [
      { name: 'symbol', example: 'gold', level: R, desc: 'Instrument or nickname.' },
      { name: 'timeframe', example: 'M15', level: O, desc: 'Default M15.' },
      { name: 'count', example: '150', level: O, desc: 'Candles to draw (default 150).' },
      { name: 'account', example: '', level: O, desc: 'Whose trades to mark (defaults to the active account).' },
          ...PRETEND,
    ],
  },
  {
    id: 'quote',
    path: '/api/v1/market/quote',
    title: 'Live quote',
    desc: 'Current bid, ask and spread for one instrument.',
    params: [{ name: 'symbol', example: 'gold', level: R, desc: 'Instrument or nickname.' }],
  },
  {
    id: 'timeframes',
    path: '/api/v1/market/timeframes',
    title: 'Supported timeframes',
    desc: 'Every timeframe the feed accepts with its length in minutes, plus the per-request candle ceiling. Anything else is rejected by the source.',
    params: [],
  },
]

export default function MarketDataGuide() {
  const [apiKey, setApiKey] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const base = window.location.origin

  useEffect(() => {
    api.primaryKey().then((r) => setApiKey(r.api_key)).catch(() => setApiKey(null)).finally(() => setLoaded(true))
  }, [])

  return (
    <DashboardLayout title="Market Data API Guide">
      <div className="guide">
        <div className="guide-intro card">
          <div className="card-body">
            <h2 className="card-title">Analysis — Market data</h2>
            <p className="card-sub">
              OHLC candles and live quotes read directly from the Exness session the trading engine
              already holds open. Unlike the other analysis sources there is no fetcher and no
              database behind this — candles are always available at source, so every call goes
              live. Ask for an instrument, a timeframe and a count.
            </p>
            {loaded && !apiKey && (
              <div className="alert alert--danger" style={{ marginTop: 12 }}>
                No active API key.{' '}
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

        <CandleExplorer apiKey={apiKey} base={base} />

        {ENDPOINTS.map((ep) => (
          <ApiEndpoint key={ep.id} ep={ep} url={buildUrl(base, ep, apiKey)} />
        ))}
      </div>
    </DashboardLayout>
  )
}

function CandleExplorer({ apiKey, base }) {
  const [symbol, setSymbol] = useState('gold')
  const [timeframe, setTimeframe] = useState('M15')
  const [count, setCount] = useState(50)
  const [side, setSide] = useState('bid')
  const [data, setData] = useState(null)
  const [quote, setQuote] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  async function run(e) {
    e?.preventDefault()
    setBusy(true); setErr(null)
    try {
      const q = new URLSearchParams({
        api_key: apiKey || '', symbol: symbol.trim(), timeframe, count: String(count), price: side,
      })
      const res = await fetch(`${base}/api/v1/market/candles?${q.toString()}`)
      const body = await res.json()
      if (!res.ok) throw new Error(body.detail || 'request failed')
      setData(body)
      const qres = await fetch(`${base}/api/v1/market/quote?api_key=${encodeURIComponent(apiKey || '')}&symbol=${encodeURIComponent(symbol.trim())}`)
      setQuote(qres.ok ? await qres.json() : null)
    } catch (e2) {
      setErr(e2.message); setData(null); setQuote(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <CandlestickChart size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">Candles</h2>
          <p className="card-sub">Pick an instrument, timeframe and how many bars back.</p>
        </div>
        {quote && (
          <span className="pill pill--ok" style={{ marginLeft: 'auto' }}>
            {quote.bid} / {quote.ask}
          </span>
        )}
      </div>
      <div className="card-body">
        <form className="form-grid" onSubmit={run}>
          <label className="field"><span className="field-label">Instrument</span>
            <input className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)}
                   placeholder="gold" /></label>
          <label className="field"><span className="field-label">Timeframe</span>
            <select className="input" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
              {TFS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select></label>
          <label className="field"><span className="field-label">Count</span>
            <input className="input" type="number" min="1" max="5000" value={count}
                   onChange={(e) => setCount(e.target.value)} /></label>
          <label className="field"><span className="field-label">Price</span>
            <select className="input" value={side} onChange={(e) => setSide(e.target.value)}>
              <option value="bid">bid</option><option value="ask">ask</option>
            </select></label>
          <div className="field form-submit">
            <button className="btn btn--primary" type="submit" disabled={busy || !apiKey}>
              {busy ? 'Loading…' : 'Get candles'}
            </button>
          </div>
        </form>

        {err && <div className="alert alert--danger" style={{ marginTop: 12 }}>{err}</div>}

        {data && data.count > 0 && (
          <>
            <div className="sym-section-title" style={{ marginTop: 6 }}>
              <Activity size={13} strokeWidth={1.75} style={{ verticalAlign: '-2px', marginRight: 6 }} />
              {data.symbol} · {data.timeframe} · {data.count} candles · {data.from?.slice(0, 16).replace('T', ' ')}
              {' → '}{data.to?.slice(0, 16).replace('T', ' ')}
            </div>
            <Sparkline candles={data.candles} />
            <div className="candle-table">
              <div className="candle-row candle-row--head">
                <span>Time</span><span>Open</span><span>High</span><span>Low</span><span>Close</span><span>Vol</span>
              </div>
              {[...data.candles].reverse().slice(0, 25).map((c) => (
                <div className="candle-row" key={c.epoch_ms}>
                  <span className="candle-time">{c.time.slice(0, 16).replace('T', ' ')}</span>
                  <span>{c.open}</span><span>{c.high}</span><span>{c.low}</span>
                  <span className={c.close >= c.open ? 'candle-up' : 'candle-down'}>{c.close}</span>
                  <span className="muted">{c.volume ?? '—'}</span>
                </div>
              ))}
            </div>
            {data.count > 25 && (
              <p className="card-sub" style={{ marginTop: 8 }}>
                Showing the most recent 25 of {data.count}. The API returns all of them, oldest first.
              </p>
            )}
          </>
        )}
      </div>
    </section>
  )
}

// Closing prices as a plain SVG path — no chart library, no external requests.
function Sparkline({ candles }) {
  if (!candles || candles.length < 2) return null
  const closes = candles.map((c) => c.close)
  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const span = max - min || 1
  const w = 100
  const h = 28
  const points = closes.map((c, i) => {
    const x = (i / (closes.length - 1)) * w
    const y = h - ((c - min) / span) * h
    return `${x.toFixed(2)},${y.toFixed(2)}`
  }).join(' ')
  const up = closes[closes.length - 1] >= closes[0]

  return (
    <svg className="candle-spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <polyline points={points} fill="none" strokeWidth="0.8"
                stroke={up ? '#6ee7b7' : '#fca5a5'} vectorEffect="non-scaling-stroke" />
    </svg>
  )
}
