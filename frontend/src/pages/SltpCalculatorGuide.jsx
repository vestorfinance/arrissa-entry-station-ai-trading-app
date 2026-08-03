import { useEffect, useState } from 'react'
import { KeyRound, Calculator, SlidersHorizontal, Layers, Info } from 'lucide-react'
import { Link } from 'react-router-dom'
import DashboardLayout from '../components/DashboardLayout.jsx'
import { ApiEndpoint, buildUrl } from '../components/ApiEndpoint.jsx'
import * as api from '../services/api.js'

const R = 'required'
const O = 'optional'

const ENDPOINTS = [
  {
    id: 'calc-sltp',
    path: '/api/v1/calc/sltp',
    title: 'SL/TP calculator (any direction)',
    desc: 'The one endpoint for every money ⇄ points ⇄ price conversion on a trade. All money is in the ACCOUNT’S currency (USD, ZAR, EUR…), cross-rated automatically. Give symbol, volume, side and EXACTLY ONE of: money (target, with mode tp|sl), points (a distance, with mode), or level (a price → returns the resulting P/L, tp/sl auto-detected). entry defaults to the live quote.',
    params: [
      { name: 'symbol', example: 'XAUUSD', level: R, desc: 'Instrument.' },
      { name: 'volume', example: '10', level: R, desc: 'Lots.' },
      { name: 'side', example: 'sell', level: R, desc: 'buy | sell.' },
      { name: 'entry', example: '4000', level: O, desc: 'Entry/open price. Omit to use the live quote.' },
      { name: 'money', example: '5000', level: O, desc: 'Target dollars (pair with mode). → price level.' },
      { name: 'points', example: '', level: O, desc: 'Distance in points (pair with mode). → price + $.' },
      { name: 'level', example: '', level: O, desc: 'A price → returns $ P/L there (tp/sl auto).' },
      { name: 'mode', example: 'tp', level: O, desc: 'tp = take profit (default) | sl = stop loss.' },
    ],
  },
  {
    id: 'calc-risk',
    path: '/api/v1/calc/risk',
    title: 'Risk engine (size / stop / validate)',
    desc: 'The one call for risk-based position sizing and stop/target placement — symbol-aware, all money in the account currency. risk = stop_distance × $/price × volume; give ANY TWO of {risk, stop, volume} and it solves the third: SIZE (risk + stop → the exact VOLUME), STOP (risk + volume → where the stop goes), VALIDATE (stop + volume → what the trade really risks). Add rr for a take-profit at rr× the stop. Returns the sized volume (snapped to the symbol’s step/min), sl & tp prices with distances and money, realised risk/reward and rr, per-point value and the margin needed.',
    params: [
      { name: 'symbol', example: 'XAUUSD', level: R, desc: 'Instrument.' },
      { name: 'side', example: 'buy', level: R, desc: 'buy | sell.' },
      { name: 'risk_pct', example: '2', level: O, desc: 'Risk as a % of the account (basis equity|balance).' },
      { name: 'risk_money', example: '', level: O, desc: 'Risk as an absolute amount (account currency).' },
      { name: 'sl', example: '3990', level: O, desc: 'Stop-loss PRICE.' },
      { name: 'sl_points', example: '', level: O, desc: 'Stop distance in points (instead of sl price).' },
      { name: 'rr', example: '2', level: O, desc: 'Reward:risk → TP at rr× the stop distance.' },
      { name: 'tp', example: '', level: O, desc: 'Take-profit PRICE (instead of rr).' },
      { name: 'tp_points', example: '', level: O, desc: 'Target distance in points (instead of rr).' },
      { name: 'volume', example: '', level: O, desc: 'Fixed lots. Omit to have it SIZED from risk + stop.' },
      { name: 'entry', example: '4000', level: O, desc: 'Entry price. Omit to use the live quote.' },
      { name: 'basis', example: 'equity', level: O, desc: 'What risk_pct is a % of: equity (default) | balance.' },
    ],
  },
  {
    id: 'calc-auto-sltp',
    path: '/api/v1/calc/auto-sltp',
    title: 'Auto SL/TP (smart, market-aware)',
    desc: 'Hands-off: give only symbol, side and a style and the engine reads live candles to decide everything — it places the STOP at market structure (recent swing high/low) with an ATR floor and the broker stop-level as guards, sets the TARGET by a style-based reward:risk, and SIZES the lot so hitting the stop loses exactly the risk budget. If you pass neither risk_pct nor risk_money it uses your saved default-risk setting. Everything returned is in the account currency: volume, sl/tp prices, realised risk/reward, the ATR and structure it used, and margin.',
    params: [
      { name: 'symbol', example: 'XAUUSD', level: R, desc: 'Instrument.' },
      { name: 'side', example: 'buy', level: R, desc: 'buy | sell.' },
      { name: 'style', example: 'swing', level: O, desc: 'scalp | intraday | swing | position — sets timeframe, stop tightness and default RR.' },
      { name: 'risk_pct', example: '2', level: O, desc: 'Risk as a % of the account.' },
      { name: 'risk_money', example: '', level: O, desc: 'Risk as an absolute amount. Omit both ⇒ your saved default.' },
      { name: 'rr', example: '', level: O, desc: 'Override the style’s default reward:risk for the TP.' },
      { name: 'sl_mode', example: 'structure', level: O, desc: 'structure (default) | atr | swing.' },
      { name: 'basis', example: '', level: O, desc: 'equity (default) | balance — what risk_pct is a % of.' },
      { name: 'entry', example: '', level: O, desc: 'Entry price. Omit to use the live quote.' },
    ],
  },
  {
    id: 'calc-point-value',
    path: '/api/v1/calc/point-value',
    title: 'Point value',
    desc: 'What one point of price movement is worth in dollars for a given size — and what a full 1.0 move is worth. Handy to sanity-check any figure.',
    params: [
      { name: 'symbol', example: 'XAUUSD', level: R, desc: 'Instrument.' },
      { name: 'volume', example: '10', level: O, desc: 'Lots (default 1).' },
      { name: 'price', example: '', level: O, desc: 'Reference price; defaults to the live quote (matters only for USD-base FX like USDJPY).' },
    ],
  },
  {
    id: 'calc-basket-target',
    path: '/api/v1/calc/basket-target',
    title: 'Basket target (many trades)',
    desc: 'Spread ONE total target across several open trades: a TP (or SL) for each so that when they ALL hit, the combined result is your number. Omit positions to use your live open trades; add apply=true to write the levels straight onto them.',
    params: [
      { name: 'target', example: '5000', level: R, desc: 'Total dollars across all trades (profit for tp, loss for sl).' },
      { name: 'mode', example: 'tp', level: O, desc: 'tp (default) | sl.' },
      { name: 'split', example: 'weighted', level: O, desc: 'weighted = ∝ size (same points each) | equal = same $ each.' },
      { name: 'symbol', example: '', level: O, desc: 'When using live trades, only include this instrument.' },
      { name: 'positions', example: '', level: O, desc: 'JSON list [{symbol,entry,volume,side}] to compute without live trades.' },
      { name: 'apply', example: '', level: O, desc: 'true = write each level onto the matching live position.' },
    ],
  },
]

// what the single calculator solves for
const KNOWS = [
  { id: 'money', label: 'a dollar target', hint: 'money' },
  { id: 'points', label: 'a points distance', hint: 'points' },
  { id: 'level', label: 'a price level', hint: 'level' },
]

export default function SltpCalculatorGuide() {
  const [apiKey, setApiKey] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const base = window.location.origin

  useEffect(() => {
    api.primaryKey().then((r) => setApiKey(r.api_key)).catch(() => setApiKey(null)).finally(() => setLoaded(true))
  }, [])

  return (
    <DashboardLayout title="SL/TP Calculator">
      <div className="guide">
        <div className="guide-intro card">
          <div className="card-body">
            <h2 className="card-title">Analysis — SL/TP Calculator</h2>
            <p className="card-sub">
              One versatile calculator for every Stop Loss / Take Profit sum: dollars ⇄ points ⇄
              price, in any direction. Where do I put my TP to make $5,000? What’s my P/L if price
              hits 4010? How many points is $500? What’s a point worth? It also does baskets —
              several trades, one total target, a level for each. Every figure is in your account’s
              own currency — a ZAR account gets Rands, cross-rated automatically (e.g. USD→ZAR via
              USDZAR). The agent uses this for any such calculation, so it never guesses the maths.
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

        <PointsExplainer />
        <VersatileCalculator apiKey={apiKey} base={base} />
        <BasketCalculator apiKey={apiKey} base={base} />

        {ENDPOINTS.map((ep) => (
          <ApiEndpoint key={ep.id} ep={ep} url={buildUrl(base, ep, apiKey)} />
        ))}
      </div>
    </DashboardLayout>
  )
}

function PointsExplainer() {
  return (
    <section className="card">
      <div className="card-head">
        <Info size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">What a “point” actually is</h2>
          <p className="card-sub">The old miscalculation came from confusing points, price, and dollars. Here is the whole model.</p>
        </div>
      </div>
      <div className="card-body">
        <ul className="muted" style={{ lineHeight: 1.7, margin: 0, paddingLeft: 18 }}>
          <li>A <strong>point</strong> is the smallest price step of an instrument. XAUUSD has 3 digits, so one point = <code>0.001</code> — 4000.000 → 4000.001 is one point. A point is NOT a whole dollar of price.</li>
          <li>The money a trade makes is <code>price_move × volume × contract_size</code>. Gold’s contract size is <code>100</code>.</li>
          <li>10 lots of gold = <code>10 × 100 = $1,000</code> for every <strong>1.0</strong> of price, i.e. <code>$1</code> per point.</li>
          <li>Want <strong>$5,000</strong>? Price must move <code>5000 ÷ 1000 = 5.0</code> (that’s 5,000 points). Short from 4000 → TP at <strong>3995.000</strong> — not 3999.944, which was only ~$56 and is why the trade drifted to +$24k floating without ever closing.</li>
        </ul>
      </div>
    </section>
  )
}

function fmt(n) {
  return typeof n === 'number' ? n.toLocaleString(undefined, { maximumFractionDigits: 8 }) : n
}

const CUR_SYMBOL = { USD: '$', EUR: '€', GBP: '£', JPY: '¥', ZAR: 'R', AUD: 'A$', NZD: 'NZ$', CAD: 'C$', CHF: 'CHF ' }
function money(code, n) {
  const s = CUR_SYMBOL[code] || (code ? `${code} ` : '')
  return `${s}${fmt(n)}`
}

function VersatileCalculator({ apiKey, base }) {
  const [knows, setKnows] = useState('money')
  const [f, setF] = useState({ symbol: 'XAUUSD', volume: '10', side: 'sell', entry: '4000', mode: 'tp', value: '5000' })
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const knowsMeta = KNOWS.find((k) => k.id === knows)

  async function run(e) {
    e.preventDefault()
    setBusy(true); setErr(null); setRes(null)
    try {
      const q = new URLSearchParams({ api_key: apiKey || '', symbol: f.symbol, volume: f.volume, side: f.side })
      if (f.entry.trim()) q.set('entry', f.entry.trim())
      q.set(knowsMeta.hint, f.value)
      if (knows !== 'level') q.set('mode', f.mode)
      const r = await fetch(`${base}/api/v1/calc/sltp?${q}`)
      const b = await r.json()
      if (b.detail || b.error) throw new Error(b.detail || b.error)
      setRes(b)
    } catch (e2) { setErr(e2.message) } finally { setBusy(false) }
  }

  return (
    <section className="card">
      <div className="card-head">
        <SlidersHorizontal size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">Calculator</h2>
          <p className="card-sub">Pick what you know — a dollar target, a points distance, or a price level — and get everything else.</p>
        </div>
      </div>
      <div className="card-body">
        <div className="pill-row" style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
          {KNOWS.map((k) => (
            <button key={k.id} type="button"
              className={'pill ' + (knows === k.id ? 'pill--ok' : 'pill--muted')}
              onClick={() => setKnows(k.id)} style={{ cursor: 'pointer', border: 'none' }}>
              I know {k.label}
            </button>
          ))}
        </div>

        <form className="form-grid" onSubmit={run}>
          <label className="field"><span className="field-label">Instrument</span>
            <input className="input" value={f.symbol} onChange={set('symbol')} placeholder="XAUUSD" /></label>
          <label className="field"><span className="field-label">Volume (lots)</span>
            <input className="input" value={f.volume} onChange={set('volume')} placeholder="10" /></label>
          <label className="field"><span className="field-label">Side</span>
            <select className="input" value={f.side} onChange={set('side')}>
              <option value="buy">buy</option><option value="sell">sell</option>
            </select></label>
          <label className="field"><span className="field-label">Entry price (optional)</span>
            <input className="input" value={f.entry} onChange={set('entry')} placeholder="live quote" /></label>
          <label className="field"><span className="field-label">
            {knows === 'money' ? 'Target $' : knows === 'points' ? 'Points' : 'Price level'}</span>
            <input className="input" value={f.value} onChange={set('value')} /></label>
          {knows !== 'level' && (
            <label className="field"><span className="field-label">Mode</span>
              <select className="input" value={f.mode} onChange={set('mode')}>
                <option value="tp">take profit</option><option value="sl">stop loss</option>
              </select></label>
          )}
          <div className="field form-submit">
            <button className="btn btn--primary" type="submit" disabled={busy || !apiKey}>
              <Calculator size={16} strokeWidth={2} />{busy ? 'Calculating…' : 'Calculate'}
            </button>
          </div>
        </form>

        {err && <div className="alert alert--danger" style={{ marginTop: 12 }}>{err}</div>}

        {res && (
          <div className="calc-result" style={{ marginTop: 16 }}>
            <div className="calc-headline" style={{ fontSize: 22, fontWeight: 700 }}>
              {res.mode === 'sl' ? 'Stop loss' : 'Take profit'} at{' '}
              <span style={{ color: 'var(--accent, #16a34a)' }}>{fmt(res.level)}</span>
              {'  ·  '}
              <span style={{ color: res.money >= 0 ? 'var(--accent, #16a34a)' : '#dc2626' }}>
                {res.money >= 0 ? '+' : '−'}{money(res.account_currency, res.money_abs)}
              </span>
            </div>
            <p className="card-sub" style={{ marginTop: 6 }}>
              {fmt(res.volume)} lots {res.symbol} {res.side} from {fmt(res.entry)} → {res.distance_points} points away.
              {res.quote_currency && res.quote_currency !== res.account_currency &&
                ` P/L converted ${res.quote_currency}→${res.account_currency}.`}
            </p>
            <div className="params-inline" style={{ display: 'flex', flexWrap: 'wrap', gap: 20, marginTop: 12 }}>
              <Stat label="Level" value={fmt(res.level)} />
              <Stat label="Distance (price)" value={fmt(res.distance_price)} />
              <Stat label="Distance (points)" value={fmt(res.distance_points)} />
              <Stat label="P/L at level" value={`${res.money >= 0 ? '+' : '−'}${money(res.account_currency, res.money_abs)}`} />
              <Stat label={`${res.account_currency || ''} / point`} value={money(res.account_currency, res.money_per_point)} />
              <Stat label={`${res.account_currency || ''} / 1.0 move`} value={money(res.account_currency, res.money_per_price_unit)} />
            </div>
            {!res.exact && (
              <div className="alert alert--warn" style={{ marginTop: 12 }}>
                Approximate: {res.symbol} isn’t USD-quoted, so a cross-rate is needed for an exact figure.
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  )
}

function Stat({ label, value }) {
  return (
    <div>
      <div className="field-label">{label}</div>
      <div style={{ fontSize: 16, fontWeight: 600 }}>{value}</div>
    </div>
  )
}

function BasketCalculator({ apiKey, base }) {
  const [target, setTarget] = useState('5000')
  const [mode, setMode] = useState('tp')
  const [split, setSplit] = useState('weighted')
  const [symbol, setSymbol] = useState('')
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  async function run(e) {
    e.preventDefault()
    setBusy(true); setErr(null); setRes(null)
    try {
      const q = new URLSearchParams({ api_key: apiKey || '', target, mode, split })
      if (symbol.trim()) q.set('symbol', symbol.trim())
      const r = await fetch(`${base}/api/v1/calc/basket-target?${q}`)
      const b = await r.json()
      if (b.detail || b.error) throw new Error(b.detail || b.error)
      setRes(b)
    } catch (e2) { setErr(e2.message) } finally { setBusy(false) }
  }

  return (
    <section className="card">
      <div className="card-head">
        <Layers size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">Basket calculator (live trades)</h2>
          <p className="card-sub">One total target across all your open trades → a level for each. Uses your live positions.</p>
        </div>
      </div>
      <div className="card-body">
        <form className="form-grid" onSubmit={run}>
          <label className="field"><span className="field-label">Total target $</span>
            <input className="input" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="5000" /></label>
          <label className="field"><span className="field-label">Mode</span>
            <select className="input" value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="tp">take profit</option><option value="sl">stop loss</option>
            </select></label>
          <label className="field"><span className="field-label">Split</span>
            <select className="input" value={split} onChange={(e) => setSplit(e.target.value)}>
              <option value="weighted">weighted (same points)</option>
              <option value="equal">equal ($ each)</option>
            </select></label>
          <label className="field"><span className="field-label">Only symbol (optional)</span>
            <input className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="all" /></label>
          <div className="field form-submit">
            <button className="btn btn--primary" type="submit" disabled={busy || !apiKey}>
              <Calculator size={16} strokeWidth={2} />{busy ? 'Calculating…' : 'Calculate'}
            </button>
          </div>
        </form>

        {err && <div className="alert alert--danger" style={{ marginTop: 12 }}>{err}</div>}

        {res && (
          <>
            <div className="sym-section-title" style={{ marginTop: 14 }}>
              {res.positions} trade{res.positions === 1 ? '' : 's'} · target {money(res.account_currency, res.target_money)} · allocated {money(res.account_currency, res.allocated_money)}
            </div>
            <table className="params" style={{ marginTop: 8 }}>
              <thead>
                <tr><th>Symbol</th><th>Side</th><th>Vol</th><th>Entry</th><th>{mode === 'sl' ? 'SL' : 'TP'}</th><th>Points</th><th>Share {res.account_currency || '$'}</th></tr>
              </thead>
              <tbody>
                {res.legs.map((l, i) => (
                  <tr key={i}>
                    <td><code>{l.symbol}</code></td>
                    <td>{l.side}</td>
                    <td>{fmt(l.volume)}</td>
                    <td>{fmt(l.entry)}</td>
                    <td><strong>{fmt(l.level)}</strong></td>
                    <td>{fmt(l.distance_points)}</td>
                    <td>{money(res.account_currency, l.share_money)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="card-sub" style={{ marginTop: 10 }}>
              To write these onto the trades, re-run the <code>/calc/basket-target</code> endpoint below with <code>apply=true</code>.
            </p>
          </>
        )}
      </div>
    </section>
  )
}
