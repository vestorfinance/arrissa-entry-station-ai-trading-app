import { useEffect, useState, useCallback } from 'react'
import { X } from 'lucide-react'
import AdminLayout from '../../components/AdminLayout.jsx'
import * as api from '../../services/api.js'

const fmt = (n) => Number(n || 0).toLocaleString()
const PLANS = ['trader', 'pro', 'max', 'elite']

export default function AdminUsers() {
  const [q, setQ] = useState('')
  const [plan, setPlan] = useState('')
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [openId, setOpenId] = useState(null)

  const load = useCallback(() => {
    setErr(null)
    api.adminUsers({ q, plan, limit: 100 }).then(setData).catch((e) => setErr(e.message))
  }, [q, plan])
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t) }, [load])

  return (
    <AdminLayout title="Users">
      {err && <div className="alert alert--danger">{err}</div>}
      <div className="admin-toolbar">
        <input className="input" placeholder="Search email or name…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="pill-row">
          <button className={'pill-opt' + (plan === '' ? ' pill-opt--on' : '')} onClick={() => setPlan('')}>All</button>
          {PLANS.map((p) => (
            <button key={p} className={'pill-opt' + (plan === p ? ' pill-opt--on' : '')} onClick={() => setPlan(p)}
              style={{ textTransform: 'capitalize' }}>{p}</button>
          ))}
        </div>
      </div>

      <section className="card">
        <div className="card-body" style={{ paddingTop: 12 }}>
          {!data ? <p className="muted">Loading…</p> : data.users.length === 0 ? <p className="muted">No users.</p> : (
            <div className="admin-table-wrap">
              <table className="admin-table admin-table--rows">
                <thead><tr><th>Email</th><th>Name</th><th>Plan</th><th className="admin-right">Credits</th><th>Status</th><th>Joined</th></tr></thead>
                <tbody>
                  {data.users.map((u) => (
                    <tr key={u.id} className="admin-row" onClick={() => setOpenId(u.id)}>
                      <td>{u.email}</td>
                      <td>{[u.first_name, u.last_name].filter(Boolean).join(' ') || '—'}</td>
                      <td>{u.plan ? <span className="pill pill--ok" style={{ textTransform: 'capitalize' }}>{u.plan}</span> : <span className="pill pill--muted">none</span>}</td>
                      <td className="admin-right">{fmt(u.credits)}</td>
                      <td>{u.status === 'suspended' ? <span className="pill pill--warn">suspended</span> : <span className="muted">active</span>}</td>
                      <td className="muted">{new Date(u.created_at).toLocaleDateString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {data && <p className="muted" style={{ marginTop: 10 }}>{fmt(data.total)} users</p>}
        </div>
      </section>

      {openId && <UserDrawer id={openId} onClose={() => setOpenId(null)} onChanged={load} />}
    </AdminLayout>
  )
}

function UserDrawer({ id, onClose, onChanged }) {
  const [d, setD] = useState(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState(null)
  const reload = useCallback(() => api.adminUser(id).then(setD).catch((e) => setMsg({ t: 'danger', x: e.message })), [id])
  useEffect(() => { reload() }, [reload])

  async function act(tag, fn) {
    setBusy(tag); setMsg(null)
    try { await fn(); await reload(); onChanged?.(); setMsg({ t: 'ok', x: 'Done' }) }
    catch (e) { setMsg({ t: 'danger', x: e.message }) } finally { setBusy('') }
  }
  function adjustCredits() {
    const v = prompt('Adjust credits (e.g. 25000 to grant, -5000 to deduct):')
    if (!v) return
    const amount = parseInt(v, 10)
    if (!amount) return
    act('credits', () => api.adminAdjustCredits(id, amount, 'admin adjustment'))
  }

  const u = d?.user, b = d?.billing
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="admin-drawer" onClick={(e) => e.stopPropagation()}>
        <button className="modal-x" onClick={onClose} aria-label="Close" style={{ fontSize: 20, lineHeight: 1 }}>×</button>
        {!d ? <p className="muted">Loading…</p> : (
          <>
            <h2 className="admin-drawer-title">{u.email}</h2>
            <p className="muted" style={{ marginTop: -4 }}>
              {[u.first_name, u.last_name].filter(Boolean).join(' ')} · joined {new Date(u.created_at).toLocaleDateString()}
              {u.status === 'suspended' && <> · <span className="pill pill--warn">suspended</span></>}
            </p>
            {msg && <div className={`alert alert--${msg.t === 'ok' ? 'ok' : 'danger'}`}>{msg.x}</div>}

            <div className="kpi-grid kpi-grid--sm">
              <Kpi label="Plan" value={b.plan_name || 'None'} />
              <Kpi label="Credits" value={fmt(b.credits)} />
              <Kpi label="Chats" value={fmt(d.usage.chats)} />
              <Kpi label="Analyses" value={fmt(d.usage.runs)} sub={`$${d.usage.analysis_cost_usd.toFixed(4)}`} />
            </div>

            <div className="admin-actions">
              <button className="btn btn--sm" disabled={!!busy} onClick={adjustCredits}>Adjust credits</button>
              <div className="admin-plan-set">
                <span className="field-label">Comp plan:</span>
                {['trader', 'pro', 'max', 'elite'].map((p) => (
                  <button key={p} className="btn btn--sm btn--ghost" disabled={!!busy}
                    style={{ textTransform: 'capitalize' }}
                    onClick={() => act('plan', () => api.adminSetPlan(id, p))}>{p}</button>
                ))}
                <button className="btn btn--sm btn--ghost" disabled={!!busy} onClick={() => act('plan', () => api.adminSetPlan(id, ''))}>Cancel</button>
              </div>
              <button className={'btn btn--sm ' + (u.status === 'suspended' ? '' : 'btn--danger')} disabled={!!busy}
                onClick={() => act('suspend', () => api.adminSuspend(id, u.status !== 'suspended'))}>
                {u.status === 'suspended' ? 'Unsuspend' : 'Suspend'}
              </button>
            </div>

            {d.exness && <p className="muted" style={{ fontSize: 12 }}>Exness: {d.exness.exness_email} · connected {new Date(d.exness.connected_at).toLocaleDateString()}</p>}

            <h3 className="admin-sub">Recent credit ledger</h3>
            <div className="admin-table-wrap" style={{ maxHeight: 260 }}>
              <table className="admin-table admin-table--rows">
                <tbody>
                  {d.ledger.length === 0 ? <tr><td className="muted">No entries.</td></tr> : d.ledger.map((l, i) => (
                    <tr key={i}>
                      <td className={l.delta >= 0 ? 'pnl-up' : 'pnl-down'}>{l.delta >= 0 ? '+' : ''}{fmt(l.delta)}</td>
                      <td>{l.reason}</td>
                      <td className="muted" style={{ fontSize: 11 }}>{new Date(l.created_at).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function Kpi({ label, value, sub }) {
  return (
    <div className="kpi">
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {sub && <span className="kpi-sub">{sub}</span>}
    </div>
  )
}
