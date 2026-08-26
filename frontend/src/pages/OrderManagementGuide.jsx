import { useEffect, useState } from 'react'
import { KeyRound } from 'lucide-react'
import { Link } from 'react-router-dom'
import DashboardLayout from '../components/DashboardLayout.jsx'
import { ApiEndpoint, buildUrl } from '../components/ApiEndpoint.jsx'
import * as api from '../services/api.js'

const R = 'required'
const O = 'optional'

const ENDPOINTS = [
  // ── Positions ────────────────────────────────────────────────────────────
  { group: 'Positions', id: 'close', path: '/api/v1/close', title: 'Close position(s)', desc: 'Versatile targeting — one position (position_id), all on a symbol (symbol), or ALL when both omitted. only=profit|loss closes just winners or losers.', params: [
    { name: 'symbol', example: 'XAUUSD', level: O, desc: 'All positions on this symbol.' },
    { name: 'position_id', example: '', level: O, desc: 'One specific position.' },
    { name: 'only', example: '', level: O, desc: "'profit' (winners) or 'loss' (losers)." },
    { name: 'volume', example: '', level: O, desc: 'Partial close volume.' },
  ] },
  { group: 'Positions', id: 'break-even', path: '/api/v1/break-even', title: 'Break even', desc: 'Move SL to entry — one position, all on a symbol, or ALL when both omitted. Losing trades are skipped (SL=entry would be invalid). offset_points locks a few points of profit.', params: [
    { name: 'symbol', example: 'XAUUSD', level: O, desc: 'All positions on this symbol.' },
    { name: 'position_id', example: '', level: O, desc: 'One specific position.' },
    { name: 'offset_points', example: '', level: O, desc: 'Lock this many points of profit.' },
  ] },
  { group: 'Positions', id: 'modify-position', path: '/api/v1/modify-position', title: 'Modify position SL/TP', desc: 'Set SL/TP on a position (id from /positions). 0 removes a level.', params: [
    { name: 'position_id', example: '', level: R, desc: 'Position id (from /positions).' },
    { name: 'sl', example: '', level: O, desc: 'New stop loss.' },
    { name: 'tp', example: '', level: O, desc: 'New take profit.' },
  ] },
  { group: 'Positions', id: 'delete-sltp', path: '/api/v1/delete-sltp', title: 'Delete SL/TP', desc: 'Remove SL and/or TP without closing — one position, all on a symbol, or ALL when both omitted.', params: [
    { name: 'symbol', example: 'XAUUSD', level: O, desc: 'All positions on this symbol.' },
    { name: 'position_id', example: '', level: O, desc: 'One specific position.' },
    { name: 'which', example: 'both', level: O, desc: "'both' | 'sl' | 'tp' (default both)." },
  ] },

  // ── Profit protection ────────────────────────────────────────────────────
  { group: 'Profit protection', id: 'lock-profit', path: '/api/v1/lock-profit', title: 'Lock profit', desc: "Trail SL to lock a % of profit — one position, all on a symbol, or ALL when both omitted.", params: [
    { name: 'percent', example: '60', level: R, desc: 'Percent of profit to lock.' },
    { name: 'symbol', example: 'XAUUSD', level: O, desc: 'All positions on this symbol.' },
    { name: 'position_id', example: '', level: O, desc: 'One specific position.' },
  ] },
  { group: 'Profit protection', id: 'lpm', path: '/api/v1/lock-profit-money', title: 'Lock profit money — start monitor', desc: 'Starts a SERVER-SIDE monitor over the live WebSocket. Returns immediately, then closes ALL trades if total profit retraces to percent% of its peak.', params: [
    { name: 'percent', example: '60', level: R, desc: 'Close all when profit falls to this % of its peak.' },
    { name: 'ref', example: 'peak', level: O, desc: "'peak' (default) or 'start'." },
  ] },
  { group: 'Profit protection', id: 'lpm-status', path: '/api/v1/lock-profit-money/status', title: 'Lock profit money — status', desc: 'Live monitor state: running, current profit, peak, triggered.', params: [] },
  { group: 'Profit protection', id: 'lpm-stop', path: '/api/v1/lock-profit-money/stop', title: 'Lock profit money — stop', desc: 'Stops the running monitor.', params: [] },
]

const GROUPS = [...new Set(ENDPOINTS.map((e) => e.group))]

export default function OrderManagementGuide() {
  const [apiKey, setApiKey] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const base = window.location.origin

  useEffect(() => {
    api.primaryKey().then((r) => setApiKey(r.api_key)).catch(() => setApiKey(null)).finally(() => setLoaded(true))
  }, [])

  return (
    <DashboardLayout title="Order Management API Guide">
      <div className="guide">
        <div className="guide-intro card">
          <div className="card-body">
            <h2 className="card-title">Order Management API</h2>
            <p className="card-sub">Act on your open positions — close, break even, lock profit, adjust SL/TP.</p>
            <div className="callout">
              <strong>Versatile targeting.</strong> Every action here works on{' '}
              <strong>one</strong> position (<code>position_id</code>),{' '}
              <strong>all on a symbol</strong> (<code>symbol</code>), or{' '}
              <strong>everything running</strong> when you omit both. <code>close</code> also filters by{' '}
              <code>only=profit</code> / <code>only=loss</code>.
            </div>
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

        {GROUPS.map((group) => (
          <div key={group} className="guide-group">
            <h2 className="guide-group-title">{group}</h2>
            {ENDPOINTS.filter((e) => e.group === group).map((ep) => (
              <ApiEndpoint key={ep.id} ep={ep} url={buildUrl(base, ep, apiKey)} base={base} apiKey={apiKey} />
            ))}
          </div>
        ))}
      </div>
    </DashboardLayout>
  )
}
