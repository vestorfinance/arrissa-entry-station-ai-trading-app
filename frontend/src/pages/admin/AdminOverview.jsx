import { useEffect, useState } from 'react'
import AdminLayout from '../../components/AdminLayout.jsx'
import * as api from '../../services/api.js'

const fmt = (n) => Number(n || 0).toLocaleString()

export default function AdminOverview() {
  const [d, setD] = useState(null)
  const [h, setH] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    api.adminOverview().then(setD).catch((e) => setErr(e.message))
    api.adminHealth().then(setH).catch(() => {})
  }, [])

  return (
    <AdminLayout title="Overview">
      {err && <div className="alert alert--danger">{err}</div>}
      {!d ? <p className="muted">Loading…</p> : (
        <>
          <div className="kpi-grid">
            <Kpi label="Users" value={fmt(d.users.total)} sub={`+${d.users.new_7d} this week · +${d.users.new_30d} this month`} />
            <Kpi label="Active (7d)" value={fmt(d.users.active_7d)} />
            <Kpi label="Subscribers" value={fmt(d.subscriptions.active)} />
            <Kpi label="MRR" value={`R${fmt(d.subscriptions.mrr_zar)}`} accent />
            <Kpi label="Revenue (MTD)" value={`R${fmt(d.revenue.mtd_zar)}`} accent />
            <Kpi label="LLM cost (MTD)" value={`$${d.cost.llm_mtd_usd}`} />
            <Kpi label="Credits spent (MTD)" value={fmt(d.credits.spent_mtd)} sub={`${fmt(d.credits.spent_today)} today`} />
            <Kpi label="Pending signups" value={fmt(d.users.pending_signups)} />
          </div>

          <div className="admin-two">
            <section className="card">
              <div className="card-head"><div><h2 className="card-title">Subscribers by plan</h2></div></div>
              <div className="card-body">
                {Object.keys(d.subscriptions.by_plan).length === 0
                  ? <p className="muted">No active subscribers yet.</p>
                  : <table className="admin-table"><tbody>
                      {['trader', 'pro', 'max', 'elite'].filter((k) => d.subscriptions.by_plan[k]).map((k) => (
                        <tr key={k}><td style={{ textTransform: 'capitalize' }}>{k}</td>
                          <td className="admin-right">{fmt(d.subscriptions.by_plan[k])}</td></tr>
                      ))}
                    </tbody></table>}
              </div>
            </section>

            <section className="card">
              <div className="card-head"><div><h2 className="card-title">Transactions</h2></div></div>
              <div className="card-body">
                <table className="admin-table"><tbody>
                  {['success', 'pending', 'declined'].map((k) => (
                    <tr key={k}><td style={{ textTransform: 'capitalize' }}>{k}</td>
                      <td className="admin-right">{fmt(d.transactions[k] || 0)}</td></tr>
                  ))}
                </tbody></table>
              </div>
            </section>
          </div>

          <section className="card">
            <div className="card-head"><div><h2 className="card-title">System health</h2>
              <p className="card-sub">Time since each background fetcher last <strong>pulled</strong> (its heartbeat) — green fresh, amber stale, red not running.</p></div></div>
            <div className="card-body">
              {!h ? <p className="muted">Checking…</p> : (
                <div className="health-grid">
                  {h.sources.map((s) => {
                    // null means the module never said — not the same as "no".
                    const state = s.running === false ? 'down'
                      : s.running === null ? 'unknown'
                      : s.fresh ? 'ok' : 'stale'
                    return (
                      <div key={s.source} className={'health-tile health-tile--' + state}
                        title={s.error || (s.last ? `Last pull: ${new Date(s.last).toLocaleString()}` : 'No pull yet')}>
                        <span className="health-dot" />
                        <span className="health-name">{s.source}</span>
                        <span className="health-age">
                          {state === 'down' ? 'not running'
                            : state === 'unknown' ? 'no heartbeat'
                            : s.age_seconds == null ? 'no pull' : ageStr(s.age_seconds)}
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </section>
        </>
      )}
    </AdminLayout>
  )
}

function Kpi({ label, value, sub, accent }) {
  return (
    <div className={'kpi' + (accent ? ' kpi--accent' : '')}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {sub && <span className="kpi-sub">{sub}</span>}
    </div>
  )
}

function ageStr(s) {
  if (s < 90) return `${s}s ago`
  if (s < 5400) return `${Math.round(s / 60)}m ago`
  return `${Math.round(s / 3600)}h ago`
}
