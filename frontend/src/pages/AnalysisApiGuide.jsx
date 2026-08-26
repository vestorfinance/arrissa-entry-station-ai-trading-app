import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { KeyRound, Bot, Signal, Workflow, CalendarClock, Clock, Play, Copy, Check, Eye, RefreshCw } from 'lucide-react'
import DashboardLayout from '../components/DashboardLayout.jsx'
import CopyId from '../components/CopyId.jsx'
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


const EXAMPLE_MESSAGE = 'Analyse BTCUSD for a scalper to enter immediately'

// The signal each run distils down to — documented field by field.
const SIGNAL_FIELDS = [
  ['symbol', 'string', 'The instrument the agent analysed, e.g. BTCUSD.'],
  ['direction', 'BUY | SELL | NONE', 'The side to take. NONE = the analysis says stay out.'],
  ['order_type', 'MARKET | BUY_STOP | SELL_STOP | BUY_LIMIT | SELL_LIMIT | NONE',
    'How to enter. MARKET = take it now at the current price. The four pending types = rest an order at entry and wait. NONE = no trade.'],
  ['entry', 'price | null', 'For a pending order, the trigger price the order rests at. For MARKET, the price the setup was built from.'],
  ['quality', '0–5', 'The agent’s own confidence out of 5 — 5 = everything aligns, ripe to trade; 0 = no setup.'],
  ['sl', 'price', 'Stop-loss PRICE. Exact when the flow has a risk-management node, else the level stated in the analysis.'],
  ['tp', 'price', 'Take-profit PRICE, same rule as sl.'],
  ['price', 'price | null', 'The market price the analysis was written against — what entry is measured from.'],
  ['rr', 'number | null', 'Reward:risk of the plan. There is deliberately NO position size — sizing depends on the account placing the order, so it belongs to your client, not to the analysis.'],
  ['note', 'string | null', 'One line on why, in the agent’s words.'],
  ['analysis_id', 'string', 'Unique id for this analysis. A shared answer keeps the FIRST caller’s id — it identifies the analysis, not the caller.'],
  ['comment', 'string', 'Ready-made order comment, SYMBOL_ANALYSISID, already clipped to the 31 characters MT5 stores. Put it on every order so a position traces back to its analysis.'],
]

// One pick from the daily scan.
const PICK_FIELDS = [
  ['symbol', 'string', 'The instrument, as your account trades it.'],
  ['direction', 'BUY | SELL', 'The side the scan wants.'],
  ['order_type', 'MARKET | BUY_STOP | SELL_STOP | BUY_LIMIT | SELL_LIMIT', 'How to get in — same meaning as in the signal above.'],
  ['entry / sl / tp', 'price', 'The levels. entry is the trigger for a pending order.'],
  ['windows_utc', '[{start, end, why}]', 'The UTC windows this one is worth trading in — anchored to session hours and the day’s scheduled events.'],
  ['quality', '1–5', 'How ripe it is, 5 = best of the day.'],
  ['why / invalidation', 'string', 'The reasoning, and what would kill the idea.'],
  ['price_at_scan', 'price', 'What the instrument traded at when the scan ran — the levels are relative to this.'],
]

// A real pick, trimmed — what a caller actually parses.
const SCAN_EXAMPLE = `{
  "scan_date": "2026-07-31",
  "status": "ok",
  "summary": "Yen crosses dominate with strong bearish momentum from BoJ intervention…",
  "symbols_scanned": 34,
  "picks": [
    {
      "symbol": "USDJPY",
      "category": "major_fx",
      "direction": "SELL",
      "order_type": "SELL_LIMIT",
      "entry": 159.45,
      "sl": 160.15,
      "tp": 157.95,
      "rr": 2.14,
      "price_at_scan": 159.178,
      "windows_utc": [
        { "start": "12:00", "end": "16:00",
          "why": "London/NY overlap; BoJ intervention and weak USD keep JPY firm." }
      ],
      "quality": 5,
      "why": "Downtrend, 5-day move -4.38 ATR, price near the 20-day low…",
      "invalidation": "Price climbs above 160.15 or intervention reverses."
    }
  ]
}`

const WATCH_EXAMPLE = `{
  "today_watch_list": {
    "date": "2026-07-31",
    "run_utc": "06:00",
    "instruments_considered": 34,
    "watching": 5,
    "symbols": {
      "EURUSD": {
        "times":  ["07:15", "12:15", "12:30", "13:23"],
        "prices": [1.15226, 1.15366, 1.14978, 1.14822],
        "support":    [1.14978, 1.14822, 1.14724],
        "resistance": [1.15226, 1.15366, 1.15614],
        "price_now": 1.15159,
        "trend": "up",
        "volatility_expansion": 1.504,
        "sentiment": { "long_percent": 31, "crowd": "short", "skew": 19, "interesting": true },
        "source": "code+news",
        "why": "calendar + volatility + crowd positioning · ECB speech lifts the euro",
        "time_reasons": [
          { "time": "07:15", "anchor": "session", "why": "London open +15m" },
          { "time": "12:30", "anchor": "event",   "why": "USD Non-Farm Payrolls" },
          { "time": "13:23", "anchor": "news",    "why": "+15m after: Fed official signals cuts" }
        ]
      }
    }
  }
}`

// The funnel, in the order it runs.
const FUNNEL = [
  ['1 · Calendar', 'code', 'The day’s high AND moderate events → every instrument they touch, directly (the event’s own currency or named instruments) or indirectly (a US print moves the indices, metals, oil, crypto and the JPY crosses).'],
  ['2 · Volatility', 'code', 'Of those, the ones actually moving — the week’s ATR against the month’s. There is deliberately no trend filter: demanding an orderly moving-average stack threw out the violent markets that matter most.'],
  ['3 · Sentiment', 'code', 'Of those, the ones where the retail crowd is lopsided — 15+ points off 50/50. It only gates instruments the crowd is actually measured on: indices, energies and crypto have no Myfxbook coverage, and a missing reading is not a boring one, so they pass through.'],
  ['4 · News + political posts', 'AI', 'The one model step: the system agent reads all high-impact news and Truth posts from the last 24h and names interesting instruments from the WHOLE unfiltered universe, independent of steps 1–3.'],
  ['Final list', '', 'The survivors of 1→2→3, plus everything the news agent named. `source` tells you which path put each instrument there.'],
]

const UNIVERSE = [
  ['Major FX', 'EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, NZDUSD'],
  ['Minor FX', 'The 21 crosses — EURGBP, EURJPY, GBPJPY, AUDJPY, CHFJPY, CADCHF …'],
  ['Indices', 'US30, NASDAQ (USTEC), US500, DE30'],
  ['Metals', 'XAUUSD, XAGUSD, XPTUSD, XPDUSD'],
  ['Energy', 'USOIL'],
  ['Crypto', 'BTCUSD, ETHUSD'],
]

export default function AnalysisApiGuide() {
  const [apiKey, setApiKey] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const [agents, setAgents] = useState([])
  const [picked, setPicked] = useState('')
  const base = window.location.origin

  useEffect(() => {
    api.primaryKey().then((r) => setApiKey(r.api_key)).catch(() => setApiKey(null)).finally(() => setLoaded(true))
    api.listAnalysisAgents()
      .then((list) => {
        setAgents(list || [])
        // default to a runnable agent: one that's active and actually has a flow
        const best = (list || []).find((a) => a.status === 'active' && (a.nodes ?? a.flow?.nodes?.length))
          || (list || [])[0]
        if (best) setPicked(best.id)
      })
      .catch(() => setAgents([]))
  }, [])

  const agentId = picked || 'YOUR_AGENT_ID'
  const endpoints = [
    {
      id: 'analysis',
      path: '/api/analysis',
      title: 'Run an analysis agent → trade signal',
      desc: 'Runs one of your analysis agents against a plain-language request and returns its verdict as a machine-readable signal, plus the full written analysis. Everything the agent’s flow does in chat happens here — live prices, news, sentiment, calendar, HMR, and the risk engine’s stop/target/lot sizing — so the numbers come back trade-ready. Metered on your credits like any AI run.',
      params: [
        { name: 'analysis_agent_id', example: agentId, level: R, desc: 'Which agent to run — copy it from the list above.' },
        { name: 'message', example: EXAMPLE_MESSAGE, level: R, desc: 'What to analyse, in plain language.' },
        { name: 'model', example: '', level: O, desc: 'arrissa-chat (default) | arrissa-pro for sharper reasoning.' },
        { name: 'include', example: '', level: O, desc: 'trace = also return every node’s full result.' },
            ...PRETEND,
    ],
    },
    {
      id: 'analysis-agents',
      path: '/api/analysis/agents',
      title: 'List your analysis agents',
      desc: 'Every agent on your account with its analysis_agent_id, status and node count — so a script can look up the id instead of hard-coding it.',
      params: [],
    },
    {
      id: 'watch-list',
      path: '/api/watch_list_daily',
      title: 'Daily Watch List — what to watch, when and at what price',
      desc: 'The instruments worth watching today, each with the UTC times and the price levels to watch them at. Built twice a day by the app’s own system analysis agent, which studies every instrument in the universe on its own against every data source in the app. Not a signal service — no entries, no buy/sell. An instant DB read; no model runs and no credits are spent.',
      params: [
        { name: 'date', example: '', level: O, desc: 'YYYY-MM-DD — a past day. Omit for the latest run.' },
        { name: 'slot', example: '', level: O, desc: 'Which run of that day: 00:00 or 06:00.' },
        { name: 'days', example: '', level: O, desc: 'Return the last N days of runs instead of one.' },
        { name: 'include', example: '', level: O, desc: 'assessment = the agent’s full write-up for each instrument (several KB each).' },
            ...PRETEND,
    ],
    },
    {
      id: 'watch-list-status',
      path: '/api/watch_list_daily/status',
      title: 'Watch list schedule & health',
      desc: 'The build schedule, the next run, how the last one went, and the id of the system agent behind it.',
      params: [],
    },
    {
      id: 'daily-scan',
      path: '/api/daily-scan',
      title: 'Daily Market Scan — today’s symbols, times and prices',
      desc: 'Reads the scan the system already ran at 00:00 UTC: the day’s most tradeable instruments out of the whole universe, each with a direction, an order type, entry/sl/tp and the UTC windows it can be traded in. An instant DB read — no model runs, no credits spent. Defaults to the most recent scan.',
      params: [
        { name: 'date', example: '', level: O, desc: 'YYYY-MM-DD — a past day’s scan. Omit for the latest.' },
        { name: 'days', example: '', level: O, desc: 'Return the last N scans instead of one (max 90).' },
        { name: 'include', example: '', level: O, desc: 'features and/or macro — the per-symbol measurements and the context the picks were made from.' },
            ...PRETEND,
    ],
    },
    {
      id: 'daily-scan-status',
      path: '/api/daily-scan/status',
      title: 'Daily scan schedule & health',
      desc: 'When it runs, when it runs next, and how the last run went.',
      params: [],
    },
  ]

  return (
    <DashboardLayout title="Analysis API">
      <div className="guide">
        <div className="guide-intro card">
          <div className="card-body">
            <h2 className="card-title">Analysis — run an agent, get a signal</h2>
            <p className="card-sub">
              One call runs an analysis agent you built on the canvas and hands back a signal your
              own system can act on: <code>symbol</code>, <code>direction</code>,{' '}
              <code>order_type</code>, a <code>quality</code> score out of 5, and the{' '}
              <code>entry</code> / <code>sl</code> / <code>tp</code> prices. The full written
              analysis comes along for the ride, so you can log or display the reasoning behind
              every signal.
            </p>
            <div className="code-block" style={{ marginTop: 12 }}>
              <code className="code-text">
                {`{ "signals": { "symbol": "BTCUSD", "direction": "BUY", "order_type": "MARKET", "quality": 3, "entry": 60123.5, "sl": 56356, "tp": 63773 } }`}
              </code>
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

        <section className="card">
          <div className="card-head">
            <Bot size={18} strokeWidth={1.75} />
            <div>
              <h2 className="card-title">Your agents &amp; their IDs</h2>
              <p className="card-sub">
                Pick one to fill the runnable URL below, or copy an ID straight into your own code.
                Only agents with a built flow can run.
              </p>
            </div>
          </div>
          <div className="card-body">
            {agents.length === 0 ? (
              <p className="muted">
                No analysis agents yet —{' '}
                <Link to="/analysis-agents" style={{ textDecoration: 'underline' }}>create one</Link>{' '}
                and build its flow first.
              </p>
            ) : (
              <div className="agent-id-list">
                {agents.map((a) => {
                  const nodes = a.nodes ?? a.flow?.nodes?.length ?? 0
                  return (
                    <label key={a.id} className={'agent-id-row' + (a.id === picked ? ' agent-id-row--on' : '')}>
                      <input type="radio" name="agent" checked={a.id === picked}
                             onChange={() => setPicked(a.id)} />
                      <span className="agent-id-main">
                        <span className="agent-id-name">{a.name}</span>
                        <span className="agent-id-sub">{a.description || 'No description'}</span>
                      </span>
                      <span className={`pill ${a.status === 'active' ? 'pill--ok' : 'pill--muted'}`}>{a.status}</span>
                      <span className="pill pill--muted">{nodes} nodes</span>
                      <CopyId value={a.id} label="ID" title="Copy agent ID" />
                    </label>
                  )
                })}
              </div>
            )}
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <Signal size={18} strokeWidth={1.75} />
            <div>
              <h2 className="card-title">The <code>signals</code> object</h2>
              <p className="card-sub">What every field means, and where the numbers come from.</p>
            </div>
          </div>
          <div className="card-body">
            <table className="params">
              <thead><tr><th>Field</th><th>Type</th><th>Meaning</th></tr></thead>
              <tbody>
                {SIGNAL_FIELDS.map(([name, type, desc]) => (
                  <tr key={name}>
                    <td><code>{name}</code></td>
                    <td><code>{type}</code></td>
                    <td>{desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted" style={{ marginTop: 12, lineHeight: 1.7 }}>
              <strong>Three answers, never anything else.</strong> No trade (
              <code>direction: "NONE"</code>), a trade to take right now (
              <code>order_type: "MARKET"</code>), or an order to rest at a level (
              <code>BUY_STOP</code> / <code>SELL_STOP</code> / <code>BUY_LIMIT</code> /{' '}
              <code>SELL_LIMIT</code>, with <code>entry</code> as the trigger). An analysis that
              says “short on a confirmed break of 64,527” is a <code>SELL_STOP</code> at 64,527 —
              it is never reported as a market sell, even when you asked to enter immediately,
              because filling it now would be a different trade at a different price. Which pending
              type it is comes from geometry, not wording: a trigger beyond the current price in
              the trade’s direction is a STOP, one price has to come back to is a LIMIT.
            </p>
            <p className="muted" style={{ marginTop: 12, lineHeight: 1.7 }}>
              <strong>Where the levels come from.</strong> If the agent’s flow contains a{' '}
              <strong>risk-management</strong> node, <code>sl</code>, <code>tp</code>,{' '}
              <code>entry</code> and <code>rr</code> are the engine’s own
              computed numbers — structure/ATR stop, reward:risk target and a lot size matched to
              your risk budget. Add that node to any agent you plan to trade from. Without it the
              levels are read out of the written analysis, which is fine for a heads-up but is only
              as precise as the text.
            </p>
          </div>
        </section>

        <WatchListCard apiKey={apiKey} base={base} />
        <DailyScanCard apiKey={apiKey} base={base} />

        {endpoints.map((ep) => (
          <ApiEndpoint key={ep.id} ep={ep} url={buildUrl(base, ep, apiKey)} />
        ))}

        <section className="card">
          <div className="card-head">
            <Workflow size={18} strokeWidth={1.75} />
            <div>
              <h2 className="card-title">Notes</h2>
            </div>
          </div>
          <div className="card-body">
            <ul className="muted" style={{ lineHeight: 1.8, margin: 0, paddingLeft: 18 }}>
              <li><code>/api/v1/analysis</code> is the same endpoint, if you prefer the versioned prefix.</li>
              <li>A run takes as long as the flow does — several seconds when it reads live data. Give your HTTP client a generous timeout.</li>
              <li>Identical calls within 5 seconds serve a cached result (billed at 20%), so a retry loop can’t double-charge you.</li>
              <li><code>direction: "NONE"</code> means the agent decided there is no trade — treat it as a valid answer, not an error. Every level is <code>null</code> then.</li>
              <li>A bare <code>order_type: "PENDING"</code> is the rare honest answer: the entry is conditional but no price was available to tell a stop from a limit. Compare <code>entry</code> with your own quote to place it.</li>
              <li>Account-aware nodes run on your <Link to="/accounts" style={{ textDecoration: 'underline' }}>active account</Link>.</li>
              <li>The Daily Market Scan is a system job — it costs you nothing to read, and the same scan is served to everyone.</li>
            </ul>
          </div>
        </section>
      </div>
    </DashboardLayout>
  )
}

// The system analysis agent: what it is, when it builds, today's actual list —
// plus the schedule and Run now controls, for admins only.
function WatchListCard({ apiKey, base }) {
  const [st, setSt] = useState(null)
  const [admin, setAdmin] = useState(false)
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState('')
  const [copied, setCopied] = useState('')
  const url = `${base}/api/watch_list_daily?api_key=${apiKey || 'YOUR_API_KEY'}`
  const curl = `curl "${url}"`

  const loadStatus = () => {
    if (!apiKey) return
    fetch(`${base}/api/watch_list_daily/status?api_key=${encodeURIComponent(apiKey)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => { if (s) setSt(s) })
      .catch(() => {})
  }
  useEffect(loadStatus, [apiKey, base])
  useEffect(() => { api.me().then((p) => setAdmin(!!p?.admin)).catch(() => {}) }, [])

  function copy(text, which) {
    navigator.clipboard.writeText(text)
    setCopied(which)
    setTimeout(() => setCopied(''), 1500)
  }

  async function run() {
    setBusy('get'); setRes(null)
    try {
      const r = await fetch(url)
      setRes({ status: r.status, body: await r.json() })
    } catch (e) {
      setRes({ status: 'ERR', body: { detail: String(e) } })
    } finally { setBusy('') }
  }



  const last = st?.last_run
  return (
    <section className="card">
      <div className="card-head">
        <Eye size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">Daily Watch List — what to watch, when, at what price</h2>
          <p className="card-sub">
            Built twice a day, <strong>mostly in code</strong>. A calendar → trend/volatility →
            sentiment funnel narrows the universe with maths, and exactly one AI step reads the
            last 24 hours of news and political posts to name anything the funnel would miss.
            Times are <strong>exact UTC clock points</strong>, never ranges: a story’s time +15m,
            the instrument’s session open +15m, and a major event’s own time. Prices are support
            and resistance computed from the candles — swing pivots, the previous day’s
            high/low/close, the 20-day range and the floor pivot. Not a signal service: no
            entries, no buy/sell.
          </p>
        </div>
      </div>
      <div className="card-body">
        <div className="scan-schedule">
          <Clock size={15} strokeWidth={1.75} />
          <div>
            <div className="scan-schedule-main">
              Builds at {(st?.schedule_utc || ['00:00', '06:00']).join(' and ')} UTC
            </div>
            <div className="scan-schedule-sub">
              {st ? <>Next {new Date(st.next_run_utc).toUTCString().replace(' GMT', '')} UTC
                {last ? ` · last ${last.date} ${last.slot}: ${last.watching} of ${last.considered} worth watching` : ' · no run yet'}
                {st.running ? ' · building now…' : ''}</>
                : 'Twice a day, system-wide.'}
            </div>
          </div>
          {st?.agent?.seeded && (
            <Link to={st.agent.editable_at} className="btn btn--ghost btn--sm" style={{ marginLeft: 'auto' }}>
              <Bot size={14} strokeWidth={1.75} /> Edit the agent
            </Link>
          )}
        </div>

        {admin && (
          <div className="watch-admin">
            <span className="field-label" style={{ margin: 0 }}>
              Admin · the build times live in{' '}
              <Link to="/admin/settings" style={{ textDecoration: 'underline' }}>Admin → Settings</Link>,
              where you can add or remove UTC slots and rebuild on demand.
            </span>
          </div>
        )}

        <div className="field-label" style={{ marginTop: 16 }}>How the list is built</div>
        <table className="params">
          <thead><tr><th>Stage</th><th>By</th><th>What it does</th></tr></thead>
          <tbody>
            {FUNNEL.map(([stage, by, desc]) => (
              <tr key={stage}>
                <td style={{ whiteSpace: 'nowrap' }}><strong>{stage}</strong></td>
                <td>{by && <span className={`pill ${by === 'AI' ? 'pill--warn' : 'pill--ok'}`}>{by}</span>}</td>
                <td>{desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted" style={{ marginTop: 10, lineHeight: 1.7 }}>
          The funnel is sequential, so a day with no high-impact events on the calendar contributes
          nothing from steps 1–3 and the list is whatever the news step names. Each run stores its
          funnel, so you can always see which stage narrowed it.
        </p>

        <div className="field-label" style={{ marginTop: 16 }}>Example response</div>
        <pre className="response-body">{WATCH_EXAMPLE}</pre>

        <div className="field-label">Runnable URL</div>
        <div className="code-block">
          <code className="code-text">{url}</code>
          <div className="code-actions">
            <button className="btn btn--ghost btn--icon" title="Copy URL" onClick={() => copy(url, 'url')}>
              {copied === 'url' ? <Check size={16} /> : <Copy size={16} />}
            </button>
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
          <button className="btn btn--primary" onClick={run} disabled={busy === 'get' || !apiKey}>
            <Play size={16} strokeWidth={2} />
            {busy === 'get' ? 'Loading…' : 'Get today’s list'}
          </button>
        </div>

        {res && <WatchResult res={res} />}
      </div>
    </section>
  )
}

function WatchResult({ res }) {
  const wl = res.body?.today_watch_list || {}
  const symbols = Object.entries(wl.symbols || {})
  return (
    <div className="response">
      <div className="response-head">
        <span className={`pill ${res.status === 200 ? 'pill--ok' : 'pill--warn'}`}>
          {res.status === 200 ? '200 OK' : `Status ${res.status}`}
        </span>
        {wl.date && <span className="muted">{wl.date} {wl.run_utc} · {wl.watching} of {wl.instruments_considered} worth watching</span>}
      </div>
      {symbols.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table className="params">
            <thead><tr><th>Symbol</th><th>Times (UTC)</th><th>Resistance</th><th>Support</th><th>Found by</th><th>Why</th></tr></thead>
            <tbody>
              {symbols.map(([sym, v]) => (
                <tr key={sym}>
                  <td><strong>{sym}</strong><div className="muted" style={{ fontSize: 11 }}>{v.trend}</div></td>
                  <td>{(v.times || []).map((t) => <code key={t} style={{ marginRight: 6 }}>{t}</code>) }
                      {!(v.times || []).length && <span className="muted">—</span>}</td>
                  <td>{(v.resistance || []).map((p) => <code key={p} style={{ marginRight: 6 }}>{p}</code>)}</td>
                  <td>{(v.support || []).map((p) => <code key={p} style={{ marginRight: 6 }}>{p}</code>)}</td>
                  <td><span className="pill pill--muted">{v.source}</span></td>
                  <td>{v.why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <pre className="response-body">{JSON.stringify(res.body, null, 2)}</pre>
    </div>
  )
}

// The no-AI scan: what it covers, when it runs, what a pick looks like — and
// today's actual scan, run against the reader's own key.
function DailyScanCard({ apiKey, base }) {
  const [st, setSt] = useState(null)
  const [err, setErr] = useState(null)
  const [scan, setScan] = useState(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState('')
  const url = `${base}/api/daily-scan?api_key=${apiKey || 'YOUR_API_KEY'}`
  const curl = `curl "${url}"`

  useEffect(() => {
    if (!apiKey) return
    fetch(`${base}/api/daily-scan/status?api_key=${encodeURIComponent(apiKey)}`)
      .then((r) => (r.ok ? r.json() : r.json().then((b) => Promise.reject(new Error(b.detail)))))
      .then(setSt)
      .catch((e) => setErr(e.message))
  }, [apiKey, base])

  function copy(text, which) {
    navigator.clipboard.writeText(text)
    setCopied(which)
    setTimeout(() => setCopied(''), 1500)
  }

  async function run() {
    setBusy(true)
    setScan(null)
    try {
      const r = await fetch(url)
      const body = await r.json()
      setScan({ status: r.status, body })
    } catch (e) {
      setScan({ status: 'ERR', body: { detail: String(e) } })
    } finally {
      setBusy(false)
    }
  }

  const when = (iso) => {
    if (!iso) return '—'
    const d = new Date(iso)
    return `${d.toUTCString().replace(' GMT', '')} UTC · ${d.toLocaleString()} your time`
  }

  return (
    <section className="card">
      <div className="card-head">
        <CalendarClock size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">Daily Market Scan — the built-in agent</h2>
          <p className="card-sub">
            Ships with the app rather than being drawn on a canvas: every day it measures the whole
            universe from live candles, weighs the day’s calendar, news, positioning and Fed odds,
            and stores the setups worth taking — with the UTC windows and the prices to take them at.
            Read it with <code>/api/daily-scan</code>; nothing runs at request time.
          </p>
        </div>
      </div>
      <div className="card-body">
        <div className="scan-schedule">
          <Clock size={15} strokeWidth={1.75} />
          <div>
            <div className="scan-schedule-main">
              {st ? st.schedule : 'Runs daily at 00:00 UTC'}
            </div>
            <div className="scan-schedule-sub">
              {err ? `Schedule unavailable — ${err}`
                : st ? <>Next run {when(st.next_run_utc)} · last scan {st.last_scan_date || '—'}
                    {st.last_status ? ` (${st.last_status}, ${st.last_picks} picks)` : ''}</>
                  : 'Fixed, system-wide — the same scan is served to every account.'}
            </div>
          </div>
        </div>

        <div className="field-label" style={{ marginTop: 16 }}>What it scans</div>
        <table className="params">
          <tbody>
            {UNIVERSE.map(([group, list]) => (
              <tr key={group}><td style={{ whiteSpace: 'nowrap' }}><strong>{group}</strong></td><td colSpan={2}>{list}</td></tr>
            ))}
          </tbody>
        </table>

        <div className="field-label" style={{ marginTop: 16 }}>What a pick looks like</div>
        <table className="params">
          <thead><tr><th>Field</th><th>Type</th><th>Meaning</th></tr></thead>
          <tbody>
            {PICK_FIELDS.map(([name, type, desc]) => (
              <tr key={name}>
                <td><code>{name}</code></td>
                <td><code>{type}</code></td>
                <td>{desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted" style={{ marginTop: 12, lineHeight: 1.7 }}>
          The shortlist is pure maths — trend, momentum in ATR units, volatility expansion against
          the instrument’s own baseline, and how close price sits to a 20-day edge — so it’s
          reproducible and costs nothing. Only that shortlist plus the day’s macro context goes to
          the model, and every pick it writes is validated (real symbol, real side, numeric levels,
          well-formed <code>HH:MM</code> windows) before it reaches the table. Add{' '}
          <code>include=features,macro</code> to see exactly what a day was decided on.
        </p>

        <div className="field-label" style={{ marginTop: 16 }}>Example response</div>
        <pre className="response-body">{SCAN_EXAMPLE}</pre>

        <div className="field-label">Runnable URL</div>
        <div className="code-block">
          <code className="code-text">{url}</code>
          <div className="code-actions">
            <button className="btn btn--ghost btn--icon" title="Copy URL" onClick={() => copy(url, 'url')}>
              {copied === 'url' ? <Check size={16} /> : <Copy size={16} />}
            </button>
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
          <button className="btn btn--primary" onClick={run} disabled={busy || !apiKey}>
            <Play size={16} strokeWidth={2} />
            {busy ? 'Loading…' : 'Get today’s scan'}
          </button>
        </div>

        {scan && <ScanResult res={scan} />}
      </div>
    </section>
  )
}

// The live scan rendered as a desk would read it, with the raw JSON underneath.
function ScanResult({ res }) {
  const b = res.body || {}
  const picks = b.picks || []
  return (
    <div className="response">
      <div className="response-head">
        <span className={`pill ${res.status === 200 ? 'pill--ok' : 'pill--warn'}`}>
          {res.status === 200 ? '200 OK' : `Status ${res.status}`}
        </span>
        {b.scan_date && <span className="muted">{b.scan_date} · {b.symbols_scanned} symbols scanned</span>}
      </div>
      {b.summary && <p className="card-sub" style={{ margin: '10px 0' }}>{b.summary}</p>}
      {picks.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table className="params">
            <thead>
              <tr><th>Symbol</th><th>Trade</th><th>Entry</th><th>SL</th><th>TP</th><th>RR</th><th>Windows (UTC)</th><th>Q</th></tr>
            </thead>
            <tbody>
              {picks.map((p) => (
                <tr key={p.symbol + p.entry}>
                  <td><strong>{p.symbol}</strong></td>
                  <td><span className={`pill ${p.direction === 'BUY' ? 'pill--ok' : 'pill--warn'}`}>{p.direction}</span>{' '}
                      <code>{p.order_type}</code></td>
                  <td><code>{p.entry}</code></td>
                  <td><code>{p.sl}</code></td>
                  <td><code>{p.tp}</code></td>
                  <td>{p.rr ?? '—'}</td>
                  <td>{(p.windows_utc || []).map((w) => `${w.start}–${w.end}`).join(', ') || '—'}</td>
                  <td>{p.quality ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <pre className="response-body">{JSON.stringify(b, null, 2)}</pre>
    </div>
  )
}
