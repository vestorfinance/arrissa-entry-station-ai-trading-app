import { useEffect, useState } from 'react'
import { NavLink, useNavigate, Navigate } from 'react-router-dom'
import { LayoutDashboard, Users, Receipt, Settings, ScrollText, ArrowLeft, LogOut } from 'lucide-react'
import { useAuth } from '../context/AuthContext.jsx'
import { useAppName } from '../services/appConfig.js'
import * as api from '../services/api.js'

// Owner-only admin shell with a vertical sidebar. Server enforces every /api/admin
// route; this gate is convenience — non-admins are redirected to the app.
export default function AdminLayout({ title, children }) {
  const { logout } = useAuth()
  const navigate = useNavigate()
  const appName = useAppName()
  const [admin, setAdmin] = useState(null)   // null=checking, true/false
  useEffect(() => { api.me().then((m) => setAdmin(!!m.admin)).catch(() => setAdmin(false)) }, [])

  if (admin === false) return <Navigate to="/dashboard" replace />

  const tabs = [
    { to: '/admin', label: 'Overview', Icon: LayoutDashboard, end: true },
    { to: '/admin/users', label: 'Users', Icon: Users },
    { to: '/admin/transactions', label: 'Transactions', Icon: Receipt },
    { to: '/admin/settings', label: 'Settings', Icon: Settings },
    { to: '/admin/audit', label: 'Audit', Icon: ScrollText },
  ]
  return (
    <div className="admin-shell">
      <aside className="admin-side">
        <div className="admin-side-brand">{appName} <span className="admin-brand-tag">Admin</span></div>
        <nav className="admin-side-nav">
          {tabs.map((t) => (
            <NavLink key={t.to} to={t.to} end={t.end}
              className={({ isActive }) => 'admin-navlink' + (isActive ? ' admin-navlink--on' : '')}>
              <t.Icon size={17} strokeWidth={1.85} /><span>{t.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="admin-side-foot">
          <button className="admin-navlink" onClick={() => navigate('/dashboard')}><ArrowLeft size={17} strokeWidth={1.85} /><span>Back to app</span></button>
          <button className="admin-navlink" onClick={() => { logout(); navigate('/login', { replace: true }) }}><LogOut size={17} strokeWidth={1.85} /><span>Logout</span></button>
        </div>
      </aside>
      <main className="admin-main">
        <div className="admin-content">
          {admin === null ? <p className="muted">Loading…</p> : (
            <>
              {title && <h1 className="admin-title">{title}</h1>}
              {children}
            </>
          )}
        </div>
      </main>
    </div>
  )
}
