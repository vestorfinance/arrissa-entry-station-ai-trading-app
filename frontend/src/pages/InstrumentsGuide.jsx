import { useEffect, useState } from 'react'
import { KeyRound, Search } from 'lucide-react'
import { Link } from 'react-router-dom'
import DashboardLayout from '../components/DashboardLayout.jsx'
import { ApiEndpoint, buildUrl } from '../components/ApiEndpoint.jsx'
import * as api from '../services/api.js'

const R = 'required'
const O = 'optional'

const ENDPOINTS = [
  {
    id: 'symbol',
    path: '/api/v1/symbol',
    title: 'Symbol details',
    desc: 'Everything about one symbol: full spec, live bid/ask/spread, and your open positions & pending orders on it.',
    params: [{ name: 'symbol', example: 'XAUUSD', level: R, desc: 'The symbol to analyse.' }],
  },
  {
    id: 'instruments',
    path: '/api/v1/instruments',
    title: 'List instruments',
    desc: 'All tradable instruments with full specs. Filter by category, substring search, or exact symbol. data=min returns only the essential fields.',
    params: [
      { name: 'category', example: 'Crypto', level: O, desc: 'Filter by category (see /instruments/categories).' },
      { name: 'search', example: '', level: O, desc: 'Substring match on symbol, e.g. CAD.' },
      { name: 'symbol', example: '', level: O, desc: 'Exact symbol, e.g. CADJPY (returns just that one).' },
      { name: 'data', example: 'min', level: O, desc: "'full' (default) or 'min'." },
    ],
  },
  {
    id: 'symbols',
    path: '/api/v1/instruments/symbols',
    title: 'Symbols only',
    desc: 'A flat list of symbol strings — ideal for dropdowns or symbol search.',
    params: [
      { name: 'search', example: 'CAD', level: O, desc: 'Substring match, e.g. CAD.' },
      { name: 'category', example: '', level: O, desc: 'Filter by category.' },
    ],
  },
  {
    id: 'categories',
    path: '/api/v1/instruments/categories',
    title: 'Categories',
    desc: 'Distinct instrument categories, each with a count of symbols in it.',
    params: [],
  },
]

export default function InstrumentsGuide() {
  const [apiKey, setApiKey] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const base = window.location.origin

  useEffect(() => {
    api.primaryKey().then((r) => setApiKey(r.api_key)).catch(() => setApiKey(null)).finally(() => setLoaded(true))
  }, [])

  return (
    <DashboardLayout title="Instruments API Guide">
      <div className="guide">
        <div className="guide-intro card">
          <div className="card-body">
            <h2 className="card-title">Instruments API</h2>
            <p className="card-sub">
              Search a symbol to analyse it, browse instruments, filter by category, or fetch a lean
              symbols list. Every example below is a real, working URL.
            </p>
            {loaded && !apiKey && (
              <div className="alert alert--danger" style={{ marginTop: 12 }}>
                No active API key found.{' '}
                <Link to="/settings" style={{ textDecoration: 'underline' }}>Generate one in Settings</Link>{' '}
                to use the explorer and examples.
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

        <SymbolExplorer apiKey={apiKey} base={base} />

        {ENDPOINTS.map((ep) => (
          <ApiEndpoint key={ep.id} ep={ep} url={buildUrl(base, ep, apiKey)} />
        ))}
      </div>
    </DashboardLayout>
  )
}

function SymbolExplorer({ apiKey, base }) {
  const [query, setQuery] = useState('XAUUSD')
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function search(e) {
    e?.preventDefault()
    const sym = query.trim().toUpperCase()
    if (!sym) return
    setError(''); setLoading(true); setData(null)
    try {
      const url = `${base}/api/v1/symbol?api_key=${encodeURIComponent(apiKey || '')}&symbol=${encodeURIComponent(sym)}`
      const res = await fetch(url)
      const body = await res.json()
      if (!res.ok) throw new Error(body.detail || `Not found (${res.status})`)
      setData(body)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const spec = data?.instrument || {}
  const specFields = [
    ['Name', spec.international],
    ['Category', spec.category],
    ['Base', spec.base_currency],
    ['Quote', spec.quote_currency],
    ['Digits', spec.digits],
    ['Contract size', spec.contract_size],
    ['Min volume', spec.volume_min],
    ['Max volume', spec.volume_max],
    ['Volume step', spec.volume_step],
    ['Swap long', spec.swap_long],
    ['Swap short', spec.swap_short],
  ].filter(([, v]) => v !== undefined && v !== null && v !== '')

  return (
    <section className="card">
      <div className="card-head">
        <Search size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">Symbol explorer</h2>
          <p className="card-sub">Search a symbol to see its full details, live price and open positions.</p>
        </div>
      </div>
      <div className="card-body">
        <form className="key-create" onSubmit={search}>
          <input
            className="input"
            placeholder="Symbol, e.g. XAUUSD"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button className="btn btn--primary" type="submit" disabled={loading || !apiKey}>
            <Search size={16} strokeWidth={2} />
            {loading ? 'Searching…' : 'Search'}
          </button>
        </form>

        {error && <div className="alert alert--danger">{error}</div>}

        {data && (
          <div className="sym-detail">
            <div className="sym-head">
              <h3 className="sym-title">{data.symbol}</h3>
              {spec.category && <span className="pill">{spec.category}</span>}
              {spec.description && <span className="sym-desc">{spec.description}</span>}
            </div>

            <div className="stat-row">
              <Stat label="Bid" value={data.price.bid} />
              <Stat label="Ask" value={data.price.ask} />
              <Stat label="Spread" value={data.price.spread} />
              <Stat label="Open positions" value={data.summary.open_positions} />
              <Stat label="Pending orders" value={data.summary.pending_orders} />
              <Stat
                label="Floating P/L"
                value={data.summary.floating_profit}
                tone={data.summary.floating_profit > 0 ? 'ok' : data.summary.floating_profit < 0 ? 'bad' : ''}
              />
            </div>

            <div className="sym-section-title">Specification</div>
            <div className="spec-grid">
              {specFields.map(([k, v]) => (
                <div className="spec-item" key={k}>
                  <span className="spec-k">{k}</span>
                  <span className="spec-v">{String(v)}</span>
                </div>
              ))}
            </div>

            {data.positions.length > 0 && (
              <>
                <div className="sym-section-title">Open positions</div>
                <div className="key-list">
                  {data.positions.map((p) => (
                    <div className="key-row" key={p.position_id}>
                      <div className="key-row-main">
                        <span className="key-name">
                          {Number(p.type) % 2 === 0 ? 'BUY' : 'SELL'} {p.volume} @ {p.open_price}
                        </span>
                        <span className="key-masked">id {p.position_id}</span>
                      </div>
                      <span className={`pill ${p.profit >= 0 ? 'pill--ok' : 'pill--warn'}`}>
                        {p.profit >= 0 ? '+' : ''}{p.profit}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}

            {data.orders.length > 0 && (
              <>
                <div className="sym-section-title">Pending orders</div>
                <div className="key-list">
                  {data.orders.map((o) => (
                    <div className="key-row" key={o.ticket_id || o.order_id}>
                      <div className="key-row-main">
                        <span className="key-name">{o.volume} @ {o.price}</span>
                        <span className="key-masked">ticket {o.ticket_id || o.order_id}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </section>
  )
}

function Stat({ label, value, tone }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${tone === 'ok' ? 'stat-ok' : tone === 'bad' ? 'stat-bad' : ''}`}>
        {String(value)}
      </span>
    </div>
  )
}
