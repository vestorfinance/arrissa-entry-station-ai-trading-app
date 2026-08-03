import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { LogOut, Menu } from 'lucide-react'
import { useAuth } from '../context/AuthContext.jsx'
import * as api from '../services/api.js'
import { useModule } from '../services/capabilities.js'

export default function Topbar({ title, titleExtra = null, onMenu }) {
  const hasExness = useModule('exness')
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [profile, setProfile] = useState(null)   // {first_name, last_name, email}

  useEffect(() => {
    api.me().then(setProfile).catch(() => {})
  }, [])

  const first = (profile?.first_name || '').trim()
  const last = (profile?.last_name || '').trim()
  const displayName = [first, last].filter(Boolean).join(' ')
    || (profile?.email || user?.email || '').split('@')[0]
  const initials = ((first[0] || '') + (last[0] || '')).toUpperCase()
    || (displayName[0] || '?').toUpperCase()

  // The account picker now lives in the chat composer + the Accounts page. We keep
  // this silent effect only to persist a DEFAULT active account the first time, so
  // trading API actions always have a target even if the user never picks one.
  useEffect(() => {
    if (!hasExness) return
    api.getExnessAccounts().then((r) => {
      if (r.active_account) return
      const selected = r.selected || []
      const pool = r.auto_connect_future || selected.length === 0
        ? r.accounts
        // `selected` is strings from JSONB; account_number is a number.
        : r.accounts.filter((a) => selected.map(String).includes(String(a.account_number)))
      const chosen = pool[0]?.account_number
      if (chosen) api.setActiveAccount(Number(chosen)).catch(() => {})
    }).catch(() => {})
  }, [hasExness])

  function onLogout() {
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <header className="topbar">
      <div className="topbar-left">
        <button className="topbar-menu" onClick={onMenu} title="Menu" aria-label="Menu">
          <Menu size={20} strokeWidth={2} />
        </button>
        <h1 className="topbar-title">{title}</h1>
        {titleExtra}
      </div>
      <div className="topbar-right">
        <span className="topbar-user" title={displayName}>
          <span className="topbar-avatar" aria-hidden="true">{initials}</span>
          <span className="topbar-user-name">{displayName}</span>
        </span>
        <button className="btn btn--ghost topbar-logout" onClick={onLogout}>
          <LogOut size={16} strokeWidth={1.75} />
          Logout
        </button>
      </div>
    </header>
  )
}
