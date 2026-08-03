import { useEffect, useState, useCallback } from 'react'
import { KeyRound, Wallet } from 'lucide-react'
import { Link } from 'react-router-dom'
import DashboardLayout from '../components/DashboardLayout.jsx'
import { ApiEndpoint, buildUrl } from '../components/ApiEndpoint.jsx'
import * as api from '../services/api.js'

const O = 'optional'
const RANGES = [
  ['today', 'Today'], ['yesterday', 'Yesterday'], ['this_week', 'This week'],
  ['last_week', 'Last week'], ['last_2_weeks', 'Last 2 weeks'], ['last_month', 'Last month'],
  ['last_3_months', 'Last 3 months'], ['last_6_months', 'Last 6 months'],
]
const RANGE_DESC = 'today | yesterday | this_week | last_week | last_2_weeks | last_month | last_3_months | last_6_months'

const ENDPOINTS = [
  { id: 'account', path: '/api/v1/account', title: 'Account info + P/L', desc: 'Account details, balance/equity/floating profit, and realised P/L totals over a period.', params: [{ name: 'range', example: 'last_month', level: O, desc: RANGE_DESC }] },
  { id: 'balance', path: '/api/v1/balance', title: 'Account stats', desc: 'Balance, equity, margin, free margin — plus floating profit (equity − balance).', params: [] },
  { id: 'total-profit', path: '/api/v1/total-profit', title: 'Total floating profit', desc: 'Account-wide unrealised P/L (equity − balance).', params: [] },
  { id: 'history', path: '/api/v1/history', title: 'Trade history', desc: 'Closed trades for a period + a P/L summary. Pick a range preset.', params: [
    { name: 'range', example: 'today', level: O, desc: RANGE_DESC },
    { name: 'symbol', example: '', level: O, desc: 'Filter to one instrument.' },
  ] },
  { id: 'server', path: '/api/v1/server', title: 'Server status', desc: 'Trade server name, time and status.', params: [] },
]

export default function AccountGuide() {
  const [apiKey, setApiKey] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const base = window.location.origin

  useEffect(() => {
    api.primaryKey().then((r) => setApiKey(r.api_key)).catch(() => setApiKey(null)).finally(() => setLoaded(true))
  }, [])

  return (
    <DashboardLayout title="Account Info API Guide">
      <div className="guide">
        <div className="guide-intro card">
          <div className="card-body">
            <h2 className="card-title">Account Info API</h2>
            <p className="card-sub">
              Account details, live balance, and realised profit &amp; loss totals from trade history —
              choose any period. Every example is a real, working URL.
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

        <AccountOverview apiKey={apiKey} base={base} />

        {ENDPOINTS.map((ep) => (
          <ApiEndpoint key={ep.id} ep={ep} url={buildUrl(base, ep, apiKey)} />
        ))}
      </div>
    </DashboardLayout>
  )
}

function AccountOverview({ apiKey, base }) {
  const [range, setRange] = useState('last_month')
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    if (!apiKey) return
    setLoading(true); setError('')
    try {
      const res = await fetch(`${base}/api/v1/account?api_key=${encodeURIComponent(apiKey)}&range=${range}`)
      const body = await res.json()
      if (!res.ok) throw new Error(body.detail || `Failed (${res.status})`)
      setData(body)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [apiKey, base, range])

  useEffect(() => { load() }, [load])

  const b = data?.balance || {}
  const p = data?.pnl || {}

  return (
    <section className="card">
      <div className="card-head">
        <Wallet size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">Account overview</h2>
          <p className="card-sub">Live balance and realised P/L for the selected period.</p>
        </div>
      </div>
      <div className="card-body">
        <div className="key-create">
          <select className="input" value={range} onChange={(e) => setRange(e.target.value)}>
            {RANGES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
          <button className="btn btn--primary" onClick={load} disabled={loading || !apiKey}>
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>

        {error && <div className="alert alert--danger">{error}</div>}

        {data && (
          <>
            <div className="sym-section-title">Balance</div>
            <div className="stat-row">
              <Stat label="Balance" value={b.balance} />
              <Stat label="Equity" value={b.equity} />
              <Stat label="Free margin" value={b.free_margin} />
              <Stat label="Floating P/L" value={b.floating_profit}
                    tone={b.floating_profit > 0 ? 'ok' : b.floating_profit < 0 ? 'bad' : ''} />
            </div>

            <div className="sym-section-title">Realised P/L · {RANGES.find(([v]) => v === range)?.[1]}</div>
            <div className="stat-row">
              <Stat label="Net P/L" value={p.net_profit}
                    tone={p.net_profit > 0 ? 'ok' : p.net_profit < 0 ? 'bad' : ''} />
              <Stat label="Gross profit" value={p.gross_profit} tone="ok" />
              <Stat label="Gross loss" value={p.gross_loss} tone="bad" />
              <Stat label="Trades" value={p.trades} />
              <Stat label="Wins" value={p.wins} />
              <Stat label="Losses" value={p.losses} />
              <Stat label="Win rate" value={p.win_rate != null ? p.win_rate + '%' : '—'} />
              <Stat label="Swap" value={p.swap} />
            </div>
          </>
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
        {String(value ?? '—')}
      </span>
    </div>
  )
}
