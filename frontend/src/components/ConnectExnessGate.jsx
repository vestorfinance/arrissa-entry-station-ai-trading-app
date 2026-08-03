import { useEffect, useState } from 'react'
import { Briefcase, Check } from 'lucide-react'
import { useAuth } from '../context/AuthContext.jsx'
import * as api from '../services/api.js'
import { useModule } from '../services/capabilities.js'

const DEMO_ALLOWED = new Set(['davidrichchild@gmail.com', 'egracemedia@gmail.com'])

// App-wide gate: the logged-in user must (1) connect THEIR own Exness account and
// (2) choose which accounts to activate — before the app is usable. Only real,
// non-archived accounts are offered (demo only for the exempt owner).
export default function ConnectExnessGate() {
  const { user } = useAuth()
  // The whole gate is about ONE broker. Without that module there is nothing to
  // connect, nothing to select, and no endpoint to ask.
  const hasExness = useModule('exness')
  const allowDemo = user?.email && DEMO_ALLOWED.has(user.email)
  const [state, setState] = useState('loading')   // loading | prompt | select | done
  const [exnessEmail, setExnessEmail] = useState('')
  const [exnessPassword, setExnessPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [accounts, setAccounts] = useState([])     // eligible accounts to choose from
  const [checked, setChecked] = useState([])       // account numbers to activate

  const eligible = (list) => (list || []).filter(
    (a) => !a.is_archived && a.platform === 'mt5' && (a.is_real || allowDemo))

  async function loadAccountsForSelect() {
    const r = await api.getExnessAccounts()
    const elig = eligible(r.accounts)
    setAccounts(elig)
    const already = r.selected || []
    setChecked(already.length ? already.filter((n) => elig.some((a) => a.account_number === n))
      : elig.map((a) => a.account_number))
    return { elig, selected: already }
  }

  useEffect(() => {
    if (!hasExness) { setState('done'); return }
    api.exnessConnection()
      .then(async (c) => {
        if (!c.connected) {
          setState('prompt')
          if (user?.email) setExnessEmail(user.email)
          return
        }
        // connected — but they must have chosen accounts to activate
        const { selected } = await loadAccountsForSelect()
        setState(selected.length ? 'done' : 'select')
      })
      .catch(() => setState('done'))
  }, [user])

  async function connect(e) {
    e.preventDefault(); setError(''); setBusy(true)
    try {
      await api.exnessConnect(exnessEmail.trim(), exnessPassword)
      await loadAccountsForSelect()
      setState('select')
    } catch (err) {
      setError(err.message || 'Could not connect.')
    } finally {
      setBusy(false)
    }
  }

  function toggle(num) {
    setChecked((c) => (c.includes(num) ? c.filter((n) => n !== num) : [...c, num]))
  }

  async function saveSelection(e) {
    e.preventDefault(); setError(''); setBusy(true)
    try {
      await api.setExnessSelection(checked, false)
      setState('done')
    } catch (err) {
      setError(err.message || 'Could not save your selection.')
    } finally {
      setBusy(false)
    }
  }

  if (state === 'loading' || state === 'done') return null

  return (
    <div className="modal-overlay" style={{ zIndex: 90 }}>
      {state === 'prompt' ? (
        <form className="modal" style={{ maxWidth: 420 }} onSubmit={connect}>
          <div className="modal-head">
            <Briefcase size={18} strokeWidth={1.9} />
            <span className="modal-title">Connect your Exness account</span>
          </div>
          <p className="card-sub" style={{ marginBottom: 14 }}>
            Your trading account is personal to you. Connect the Exness account you opened through us to continue.
          </p>
          {error && <div className="alert alert--danger" style={{ marginBottom: 12 }}>{error}</div>}
          <div className="auth-form">
            <input className="auth-input" type="email" autoFocus placeholder="Exness account email"
                   value={exnessEmail} onChange={(e) => setExnessEmail(e.target.value)} />
            <input className="auth-input" type="password" placeholder="Your Exness password"
                   value={exnessPassword} onChange={(e) => setExnessPassword(e.target.value)} />
            <p className="auth-note">Used once to securely connect — your password is never stored.</p>
            <button className="auth-continue" type="submit" disabled={busy || !exnessEmail || !exnessPassword}>
              {busy ? 'Connecting…' : 'Connect account'}
            </button>
          </div>
        </form>
      ) : (
        <form className="modal" style={{ maxWidth: 460 }} onSubmit={saveSelection}>
          <div className="modal-head">
            <Check size={18} strokeWidth={1.9} />
            <span className="modal-title">Choose accounts to activate</span>
          </div>
          <p className="card-sub" style={{ marginBottom: 12 }}>
            Pick the account(s) this software may trade on. You can change this later in Settings.
          </p>
          {error && <div className="alert alert--danger" style={{ marginBottom: 12 }}>{error}</div>}
          {accounts.length === 0 ? (
            <p className="muted" style={{ marginBottom: 12 }}>No tradable (real, active) accounts found on this connection.</p>
          ) : (
            <div className="key-list" style={{ marginBottom: 14 }}>
              {accounts.map((a) => {
                const on = checked.includes(a.account_number)
                return (
                  <label className={`key-row acct-row ${on ? 'acct-row--on' : ''}`} key={a.account_number}>
                    <input type="checkbox" checked={on} onChange={() => toggle(a.account_number)} />
                    <div className="key-row-main">
                      <span className="key-name">{a.account_number} · {a.account_type}</span>
                      <span className="key-masked">{a.currency}{a.is_real ? '' : ' · demo'}</span>
                    </div>
                  </label>
                )
              })}
            </div>
          )}
          <button className="auth-continue" type="submit" disabled={busy || checked.length === 0}>
            {busy ? 'Saving…' : 'Activate & continue'}
          </button>
        </form>
      )}
    </div>
  )
}
