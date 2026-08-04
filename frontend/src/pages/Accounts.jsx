import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { Wallet, Plug, Plus, Trash2, ShieldCheck, AlertTriangle, CheckCircle2, ExternalLink} from 'lucide-react'
import DashboardLayout from '../components/DashboardLayout.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import * as api from '../services/api.js'
import { backdrop } from '../services/backdrop.js'
import BrokerLogo, { useBrokers } from '../components/BrokerLogo.jsx'

// Where somebody with no Exness account goes to open one. Named here rather
// than inline so the two places that offer it cannot drift apart.
const EXNESS_SIGNUP = 'https://one.exnessonelink.com/a/l5kqp6wwav'

const DEMO_ALLOWED = new Set(['davidrichchild@gmail.com', 'egracemedia@gmail.com'])

export default function Accounts() {
  const { user } = useAuth()
  const allowDemo = user?.email && DEMO_ALLOWED.has(user.email)
  const [data, setData] = useState(null)
  const [msg, setMsg] = useState(null)
  const [risk, setRisk] = useState(null)

  const load = useCallback(async () => {
    try {
      setData(await api.getAllAccounts())
    } catch (err) {
      setMsg({ type: 'danger', text: err.message })
    }
  }, [])
  useEffect(() => {
    load()
    const t = setInterval(load, 12000)   // new accounts / balances refresh in
    return () => clearInterval(t)
  }, [load])
  useEffect(() => { api.getRiskSettings().then(setRisk).catch(() => setRisk(null)) }, [])

  // Nudge: they have accounts but haven't configured any risk parameters yet.
  const exAccts = (data?.exness?.accounts || []).filter((a) => !a.is_archived && a.platform === 'mt5')
  const tlAccts = (data?.tradelocker?.connections || []).flatMap((c) => c.accounts || [])
  const hasAccounts = exAccts.length > 0 || tlAccts.length > 0
  const hasRisk = !!(risk && (risk.profile || (risk.accounts || []).length))
  const showNudge = hasAccounts && risk !== null && !hasRisk

  const active = data?.active || { broker: 'exness', account: null }
  const isActive = (broker, acc) =>
    active.broker === broker && String(active.account) === String(acc)

  async function makeActive(broker, account) {
    setMsg(null)
    try {
      await api.setActiveAccountUnified(broker, account)
      setMsg({ type: 'ok', text: `Active account switched to ${broker} · ${account}` })
      load()
    } catch (err) {
      setMsg({ type: 'danger', text: err.message })
    }
  }

  return (
    <DashboardLayout title="Accounts">
      <div className="settings-stack">
        <section className="card">
          <div className="card-head">
            <Wallet size={18} strokeWidth={1.75} />
            <div>
              <h2 className="card-title">Account management</h2>
              <p className="card-sub">
                All your trading accounts across brokers. The <strong>active</strong> account
                decides which broker protocol the app uses everywhere — trading, charts,
                market data and live streaming.
              </p>
            </div>
          </div>
          <div className="card-body">
            {msg && <div className={`alert alert--${msg.type === 'ok' ? 'ok' : 'danger'}`}>{msg.text}</div>}
            <div className="conn-status">
              <span className="pill pill--ok">Active</span>
              {active.account ? (
                <span className="muted">
                  <BrokerBadge broker={active.broker} /> · {active.account}
                </span>
              ) : (
                <span className="muted">No active account yet — connect one below.</span>
              )}
            </div>
          </div>
        </section>

        {showNudge && (
          <div className="risk-nudge">
            <div className="risk-nudge-main">
              {/* The icon belongs ON the heading's line, not centred against the
                  whole two-line block — it labels the heading, not the card. */}
              <div className="risk-nudge-title">
                <span className="risk-nudge-icon"><ShieldCheck size={18} strokeWidth={1.9} /></span>
                Set your risk parameters
              </div>
              <div className="risk-nudge-sub">
                You've added accounts — now set how much to risk per trade, your reward:risk, daily/weekly/monthly
                drawdown limits and trading hours. Until you do, trades default to 2% risk.
              </div>
            </div>
            <Link className="btn btn--primary" to="/risk-settings">Set risk parameters</Link>
          </div>
        )}

        {/* Exness is a module. The server lists the brokers it actually has, so
            an instance without it shows no Exness card rather than a card that
            can only ever fail. */}
        {(data?.brokers || ['exness']).includes('exness') && (
          <ExnessSection
            data={data} allowDemo={allowDemo} isActive={isActive}
            onActive={makeActive} onChange={load} setMsg={setMsg}
          />
        )}

        {/* Same rule as Exness: TradeLocker is a module, so its card appears
            only when the server says that broker is actually installed. */}
        {(data?.brokers || ['tradelocker']).includes('tradelocker') && (
          <TradeLockerSection
            data={data} isActive={isActive}
            onActive={makeActive} onChange={load} setMsg={setMsg}
          />
        )}
      </div>
    </DashboardLayout>
  )
}

/**
 * "This account is available to the app."
 *
 * Distinct from ACTIVE, and the distinction is the point: available is "you may
 * use this one", active is "use this one now". Someone with five accounts at a
 * broker should be able to keep four of them out of reach of software that
 * places trades.
 *
 * `available === null` means never chosen — everything is ticked, because a user
 * who has not opened this page has not opted out of anything.
 */
function AvailableToggle({ account, available, allAccounts, onChange }) {
  const list = available === null || available === undefined ? null : available.map(String)
  const on = list === null || list.includes(String(account))
  const [busy, setBusy] = useState(false)

  async function toggle() {
    setBusy(true)
    try {
      // From "never chosen" everything is available, so the FIRST untick means
      // "all of them except this one" — the user is removing one account, not
      // selecting one. Starting from an empty list would silently switch off
      // every other account they own.
      const base = list === null ? allAccounts.map(String) : list
      const next = on ? base.filter((a) => a !== String(account))
                      : [...base, String(account)]
      await onChange(next)
    } finally { setBusy(false) }
  }

  return (
    <label className={'avail' + (on ? ' avail--on' : '')} title={
      on ? 'Available to this app — untick to keep it out of reach'
         : 'Not available to this app'}>
      <input type="checkbox" checked={on} disabled={busy} onChange={toggle} />
      <span>Available</span>
    </label>
  )
}

function BrokerBadge({ broker }) {
  // The name comes from the broker module, not from a map in here — core has no
  // business knowing that "tradelocker" is spelled "TradeLocker".
  const b = useBrokers().find((x) => x.id === broker)
  return (
    <span className={`pill pill--broker pill--${broker}`}>
      <BrokerLogo broker={broker} size={16} />
      {b?.name || broker}
    </span>
  )
}

// ── Exness ──────────────────────────────────────────────────────────────────────
function ExnessSection({ data, allowDemo, isActive, onActive, onChange, setMsg }) {
  const { user } = useAuth()
  const ex = data?.exness
  const [showConnect, setShowConnect] = useState(false)
  const [cEmail, setCEmail] = useState('')
  const [cPass, setCPass] = useState('')
  const [cErr, setCErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [showDisconnect, setShowDisconnect] = useState(false)

  const accounts = (ex?.accounts || []).filter(
    (a) => !a.is_archived && a.platform === 'mt5' && (a.is_real || allowDemo))

  function openConnect() {
    setCEmail(ex?.email || user?.email || ''); setCPass(''); setCErr(''); setShowConnect(true)
  }
  async function doConnect(e) {
    e.preventDefault(); setBusy(true); setCErr('')
    try {
      await api.exnessConnect(cEmail.trim(), cPass)
      setShowConnect(false); setCPass('')
      setMsg({ type: 'ok', text: 'Exness account connected' })
      onChange()
    } catch (err) { setCErr(err.message) } finally { setBusy(false) }
  }
  async function doDisconnect() {
    setBusy(true)
    try {
      await api.exnessDisconnect()
      setShowDisconnect(false)
      setMsg({ type: 'ok', text: 'Exness disconnected — session token deleted.' })
      onChange()
    } catch (err) { setMsg({ type: 'danger', text: err.message }) } finally { setBusy(false) }
  }

  return (
    <section className="card">
      <div className="card-head">
        <BrokerLogo broker="exness" size={26} />
        <div>
          <h2 className="card-title">Exness</h2>
          <p className="card-sub">Your Exness accounts. Password is used once to obtain a session token and is never stored.</p>
        </div>
      </div>
      <div className="card-body">
        <div className="conn-status">
          <span className={`pill ${ex?.connected ? 'pill--ok' : 'pill--warn'}`}>
            {ex ? (ex.connected ? 'Connected' : 'Not connected') : 'Checking…'}
          </span>
          {ex?.email && <span className="muted">{ex.email}</span>}
          <span className="conn-actions">
            {ex?.connected ? (
              <>
                <button className="btn btn--ghost btn--sm" onClick={openConnect}>Reconnect</button>
                <button className="btn btn--danger btn--sm" onClick={() => setShowDisconnect(true)}>Disconnect</button>
              </>
            ) : (
              <>
                <button className="btn btn--primary btn--sm" onClick={openConnect}>Connect Exness</button>
                {/* Connecting needs an account to connect TO. Offered only while
                    they are not connected, because afterwards it is an advert. */}
                <a className="btn btn--ghost btn--sm" href={EXNESS_SIGNUP}
                   target="_blank" rel="noreferrer">
                  Open an Exness account <ExternalLink size={12} />
                </a>
              </>
            )}
          </span>
        </div>

        {accounts.length > 0 && (
          <div className="key-list">
            {accounts.map((a) => (
              <div className={`key-row acct-row ${isActive('exness', a.account_number) ? 'acct-row--on' : ''}`}
                   key={a.account_number}>
                <BrokerLogo broker="exness" size={30} />
                <div className="key-row-main">
                  <span className="key-name">{a.account_number} · {a.account_type}</span>
                  <span className="key-masked">{a.server} · {a.currency} · {a.balance}</span>
                </div>
                <span className="acct-row-side">
                  <AvailableToggle account={a.account_number} available={ex?.available}
                    allAccounts={accounts.map((x) => x.account_number)}
                    onChange={async (next) => {
                      await api.setAvailableAccounts('exness', next); onChange()
                    }} />
                  {isActive('exness', a.account_number) ? (
                    <span className="pill pill--ok"><CheckCircle2 size={13} strokeWidth={2} /> Active</span>
                  ) : (
                    <button className="btn btn--ghost btn--sm"
                            onClick={() => onActive('exness', a.account_number)}>Set active</button>
                  )}
                  <span className={`pill ${a.is_real ? 'pill--warn' : ''}`}>{a.is_real ? 'Real' : 'Demo'}</span>
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {showConnect && (
        <div className="modal-overlay" {...backdrop(() => setShowConnect(false))}>
          <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={doConnect}>
            <div className="modal-head"><Plug size={16} strokeWidth={1.9} /><span className="modal-title">Connect Exness account</span></div>
            <p className="card-sub" style={{ marginBottom: 12 }}>
              Your password is used <strong>once</strong> to obtain a trading session and is never stored.
            </p>
            {cErr && <div className="alert alert--danger" style={{ marginBottom: 10 }}>{cErr}</div>}
            <label className="field"><span className="field-label">Exness email</span>
              <input className="input" type="email" required value={cEmail} onChange={(e) => setCEmail(e.target.value)} /></label>
            <label className="field"><span className="field-label">Exness password</span>
              <input className="input" type="password" required autoFocus value={cPass} onChange={(e) => setCPass(e.target.value)} /></label>
            <div className="modal-actions">
              <button type="button" className="btn btn--ghost" onClick={() => setShowConnect(false)}>Cancel</button>
              <button type="submit" className="btn btn--primary" disabled={busy}>{busy ? 'Connecting…' : 'Connect'}</button>
            </div>
          </form>
        </div>
      )}
      {showDisconnect && (
        <div className="modal-overlay" {...backdrop(() => setShowDisconnect(false))}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head"><AlertTriangle className="modal-warn-icon" size={18} strokeWidth={1.75} /><span className="modal-title">Disconnect Exness?</span></div>
            <p className="modal-body">This deletes your Exness session token. Scheduled actions and live positions stop until you reconnect.</p>
            <div className="modal-actions">
              <button className="btn btn--ghost" onClick={() => setShowDisconnect(false)}>Cancel</button>
              <button className="btn btn--danger" onClick={doDisconnect} disabled={busy}>{busy ? 'Disconnecting…' : 'Disconnect'}</button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

// ── TradeLocker ─────────────────────────────────────────────────────────────────
function TradeLockerSection({ data, isActive, onActive, onChange, setMsg }) {
  const connections = data?.tradelocker?.connections || []
  const [show, setShow] = useState(false)
  const [form, setForm] = useState({ email: '', password: '', server: '', environment: 'demo' })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  function open() {
    setForm({ email: '', password: '', server: '', environment: 'demo' }); setErr(''); setShow(true)
  }
  async function doConnect(e) {
    e.preventDefault(); setBusy(true); setErr('')
    try {
      await api.tradelockerConnect(form)
      setShow(false)
      setMsg({ type: 'ok', text: 'TradeLocker account connected' })
      onChange()
    } catch (e2) { setErr(e2.message) } finally { setBusy(false) }
  }
  async function disconnect(connectionId) {
    if (!confirm('Disconnect this TradeLocker login? Its accounts will be removed.')) return
    try {
      await api.tradelockerDisconnect(connectionId)
      setMsg({ type: 'ok', text: 'TradeLocker login disconnected' })
      onChange()
    } catch (e2) { setMsg({ type: 'danger', text: e2.message }) }
  }

  return (
    <section className="card">
      <div className="card-head">
        <BrokerLogo broker="tradelocker" size={26} />
        <div>
          <h2 className="card-title">TradeLocker</h2>
          <p className="card-sub">Connect a TradeLocker login (demo or live). All accounts under it join your list. Password is never stored.</p>
        </div>
      </div>
      <div className="card-body">
        <div className="conn-status">
          <span className={`pill ${connections.length ? 'pill--ok' : 'pill--warn'}`}>
            {connections.length ? `${connections.length} login${connections.length > 1 ? 's' : ''}` : 'Not connected'}
          </span>
          <span className="conn-actions">
            <button className="btn btn--primary btn--sm" onClick={open}><Plus size={15} strokeWidth={2} /> Connect TradeLocker</button>
          </span>
        </div>

        {connections.map((c) => (
          <div key={c.connection_id} className="tl-conn">
            <div className="tl-conn-head">
              <span className="key-name">{c.email}</span>
              <span className="key-masked">{c.environment} · {c.server}</span>
              <button className="btn btn--danger btn--icon btn--sm" title="Disconnect"
                      onClick={() => disconnect(c.connection_id)}><Trash2 size={15} /></button>
            </div>
            <div className="key-list">
              {(c.accounts || []).length === 0 && <p className="muted">No accounts on this login.</p>}
              {(c.accounts || []).map((a) => (
                <div className={`key-row acct-row ${isActive('tradelocker', a.account_id) ? 'acct-row--on' : ''}`}
                     key={a.account_id}>
                  <BrokerLogo broker="tradelocker" size={30} />
                  <div className="key-row-main">
                    {/* A login can hold several accounts, and TradeLocker's own
                        `name` is a machine string (DEMO#uuid#1#1) that tells them
                        apart to nobody. The account NUMBER does, so it leads. */}
                    <span className="key-name">
                      {a.acc_num ? `Account ${a.acc_num}` : a.account_id}
                    </span>
                    <span className="key-masked">
                      {[a.account_id, a.currency].filter(Boolean).join(' · ')}
                    </span>
                  </div>
                  <span className="acct-row-side">
                    <AvailableToggle account={a.account_id} available={data?.tradelocker?.available}
                      allAccounts={connections.flatMap((c) => (c.accounts || []).map((x) => x.account_id))}
                      onChange={async (next) => {
                        await api.setAvailableAccounts('tradelocker', next); onChange()
                      }} />
                    {isActive('tradelocker', a.account_id) ? (
                      <span className="pill pill--ok"><CheckCircle2 size={13} strokeWidth={2} /> Active</span>
                    ) : (
                      <button className="btn btn--ghost btn--sm"
                              onClick={() => onActive('tradelocker', a.account_id)}>Set active</button>
                    )}
                    <span className={`pill ${a.environment === 'live' ? 'pill--warn' : ''}`}>
                      {a.environment === 'live' ? 'Live' : 'Demo'}
                    </span>
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {show && (
        <div className="modal-overlay" {...backdrop(() => setShow(false))}>
          <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={doConnect}>
            <div className="modal-head"><Plug size={16} strokeWidth={1.9} /><span className="modal-title">Connect TradeLocker account</span></div>
            <p className="card-sub" style={{ marginBottom: 12 }}>
              Enter your TradeLocker login. The password is used <strong>once</strong> to obtain a session and is never stored.
            </p>
            {err && <div className="alert alert--danger" style={{ marginBottom: 10 }}>{err}</div>}
            <div className="pill-row" style={{ marginBottom: 12 }}>
              {['demo', 'live'].map((env) => (
                <button key={env} type="button"
                        className={'pill-opt' + (form.environment === env ? ' pill-opt--on' : '')}
                        onClick={() => setForm((f) => ({ ...f, environment: env }))}>
                  {env === 'live' ? 'Live' : 'Demo'}
                </button>
              ))}
            </div>
            <label className="field"><span className="field-label">Email</span>
              <input className="input" type="email" required value={form.email}
                     onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} /></label>
            <label className="field"><span className="field-label">Password</span>
              <input className="input" type="password" required value={form.password}
                     onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} /></label>
            <label className="field"><span className="field-label">Server</span>
              <input className="input" type="text" required placeholder="e.g. OSP-DEMO or your broker's server"
                     value={form.server} onChange={(e) => setForm((f) => ({ ...f, server: e.target.value }))} /></label>
            <div className="modal-actions">
              <button type="button" className="btn btn--ghost" onClick={() => setShow(false)}>Cancel</button>
              <button type="submit" className="btn btn--primary" disabled={busy}>{busy ? 'Connecting…' : 'Connect'}</button>
            </div>
          </form>
        </div>
      )}
    </section>
  )
}
