import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { KeyRound, Plus, Trash2, Copy, Check, ShieldCheck, Code2, Sparkles,
         RefreshCw, Eye, EyeOff, SlidersHorizontal } from 'lucide-react'
import DashboardLayout from '../components/DashboardLayout.jsx'
import AnalysisModelPicker from '../components/AnalysisModelPicker.jsx'
import { getThemePref, setThemePref } from '../services/theme.js'
import { getDevMode, setDevMode } from '../services/devmode.js'
import { useBilling } from '../services/billing.js'
import * as api from '../services/api.js'
import { useCapabilities } from '../services/capabilities.js'

export default function Settings() {
  return (
    <DashboardLayout title="Settings">
      <div className="settings-stack settings-stack--split">
        <Appearance />
        <AnalysisModelPicker />
        <InstanceSettings />
        <DeveloperMode />
        <ChangePassword />
        <ApiKeys />
      </div>
    </DashboardLayout>
  )
}

function Appearance() {
  const [pref, setPref] = useState(getThemePref())
  const choose = (p) => { setPref(p); setThemePref(p) }
  const opts = [
    { id: 'system', label: 'System' },
    { id: 'light', label: 'Light' },
    { id: 'dark', label: 'Dark' },
  ]
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2 className="card-title">Appearance</h2>
          <p className="card-sub">Choose the app theme. System follows your device automatically.</p>
        </div>
      </div>
      <div className="card-body">
        <div className="pill-row">
          {opts.map((o) => (
            <button
              key={o.id}
              type="button"
              className={'pill-opt' + (pref === o.id ? ' pill-opt--on' : '')}
              onClick={() => choose(o.id)}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>
    </section>
  )
}

// ── how this instance behaves ──────────────────────────────────────────────────
// Two settings that used to live in the admin console. They are not about
// managing other people — they are about how this box runs — so where the
// operator IS the user, they belong here. The rest of the console (users,
// plans, credits, who may register) has nothing to manage on a single-user
// instance and is gone.
function InstanceSettings() {
  const caps = useCapabilities()
  const [d, setD] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [err, setErr] = useState(null)
  const [win, setWin] = useState('')
  const [share, setShare] = useState(true)
  const [hours, setHours] = useState('')
  const [name, setName] = useState('')

  const selfRun = caps && caps.admin === false && caps.edition === 'community'
  const load = () => api.instanceSettings().then((r) => {
    setD(r)
    setWin(String(r.analysis?.window_seconds ?? 60))
    setShare(!!r.analysis?.enabled)
    setHours((r.watch_list?.schedule_utc || []).join(', '))
    setName(r.app_name || '')
  }).catch((e) => setErr(e.message))
  useEffect(() => { if (selfRun) load() }, [selfRun])

  if (!selfRun) return null

  async function save() {
    setBusy(true); setErr(null); setMsg(null)
    try {
      const r = await api.saveInstanceSettings({
        app_name: name,
        analysis_window_seconds: Number(win) || 0,
        analysis_sharing: share,
        watch_list_hours: hours,
      })
      setD(r); setMsg('Saved.')
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  return (
    <section className="card">
      <div className="card-head">
        <SlidersHorizontal size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">This instance</h2>
          <p className="card-sub">How your copy runs — repeat analyses, and when the watch list is built.</p>
        </div>
      </div>
      <div className="card-body">
        {err && <div className="alert alert--danger">{err}</div>}
        {msg && <div className="alert alert--ok"><Check size={14} strokeWidth={2} /> {msg}</div>}

        {/* The plan called this "their own branding, free". It was writable
            only through a console that does not exist here. */}
        <label className="field">
          <span className="field-label">App name</span>
          <input className="input" value={name} placeholder="EntryStation"
                 onChange={(e) => setName(e.target.value)} />
        </label>
        <p className="card-sub">Yours to name — it appears in the sidebar and on emails.</p>

        <label className="field" style={{ marginTop: 16 }}>
          <span className="field-label">Repeat analyses within (seconds)</span>
          <input className="input" value={win} onChange={(e) => setWin(e.target.value)} />
        </label>
        <p className="card-sub">
          Ask for the same instrument and style twice inside this window and it is analysed once —
          the second caller waits for the first run, or takes its answer. That is your own AI spend
          it is saving. 0 switches it off.
        </p>

        <label className="avail" style={{ marginTop: 10 }}>
          <input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} />
          <span>Share repeat answers</span>
        </label>

        <label className="field" style={{ marginTop: 16 }}>
          <span className="field-label">Watch list built at (UTC)</span>
          <input className="input" value={hours} placeholder="00:00, 06:00"
                 onChange={(e) => setHours(e.target.value)} />
        </label>
        <p className="card-sub">
          {d?.watch_list?.next_run_utc
            ? `Next build ${d.watch_list.next_run_utc}`
            : 'Each time becomes its own stored build for the day.'}
        </p>

        <div className="guide-actions">
          <button className="btn btn--primary" disabled={busy} onClick={save}>
            {busy ? 'Saving…' : 'Save'}
          </button>
          <button className="btn btn--ghost" disabled={busy}
                  onClick={async () => {
                    setErr(null); setMsg(null)
                    try { await api.runWatchListNow(); setMsg('Watch-list build started.') }
                    catch (e) { setErr(e.message) }
                  }}>
            Build the watch list now
          </button>
        </div>
      </div>
    </section>
  )
}

function DeveloperMode() {
  const billing = useBilling()
  // The API surface, gated by capability rather than by plan — a Community
  // instance has no plans and its owner still gets their own API.
  const caps = useCapabilities()
  const isElite = caps ? !!caps.guides : !!billing?.developer
  const [on, setOn] = useState(getDevMode())
  // If a non-Elite user somehow has dev mode on (e.g. after a downgrade), force it off.
  useEffect(() => {
    if (billing && !isElite && getDevMode()) { setDevMode(false); setOn(false) }
  }, [billing, isElite])
  const choose = (v) => { if (!isElite) return; setDevMode(v); setOn(v) }
  return (
    <section className="card">
      <div className="card-head">
        <Code2 size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">Developer mode</h2>
          <p className="card-sub">
            Show raw JSON in the chat's tool activity and unlock the programmatic API + API keys.
            An <strong>Elite</strong> feature. When off, tool results are shown as formatted, readable data.
          </p>
        </div>
        {!isElite && <span className="pill pill--muted">Elite only</span>}
      </div>
      <div className="card-body">
        {isElite ? (
          <div className="pill-row">
            <button type="button" className={'pill-opt' + (!on ? ' pill-opt--on' : '')} onClick={() => choose(false)}>Off</button>
            <button type="button" className={'pill-opt' + (on ? ' pill-opt--on' : '')} onClick={() => choose(true)}>On</button>
          </div>
        ) : (
          <Link className="btn btn--primary btn--sm" to="/billing">Upgrade to Elite</Link>
        )}
      </div>
    </section>
  )
}

function ChangePassword() {
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [msg, setMsg] = useState(null) // {type, text}
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setMsg(null)
    if (next.length < 8) return setMsg({ type: 'danger', text: 'New password must be at least 8 characters' })
    if (next !== confirm) return setMsg({ type: 'danger', text: 'New passwords do not match' })
    setBusy(true)
    try {
      await api.changePassword(cur, next)
      setMsg({ type: 'ok', text: 'Password updated' })
      setCur(''); setNext(''); setConfirm('')
    } catch (err) {
      setMsg({ type: 'danger', text: err.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <ShieldCheck size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">Change password</h2>
          <p className="card-sub">Update the password for your account.</p>
        </div>
      </div>

      <form className="card-body" onSubmit={submit}>
        {msg && <div className={`alert alert--${msg.type === 'ok' ? 'ok' : 'danger'}`}>{msg.text}</div>}
        <label className="field">
          <span className="field-label">Current password</span>
          <input className="input" type="password" value={cur} onChange={(e) => setCur(e.target.value)} required />
        </label>
        <label className="field">
          <span className="field-label">New password</span>
          <input className="input" type="password" value={next} onChange={(e) => setNext(e.target.value)} required />
        </label>
        <label className="field">
          <span className="field-label">Confirm new password</span>
          <input className="input" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
        </label>
        <button className="btn btn--primary" type="submit" disabled={busy}>
          {busy ? 'Saving…' : 'Update password'}
        </button>
      </form>
    </section>
  )
}

function ApiKeys() {
  const [keys, setKeys] = useState([])
  const [name, setName] = useState('')
  const [created, setCreated] = useState(null) // freshly generated key (shown once)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)

  async function refresh() {
    try {
      setKeys(await api.listKeys())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { refresh() }, [])

  async function generate(e) {
    e.preventDefault()
    setError(''); setCreated(null); setCopied(false)
    if (!name.trim()) return setError('Give the key a name')
    setBusy(true)
    try {
      const res = await api.createKey(name.trim())
      setCreated(res)
      setName('')
      refresh()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function revoke(id) {
    if (!confirm('Revoke this API key? Applications using it will stop working.')) return
    try {
      await api.revokeKey(id)
      refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  function copy() {
    navigator.clipboard.writeText(created.key)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <section className="card">
      <div className="card-head">
        <KeyRound size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">API keys</h2>
          <p className="card-sub">Generate keys to access the trading API. Keep them secret.</p>
        </div>
      </div>

      <div className="card-body">
        {error && <div className="alert alert--danger">{error}</div>}

        <form className="key-create" onSubmit={generate}>
          <input
            className="input"
            placeholder="Key name (e.g. Trading bot)"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button className="btn btn--primary" type="submit" disabled={busy}>
            <Plus size={16} strokeWidth={2} />
            {busy ? 'Generating…' : 'Generate'}
          </button>
        </form>

        {created && (
          <div className="key-reveal">
            <div className="key-reveal-top">
              <span className="pill pill--warn">Copy now — shown once</span>
            </div>
            <div className="key-reveal-row">
              <code className="key-value">{created.key}</code>
              <button className="btn btn--ghost btn--icon" onClick={copy} title="Copy">
                {copied ? <Check size={16} /> : <Copy size={16} />}
              </button>
            </div>
          </div>
        )}

        <div className="key-list">
          {loading ? (
            <p className="muted">Loading…</p>
          ) : keys.length === 0 ? (
            <p className="muted">No API keys yet.</p>
          ) : (
            keys.map((k) => (
              <div className="key-row" key={k.id}>
                <div className="key-row-main">
                  <span className="key-name">{k.name}</span>
                  <code className="key-masked">{k.masked}</code>
                </div>
                <div className="key-row-side">
                  {k.revoked ? (
                    <span className="pill pill--muted">Revoked</span>
                  ) : (
                    <>
                      <span className="pill pill--ok">Active</span>
                      <button className="btn btn--danger btn--icon" onClick={() => revoke(k.id)} title="Revoke">
                        <Trash2 size={16} />
                      </button>
                    </>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  )
}
