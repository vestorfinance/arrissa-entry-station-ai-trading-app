import { useEffect, useState } from 'react'
import AdminLayout from '../../components/AdminLayout.jsx'
import * as api from '../../services/api.js'

// Every payment, both doors: modules bought from the store, and plans or credit
// top-ups bought inside the app.
//
// The column that earns the page is UNDELIVERED — money taken with nothing
// handed over. A list of amounts looks healthy whatever happened afterwards, and
// the failure worth finding is a buyer who paid and got no licence. So it is
// counted at the top and called out on the row, rather than left to be inferred
// from a blank cell.

const FILTERS = [
  { key: '', label: 'Everything' },
  { key: 'module', label: 'Modules' },
  { key: 'subscription', label: 'Plans' },
  { key: 'topup', label: 'Credits' },
]

const money = (n) => (n || n === 0 ? `R ${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—')
const when = (t) => (t ? new Date(t).toLocaleString() : '—')

function Kpi({ label, value, sub, accent }) {
  return (
    <div className={'kpi' + (accent ? ' kpi--accent' : '')}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {sub && <span className="kpi-sub">{sub}</span>}
    </div>
  )
}

function StatusPill({ row }) {
  const paid = row.status === 'paid' || row.status === 'success'
  if (paid && !row.delivered) return <span className="pill pill--warn">paid, not delivered</span>
  if (paid) return <span className="pill pill--ok">{row.status}</span>
  if (row.status === 'pending') return <span className="pill pill--muted">pending</span>
  return <span className="pill pill--warn">{row.status}</span>
}

export default function AdminTransactions() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [kind, setKind] = useState('')

  useEffect(() => {
    let dead = false
    setData(null)
    api.adminTransactions(kind)
      .then((r) => { if (!dead) { setData(r); setErr(null) } })
      .catch((e) => { if (!dead) setErr(e.message) })
    return () => { dead = true }
  }, [kind])

  const t = data?.totals

  return (
    <AdminLayout title="Transactions">
      {err && <div className="alert alert--danger">{err}</div>}

      {t && (
        <div className="kpi-grid">
          <Kpi label="Paid" value={t.paid} sub={`${t.count} in total`} />
          <Kpi label="Taken" value={money(t.zar)} accent
               sub={t.zar_test ? `${money(t.zar_test)} of it in test mode` : undefined} />
          <Kpi label="Pending" value={t.pending} />
          <Kpi label="Paid, not delivered" value={t.undelivered}
               sub={t.undelivered ? 'someone paid and got nothing — open these' : 'nothing owed'} />
        </div>
      )}

      <div className="admin-filters" style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        {FILTERS.map((f) => (
          <button key={f.key}
                  className={'btn btn--sm ' + (kind === f.key ? 'btn--primary' : 'btn--ghost')}
                  onClick={() => setKind(f.key)}>{f.label}</button>
        ))}
      </div>

      <section className="card">
        <div className="card-body" style={{ paddingTop: 12 }}>
          {!data ? <p className="muted">Loading…</p>
            : data.transactions.length === 0 ? <p className="muted">No payments yet.</p> : (
            <div className="admin-table-wrap">
              <table className="admin-table admin-table--rows">
                <thead>
                  <tr>
                    <th>When</th><th>Kind</th><th>What</th><th>Buyer</th>
                    <th>Instance</th><th>Amount</th><th>Status</th><th>Reference</th>
                  </tr>
                </thead>
                <tbody>
                  {data.transactions.map((r) => (
                    <tr key={r.reference}>
                      <td className="muted" style={{ whiteSpace: 'nowrap', fontSize: 12 }}>
                        {when(r.paid_at || r.created_at)}
                      </td>
                      <td><span className="pill pill--muted">{r.kind}</span></td>
                      <td>{r.what}</td>
                      <td className="muted" style={{ fontSize: 12 }}>{r.email}</td>
                      <td className="muted" style={{ fontSize: 12 }}>{r.instance || '—'}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        {money(r.amount_zar)}
                        {/* Test money is not money. A live total that quietly
                            included test-mode payments would be the one number
                            on this page nobody could trust. */}
                        {r.mode === 'test' && <span className="pill pill--warn" style={{ marginLeft: 6 }}>test</span>}
                      </td>
                      <td><StatusPill row={r} /></td>
                      <td className="muted" style={{ fontSize: 11 }}>
                        {r.reference}
                        {r.kind === 'module' && r.detail ? <><br />{r.detail}</> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>
    </AdminLayout>
  )
}
