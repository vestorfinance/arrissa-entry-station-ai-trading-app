import { useEffect, useState } from 'react'
import { KeyRound, Radar, Play } from 'lucide-react'
import { Link } from 'react-router-dom'
import DashboardLayout from '../components/DashboardLayout.jsx'
import { ApiEndpoint, buildUrl } from '../components/ApiEndpoint.jsx'
import * as api from '../services/api.js'

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

const R = 'required'

const ENDPOINTS = [
  {
    id: 'artificial-sentiment',
    path: '/api/v1/artificial-sentiment',
    title: 'Read positioning from the candles',
    desc: "Who controls a market, reconstructed from its own price structure — swings, liquidity sweeps, volume and wick absorption. Where /sentiment reports how Myfxbook's users are positioned (one number, only for the symbols it covers, behind a daily quota), this works on ANY instrument, on ANY timeframe, as often as you like. Returns bulls/bears percentages, each side's estimated average entry, and how much of each side is trapped underwater — raw numbers, with no bias label or commentary attached. It is a model, not a measurement, and every response says so.",
    params: [
      { name: 'symbol', example: 'XAUUSD', level: R, desc: 'Any instrument your account can price — gold, XAUUSD, nasdaq, BTCUSD.' },
      { name: 'timeframe', example: 'M15', level: O, desc: 'M1 | M5 | M15 | M30 | H1 | H4 | D1 (default M15). The read is timeframe-specific: H4 tells you about swing positioning, M5 about intraday.' },
      { name: 'count', example: '200', level: O, desc: 'Candles to reconstruct from, 40–1000 (default 200). More candles reach further back; recent swings always weigh more.' },
      { name: 'compare', example: 'true', level: O, desc: "Also return Myfxbook's real retail positioning for the same symbol, and the gap between them." },
          ...PRETEND,
    ],
      examples: [
        { label: 'Point in time — as of 25 Aug 2026, 13:12 UTC',
          hint: 'Run this and compare it with the URL above. Anything published after that moment is gone; a release scheduled for later is still listed, with its actual blanked.',
          params: { pretend_date: '2026-08-25', pretend_time: '13:12' } },
      ],
  },
]

const TIMEFRAMES = ['M5', 'M15', 'M30', 'H1', 'H4', 'D1']

export default function ArtificialSentimentGuide() {
  const [apiKey, setApiKey] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const base = window.location.origin

  useEffect(() => {
    api.primaryKey().then((r) => setApiKey(r.api_key)).catch(() => setApiKey(null)).finally(() => setLoaded(true))
  }, [])

  return (
    <DashboardLayout title="Artificial Sentiment API Guide">
      <div className="guide">
        <div className="guide-intro card">
          <div className="card-body">
            <h2 className="card-title">Analysis — Artificial sentiment</h2>
            <p className="card-sub">
              Retail sentiment tells you how one broker’s users are positioned. This tells you
              how <em>the market</em> is positioned, worked out from the only evidence that is
              always available: the candles. Every swing is treated as a battle, and each battle
              transfers control energy between bulls and bears — so the two numbers always add to
              100 without a fudge at the end.
            </p>
            <p className="card-sub">
              The most useful output is not the percentage but the <strong>trapped</strong> figures:
              the share of each side that is underwater right now. Trapped positions are future
              forced flow — they have to be closed eventually, and that is real pressure in a
              known direction.
            </p>
            <p className="card-sub">
              The response is <strong>measurements only</strong> — percentages, prices, counts. No
              bias word, no commentary: “58% bull” is a number and “moderately bullish” is a reading
              of it, which is yours to make.
            </p>
            <div className="alert" style={{ marginTop: 12 }}>
              It is a <strong>model, not a measurement</strong>. It infers positioning from price
              and cannot see real accounts, so it will be wrong when a market moves for reasons the
              chart does not contain. Use <code>compare=true</code> to put Myfxbook’s real retail
              reading beside it — the disagreements are the interesting part.
            </div>
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

        <TryIt apiKey={apiKey} base={base} />

        {ENDPOINTS.map((ep) => (
          <ApiEndpoint key={ep.id} ep={ep} url={buildUrl(base, ep, apiKey)} base={base} apiKey={apiKey} />
        ))}

        <div className="card">
          <div className="card-body">
            <h3 className="card-title">How it reads a chart</h3>
            <ul className="guide-list">
              <li><strong>Swings, not candles.</strong> Structure is found with a ZigZag whose
                threshold scales with ATR, so gold and EURUSD both get sensible swings without
                per-symbol settings.</li>
              <li><strong>Wicks cut against the body.</strong> A long upper wick on heavy volume
                means buying was <em>absorbed</em> by sellers — it credits bears, however green
                the candle closed.</li>
              <li><strong>Sweeps matter most.</strong> A dip below a swing low that snaps back is
                not weakness: weak longs were flushed and stronger buyers replaced them at a better
                price. That single idea is what separates this from counting green candles.</li>
              <li><strong>Recency.</strong> A swing’s influence halves every quarter of the
                window, because positioning from the far end has mostly been closed out.</li>
              <li><strong>Positions leave the way real ones do.</strong> Each cohort carries a
                probability it is still open — eroded by progress toward a reward target, toward a
                stop, toward a margin liquidation, and by age. No single risk-reward or leverage is
                assumed; each is a distribution, because retail is not one trader.</li>
              <li><strong>Liquidation is separate from the stop.</strong> The broker closes a
                leveraged position for want of margin, often before the trader’s own stop — which
                is what removes weak hands on a sweep. The share already force-closed is reported
                on its own.</li>
              <li><strong>Confidence says how much the evidence agrees.</strong> Two charts can
                both read 54% bull; one where every signal aligns and one where they conflict are
                not the same reading.</li>
              <li><strong>Ranges are an answer.</strong> If nothing has travelled far enough to
                take ground, it returns a flat 50/50 rather than manufacturing a lean.</li>
            </ul>
          </div>
        </div>
      </div>
    </DashboardLayout>
  )
}

function TryIt({ apiKey, base }) {
  const [symbol, setSymbol] = useState('XAUUSD')
  const [tf, setTf] = useState('M15')
  const [compare, setCompare] = useState(true)
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState(null)
  const [err, setErr] = useState(null)

  async function run() {
    if (!apiKey || busy) return
    setBusy(true); setErr(null); setRes(null)
    try {
      const q = new URLSearchParams({ api_key: apiKey, symbol, timeframe: tf })
      if (compare) q.set('compare', 'true')
      const r = await fetch(`${base}/api/v1/artificial-sentiment?${q.toString()}`)
      const body = await r.json()
      if (!r.ok) throw new Error(body.detail || 'request failed')
      setRes(body)
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <Radar size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">Try it</h2>
          <p className="card-sub">Reads live candles from your account and reconstructs positioning.</p>
        </div>
      </div>
      <div className="card-body">
        <div className="art-controls">
          <input className="input art-sym" value={symbol} spellCheck={false}
                 onChange={(e) => setSymbol(e.target.value)} placeholder="XAUUSD" />
          <div className="pill-row">
            {TIMEFRAMES.map((t) => (
              <button key={t} type="button"
                      className={'pill-opt' + (tf === t ? ' pill-opt--on' : '')}
                      onClick={() => setTf(t)}>{t}</button>
            ))}
          </div>
          <label className="cot-toggle">
            <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} />
            <span>Compare with retail</span>
          </label>
          <button className="btn btn--primary" onClick={run} disabled={!apiKey || busy}>
            <Play size={14} strokeWidth={2} /> {busy ? 'Reading…' : 'Read'}
          </button>
        </div>

        {err && <div className="alert alert--danger" style={{ marginTop: 12 }}>{err}</div>}

        {res && (
          <div className="art-result">
            {/* Round ONE side and derive the other. Rounding each half on its own
                turned 40.5 / 59.5 into "41% / 60%" — a total of 101, from a model
                whose whole premise is that the two sides share a fixed 100. */}
            <div className="art-bar" title={`${res.bulls_percent}% bull / ${res.bears_percent}% bear`}>
              <span className="art-bar-bull" style={{ width: `${res.bulls_percent}%` }}>
                {Math.round(res.bulls_percent)}%
              </span>
              <span className="art-bar-bear" style={{ width: `${res.bears_percent}%` }}>
                {100 - Math.round(res.bulls_percent)}%
              </span>
            </div>
            <div className="art-head">
              <span className="muted">
                {res.swings} swings · {res.candles} candles · {res.timeframe} · ATR {res.atr}
                {res.volume_used === false && ' · no volume reported'}
              </span>
            </div>

            <div className="art-grid">
              <Stat label="Avg long entry" value={res.average_long_entry} />
              <Stat label="Avg short entry" value={res.average_short_entry} />
              <Stat label="Trapped longs" value={fmtPct(res.trapped_longs_percent)}
                    warn={res.trapped_longs_percent >= 40} />
              <Stat label="Trapped shorts" value={fmtPct(res.trapped_shorts_percent)}
                    warn={res.trapped_shorts_percent >= 40} />
              <Stat label="Price" value={res.current_price} />
              <Stat label="Confidence" value={fmtPct(res.confidence_percent)} />
              <Stat label="Liquidated longs" value={fmtPct(res.liquidated_longs_percent)}
                    warn={res.liquidated_longs_percent >= 40} />
              <Stat label="Liquidated shorts" value={fmtPct(res.liquidated_shorts_percent)}
                    warn={res.liquidated_shorts_percent >= 40} />
              {res.retail?.long_percent != null && (
                <Stat label="Retail long (Myfxbook)" value={fmtPct(res.retail.long_percent)} />
              )}
              {res.retail_gap != null && (
                <Stat label="Gap vs retail" value={`${res.retail_gap > 0 ? '+' : ''}${res.retail_gap}`}
                      warn={Math.abs(res.retail_gap) >= 15} />
              )}
            </div>
            <p className="art-method">{res.method}</p>
          </div>
        )}
      </div>
    </section>
  )
}

function Stat({ label, value, warn }) {
  return (
    <div className="art-stat">
      <span className="art-stat-label">{label}</span>
      <span className={'art-stat-value' + (warn ? ' art-stat-value--warn' : '')}>
        {value ?? '—'}
      </span>
    </div>
  )
}

const fmtPct = (v) => (v == null ? '—' : `${v}%`)
