import { useEffect, useState } from 'react'
import AdminLayout from '../../components/AdminLayout.jsx'
import * as api from '../../services/api.js'

export default function AdminAudit() {
  const [log, setLog] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { api.adminAudit().then((r) => setLog(r.log)).catch((e) => setErr(e.message)) }, [])

  return (
    <AdminLayout title="Audit log">
      {err && <div className="alert alert--danger">{err}</div>}
      <section className="card">
        <div className="card-body" style={{ paddingTop: 12 }}>
          {!log ? <p className="muted">Loading…</p> : log.length === 0 ? <p className="muted">No admin actions yet.</p> : (
            <div className="admin-table-wrap">
              <table className="admin-table admin-table--rows">
                <thead><tr><th>When</th><th>Admin</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead>
                <tbody>
                  {log.map((r, i) => (
                    <tr key={i}>
                      <td className="muted" style={{ whiteSpace: 'nowrap', fontSize: 12 }}>{new Date(r.created_at).toLocaleString()}</td>
                      <td>{r.admin_email}</td>
                      <td><span className="pill pill--muted">{r.action}</span></td>
                      <td className="muted">{r.target_type}{r.target_id ? ` · ${String(r.target_id).slice(0, 8)}` : ''}</td>
                      <td className="muted" style={{ fontSize: 12 }}>{r.meta && Object.keys(r.meta).length ? JSON.stringify(r.meta) : '—'}</td>
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
