import { useEffect, useState, useCallback } from 'react'
import { Copy, Check, RefreshCw, X, Plus, Clock } from 'lucide-react'
import AdminLayout from '../../components/AdminLayout.jsx'
import * as api from '../../services/api.js'
import { setAppNameCache } from '../../services/appConfig.js'

export default function AdminSettings() {
  const [s, setS] = useState(null)
  const [msg, setMsg] = useState(null)
  const reload = useCallback(() => api.adminSettings().then(setS).catch((e) => setMsg({ t: 'danger', x: e.message })), [])
  useEffect(() => { reload() }, [reload])
  const flash = (t, x) => setMsg({ t, x })

  return (
    <AdminLayout title="Settings">
      {msg && <div className={`alert alert--${msg.t === 'ok' ? 'ok' : 'danger'}`}>{msg.x}</div>}
      {!s ? <p className="muted">Loading…</p> : (
        <div className="settings-stack settings-stack--wide">
          <Branding s={s} reload={reload} flash={flash} />
          <AIKeys s={s} reload={reload} flash={flash} />
          <Access s={s} reload={reload} flash={flash} />
          <AnalysisApi flash={flash} />
          <Byok flash={flash} />
          <WatchList flash={flash} />
          <Paystack reload={reload} flash={flash} />
          <Email s={s} reload={reload} flash={flash} />
          <TradeLocker s={s} reload={reload} flash={flash} />
          <Admins s={s} reload={reload} flash={flash} />
        </div>
      )}
    </AdminLayout>
  )
}

function Card({ title, sub, children, right }) {
  return (
    <section className="card">
      <div className="card-head"><div><h2 className="card-title">{title}</h2>{sub && <p className="card-sub">{sub}</p>}</div>{right}</div>
      <div className="card-body">{children}</div>
    </section>
  )
}

function Branding({ s, reload, flash }) {
  const [name, setName] = useState(s.app_name || '')
  const [busy, setBusy] = useState(false)
  async function save() {
    setBusy(true)
    try { const r = await api.adminSetBranding(name.trim()); setAppNameCache(r.app_name); await reload(); flash('ok', 'App name saved') }
    catch (e) { flash('danger', e.message) } finally { setBusy(false) }
  }
  return (
    <Card title="Branding" sub="The app name shown across the product and in emails.">
      <div className="key-create">
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="EntryStation" />
        <button className="btn btn--primary" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
      </div>
    </Card>
  )
}

const AI_META = { deepseek: 'arrissa-chat · DeepSeek', openai: 'arrissa-pro · OpenAI', anthropic: 'Anthropic (optional)' }

function AIKeys({ s, reload, flash }) {
  const [keys, setKeys] = useState({})
  const [busy, setBusy] = useState('')
  async function save(p) {
    setBusy(p)
    try { await api.adminSetAIKey(p, keys[p] || ''); setKeys((k) => ({ ...k, [p]: '' })); await reload(); flash('ok', `${p} key ${keys[p] ? 'saved' : 'cleared'}`) }
    catch (e) { flash('danger', e.message) } finally { setBusy('') }
  }
  return (
    <Card title="AI provider keys" sub="The app runs on THESE keys (no bring-your-own-key). arrissa-chat → DeepSeek, arrissa-pro → OpenAI.">
      {['deepseek', 'openai', 'anthropic'].map((p) => (
        <div className="ps-env" key={p} style={{ borderTop: p === 'deepseek' ? 'none' : undefined, paddingTop: p === 'deepseek' ? 0 : undefined }}>
          <div className="ps-env-head">
            <strong>{AI_META[p]}</strong>
            {s.ai[p]?.has_key ? <span className="pill pill--ok">key set</span> : <span className="pill pill--muted">no key</span>}
          </div>
          <div className="key-create">
            <input className="input" type="password" placeholder={s.ai[p]?.has_key ? 'unchanged — blank keeps it' : 'sk-…'}
              value={keys[p] || ''} onChange={(e) => setKeys((k) => ({ ...k, [p]: e.target.value }))} />
            <button className="btn btn--sm" disabled={busy === p} onClick={() => save(p)}>{busy === p ? 'Saving…' : 'Save'}</button>
          </div>
        </div>
      ))}
    </Card>
  )
}

function Access({ s, reload, flash }) {
  const [busy, setBusy] = useState('')
  const [copied, setCopied] = useState(false)
  const link = s.invite.code ? `${window.location.origin}${s.invite.path}` : null
  const open = s.registrations_open === true

  async function rotate() {
    if (!confirm('Rotate the invite link? The current link stops working immediately.')) return
    setBusy('rotate')
    try { await api.rotateAdminInvite(); await reload(); flash('ok', 'Invite link rotated') } catch (e) { flash('danger', e.message) } finally { setBusy('') }
  }
  async function toggleReg(v) {
    setBusy('reg')
    try { await api.adminSetRegistrations(v); await reload(); flash('ok', v ? 'Public registration OPEN' : 'Registration set to invite-only') } catch (e) { flash('danger', e.message) } finally { setBusy('') }
  }
  return (
    <Card title="Access & registration" sub="Public signup is off by default — share the invite link, or open registration to everyone.">
      <div className="field-label" style={{ marginBottom: 6 }}>Registration</div>
      <div className="pill-row" style={{ marginBottom: 16 }}>
        <button className={'pill-opt' + (!open ? ' pill-opt--on' : '')} disabled={busy === 'reg'} onClick={() => toggleReg(false)}>Invite-only</button>
        <button className={'pill-opt' + (open ? ' pill-opt--on' : '')} disabled={busy === 'reg'} onClick={() => toggleReg(true)}>Open to all</button>
      </div>
      <div className="field-label" style={{ marginBottom: 6 }}>Private invite link</div>
      {link ? (
        <div className="key-reveal"><div className="key-reveal-row">
          <code className="key-value">{link}</code>
          <button className="btn btn--ghost btn--icon" onClick={() => { navigator.clipboard.writeText(link); setCopied(true); setTimeout(() => setCopied(false), 1500) }}>{copied ? <Check size={16} /> : <Copy size={16} />}</button>
        </div></div>
      ) : <p className="muted">No invite link yet.</p>}
      <button className="btn btn--sm" disabled={busy === 'rotate'} onClick={rotate} style={{ marginTop: 12 }}><RefreshCw size={15} /> {busy === 'rotate' ? 'Rotating…' : 'Rotate link'}</button>
    </Card>
  )
}

function Paystack({ reload, flash }) {
  const [cfg, setCfg] = useState(null)
  const [keys, setKeys] = useState({ test: { secret: '', public: '' }, live: { secret: '', public: '' } })
  const [busy, setBusy] = useState('')
  const load = useCallback(() => api.getAdminPaystack().then(setCfg).catch((e) => flash('danger', e.message)), [flash])
  useEffect(() => { load() }, [load])

  async function switchMode(m) { setBusy('mode'); try { await api.setAdminPaystackMode(m); await load(); await reload(); flash('ok', `Paystack: ${m} mode`) } catch (e) { flash('danger', e.message) } finally { setBusy('') } }
  async function saveKeys(m) { setBusy('k' + m); try { await api.setAdminPaystackKeys(m, keys[m].secret, keys[m].public); setKeys((k) => ({ ...k, [m]: { secret: '', public: '' } })); await load(); flash('ok', `${m} keys saved`) } catch (e) { flash('danger', e.message) } finally { setBusy('') } }
  async function sync(m) { setBusy('s' + m); try { const r = await api.syncAdminPaystackPlans(m); await load(); flash('ok', `${m}: ${r.plans.filter((p) => p.created).length} created, ${r.plans.length} total`) } catch (e) { flash('danger', e.message) } finally { setBusy('') } }

  if (!cfg) return null
  return (
    <Card title="Payments · Paystack" sub="Environment, API keys and plan sync." right={<span className={'pill ' + (cfg.mode === 'live' ? 'pill--ok' : 'pill--warn')}>{cfg.mode} mode</span>}>
      <div className="field-label" style={{ marginBottom: 6 }}>Active environment</div>
      <div className="pill-row" style={{ marginBottom: 16 }}>
        <button className={'pill-opt' + (cfg.mode === 'test' ? ' pill-opt--on' : '')} disabled={busy === 'mode'} onClick={() => switchMode('test')}>Test</button>
        <button className={'pill-opt' + (cfg.mode === 'live' ? ' pill-opt--on' : '')} disabled={busy === 'mode'} onClick={() => switchMode('live')}>Live</button>
      </div>
      {['test', 'live'].map((m) => (
        <div className="ps-env" key={m}>
          <div className="ps-env-head">
            <strong style={{ textTransform: 'capitalize' }}>{m}</strong>
            {cfg[m].has_secret ? <span className="pill pill--ok">secret set</span> : <span className="pill pill--muted">no secret</span>}
            {cfg[m].public && <span className="pill pill--muted">pk set</span>}
            <span className="ps-plan-count">{cfg.plans.filter((p) => p.mode === m).length}/8 plans</span>
          </div>
          <div className="key-create">
            <input className="input" type="password" placeholder={cfg[m].has_secret ? 'secret — blank keeps it' : `sk_${m}_…`} value={keys[m].secret} onChange={(e) => setKeys((k) => ({ ...k, [m]: { ...k[m], secret: e.target.value } }))} />
            <input className="input" placeholder={`pk_${m}_…`} value={keys[m].public} onChange={(e) => setKeys((k) => ({ ...k, [m]: { ...k[m], public: e.target.value } }))} />
            <button className="btn btn--sm" disabled={busy === 'k' + m} onClick={() => saveKeys(m)}>{busy === 'k' + m ? 'Saving…' : 'Save'}</button>
            <button className="btn btn--sm plan-cta" disabled={!cfg[m].has_secret || busy === 's' + m} onClick={() => sync(m)}>{busy === 's' + m ? 'Creating…' : 'Create plans'}</button>
          </div>
        </div>
      ))}
    </Card>
  )
}

function Email({ s, reload, flash }) {
  const [f, setF] = useState({ host: s.smtp.host || '', port: s.smtp.port || 587, user: s.smtp.user || '', mail_from: s.smtp.mail_from || '', password: '' })
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setF((o) => ({ ...o, [k]: e.target.value }))
  async function save() { setBusy(true); try { await api.adminSetSmtp({ ...f, port: Number(f.port) || 587 }); setF((o) => ({ ...o, password: '' })); await reload(); flash('ok', 'SMTP saved') } catch (e) { flash('danger', e.message) } finally { setBusy(false) } }
  return (
    <Card title="Email (SMTP)" sub="Outgoing mail — verification codes and notices." right={s.smtp.has_pass ? <span className="pill pill--ok">configured</span> : <span className="pill pill--muted">not set</span>}>
      <label className="field"><span className="field-label">Host</span><input className="input" value={f.host} onChange={set('host')} placeholder="smtp.gmail.com" /></label>
      <label className="field"><span className="field-label">Port</span><input className="input" value={f.port} onChange={set('port')} placeholder="587" /></label>
      <label className="field"><span className="field-label">User</span><input className="input" value={f.user} onChange={set('user')} placeholder="user@example.com" /></label>
      <label className="field"><span className="field-label">From address</span><input className="input" value={f.mail_from} onChange={set('mail_from')} placeholder="noreply@…" /></label>
      <label className="field"><span className="field-label">Password</span><input className="input" type="password" value={f.password} onChange={set('password')} placeholder={s.smtp.has_pass ? 'unchanged — blank keeps it' : 'app password'} /></label>
      <button className="btn btn--primary" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save SMTP'}</button>
    </Card>
  )
}

function TradeLocker({ s, reload, flash }) {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  async function save() { setBusy(true); try { await api.setAdminTradelockerKey(key.trim()); setKey(''); await reload(); flash('ok', 'TradeLocker key saved') } catch (e) { flash('danger', e.message) } finally { setBusy(false) } }
  return (
    <Card title="TradeLocker partner key" sub="App-level developer/partner API key (BrandSocket stream + higher rate limits)." right={s.tradelocker.has_key ? <span className="pill pill--ok">key set</span> : <span className="pill pill--muted">no key</span>}>
      <div className="key-create">
        <input className="input" type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder={s.tradelocker.has_key ? 'unchanged — blank keeps it' : 'developer key'} />
        <button className="btn btn--sm" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
      </div>
    </Card>
  )
}

// Analysis API request sharing: inside the window, the same instrument + style is
// analysed once and the answer is shared with everyone else who asks.
function Byok({ flash }) {
  const [s, setS] = useState(null)
  const [f, setF] = useState({ enabled: true, markup_pct: 40 })
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => api.getAdminByok()
    .then((r) => { setS(r); setF({ enabled: r.enabled, markup_pct: r.markup_pct }) })
    .catch((e) => flash('danger', e.message)), [flash])
  useEffect(() => { load() }, [load])

  const dirty = s && (f.enabled !== s.enabled || Number(f.markup_pct) !== s.markup_pct)

  async function save() {
    setBusy(true)
    try {
      const r = await api.setAdminByok({ enabled: f.enabled, markup_pct: Number(f.markup_pct) })
      setS(r)
      flash('ok', r.enabled
        ? `Own keys allowed, charged at ${r.markup_pct}% of what the tokens would have cost`
        : 'Own keys ignored — everyone runs on the app key')
    } catch (e) { flash('danger', e.message) } finally { setBusy(false) }
  }

  // A worked example beats a definition. "40% markup" reads as "we add 40%" to
  // about half the people who see it, and it means the opposite.
  const example = (0.010 * (Number(f.markup_pct) || 0) / 100)

  return (
    <Card
      title="Bring your own key"
      sub="A paying user can connect their own OpenAI, Anthropic or DeepSeek key on the Connections page. Their requests then go out on it and we spend nothing on tokens — so instead of the token cost, they are charged the percentage below of what those tokens WOULD have cost us. Everything around the model — the analysis engine, the data, the accounts, the scheduling — is still ours."
      right={s && !s.metered ? <span className="pill pill--muted">nothing is metered here</span> : null}
    >
      <div className="admin-field-row">
        <label className="field">
          <span className="field-label">Own keys</span>
          <select className="input" value={f.enabled ? 'on' : 'off'}
                  onChange={(e) => setF({ ...f, enabled: e.target.value === 'on' })}>
            <option value="on">Allowed</option>
            <option value="off">Off — always use the app key</option>
          </select>
        </label>
        <label className="field">
          <span className="field-label">Charge (% of token cost)</span>
          <input className="input" type="number" min="0" max="500" value={f.markup_pct}
                 disabled={!f.enabled}
                 onChange={(e) => setF({ ...f, markup_pct: e.target.value })} />
        </label>
        <button className="btn btn--primary" disabled={!dirty || busy} onClick={save}>
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
      <p className="muted" style={{ marginTop: 12, fontSize: 12.5, lineHeight: 1.6 }}>
        {s ? <>Now: {s.enabled
          ? <>a user on their own key pays <strong>{s.markup_pct}%</strong> of token cost — a run that
            would have cost us $0.010 costs them ${example.toFixed(4)} worth of credits.
            {s.markup_pct === 0 && ' At 0% their own key runs free.'}</>
          : <>off — connected keys are ignored and every request runs on the app key at full cost.</>}
          {!s.metered && ' This edition meters nothing, so the setting has no effect here.'}</>
          : 'Loading…'}
      </p>
    </Card>
  )
}

function AnalysisApi({ flash }) {
  const [s, setS] = useState(null)
  const [f, setF] = useState({ window_seconds: 60, cached_charge_pct: 50, enabled: true })
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => api.getAdminAnalysisApi()
    .then((r) => { setS(r); setF({ window_seconds: r.window_seconds, cached_charge_pct: r.cached_charge_pct, enabled: r.enabled }) })
    .catch((e) => flash('danger', e.message)), [flash])
  useEffect(() => { load() }, [load])

  const dirty = s && (f.window_seconds !== s.window_seconds
    || f.cached_charge_pct !== s.cached_charge_pct || f.enabled !== s.enabled)

  async function save() {
    setBusy(true)
    try {
      const r = await api.setAdminAnalysisApi({
        window_seconds: Number(f.window_seconds),
        cached_charge_pct: Number(f.cached_charge_pct),
        enabled: f.enabled,
      })
      setS(r)
      flash('ok', r.enabled
        ? `One analysis per instrument+style every ${r.window_seconds}s, shared at ${r.cached_charge_pct}% of cost`
        : 'Sharing off — every request runs its own analysis')
    } catch (e) { flash('danger', e.message) } finally { setBusy(false) }
  }

  return (
    <Card
      title="Analysis API requests"
      sub="Ask for the same instrument and trading style twice in the same window and it is analysed ONCE. The first caller runs the agent; anyone who asks while it is running waits for that same run, and anyone who asks after it finished gets the stored answer. They pay the fraction below instead of full price."
      right={s?.live ? <span className="pill pill--muted">{s.live.running} running · {s.live.fresh} shareable</span> : null}
    >
      <div className="admin-field-row">
        <label className="field">
          <span className="field-label">Window (seconds)</span>
          <input className="input" type="number" min="0" max="3600" value={f.window_seconds}
                 onChange={(e) => setF({ ...f, window_seconds: e.target.value })} />
        </label>
        <label className="field">
          <span className="field-label">Shared answer costs (%)</span>
          <input className="input" type="number" min="0" max="100" value={f.cached_charge_pct}
                 onChange={(e) => setF({ ...f, cached_charge_pct: e.target.value })} />
        </label>
        <label className="field">
          <span className="field-label">Sharing</span>
          <select className="input" value={f.enabled ? 'on' : 'off'}
                  onChange={(e) => setF({ ...f, enabled: e.target.value === 'on' })}>
            <option value="on">On</option>
            <option value="off">Off — every request runs</option>
          </select>
        </label>
        <button className="btn btn--primary" disabled={!dirty || busy} onClick={save}>
          {busy ? 'Saving…' : 'Save'}
        </button>
      </div>
      <p className="muted" style={{ marginTop: 12, fontSize: 12.5, lineHeight: 1.6 }}>
        {s ? <>Now: {s.enabled
          ? <>one analysis per user + agent + instrument + style every <strong>{s.window_seconds}s</strong>,
            shared answers billed at <strong>{s.cached_charge_pct}%</strong> of what that analysis cost.</>
          : <>off — every request runs its own analysis and pays full price.</>}
          {' '}A 0-second window also switches sharing off.</> : 'Loading…'}
      </p>
    </Card>
  )
}

// Daily Watch List schedule — an expandable list of UTC build times. The worker
// re-reads it every minute, so a change takes effect without a restart.
const DEFAULT_TIMES = [0, 6]
const HOURS = Array.from({ length: 24 }, (_, h) => h)
const hhmm = (h) => `${String(h).padStart(2, '0')}:00`

function WatchList({ flash }) {
  const [st, setSt] = useState(null)
  const [times, setTimes] = useState(DEFAULT_TIMES)
  const [next, setNext] = useState(0)
  const [busy, setBusy] = useState('')

  const load = useCallback(() => api.getAdminWatchList()
    .then((r) => { setSt(r); setTimes(r.hours_utc?.length ? r.hours_utc : DEFAULT_TIMES) })
    .catch((e) => flash('danger', e.message)), [flash])
  useEffect(() => { load() }, [load])

  const dirty = st && times.join(',') !== (st.hours_utc || []).join(',')
  const addable = HOURS.filter((h) => !times.includes(h))

  function add() {
    const h = addable.includes(next) ? next : addable[0]
    if (h === undefined) return
    setTimes((t) => [...t, h].sort((a, b) => a - b))
  }
  async function save() {
    setBusy('save')
    try { const r = await api.setAdminWatchSchedule(times.join(',')); setSt(r); flash('ok', `Builds at ${r.schedule_utc.join(' and ')} UTC`) }
    catch (e) { flash('danger', e.message) } finally { setBusy('') }
  }
  async function runNow() {
    setBusy('run')
    try { await api.runAdminWatchList(); flash('ok', 'Building now — it takes about 20 seconds.'); setTimeout(load, 25000) }
    catch (e) { flash('danger', e.message) } finally { setBusy('') }
  }

  const last = st?.last_run
  return (
    <Card
      title="Daily Watch List"
      sub="The UTC times the watch list is built at. Add as many as you want — each one becomes its own stored build for the day. Defaults to 00:00 and 06:00."
      right={<button className="btn btn--sm" disabled={busy === 'run' || st?.running} onClick={runNow}>
        <RefreshCw size={14} strokeWidth={2} /> {busy === 'run' ? 'Starting…' : st?.running ? 'Building…' : 'Run now'}
      </button>}
    >
      <div className="watch-times">
        {times.map((h) => (
          <span key={h} className="watch-time">
            <Clock size={13} strokeWidth={1.75} />
            {hhmm(h)}
            {times.length > 1 && (
              <button className="watch-time-x" title="Remove this build time"
                      onClick={() => setTimes((t) => t.filter((x) => x !== h))}>
                <X size={13} strokeWidth={2.2} />
              </button>
            )}
          </span>
        ))}
        {times.length === 0 && <span className="muted">No build times — add at least one.</span>}
      </div>

      <div className="key-create" style={{ marginTop: 12, flexWrap: 'wrap' }}>
        <select className="input" value={next} onChange={(e) => setNext(Number(e.target.value))}
                disabled={!addable.length} style={{ flex: '0 0 130px', maxWidth: 130 }}>
          {addable.map((h) => <option key={h} value={h}>{hhmm(h)} UTC</option>)}
        </select>
        <button className="btn btn--sm" onClick={add} disabled={!addable.length}>
          <Plus size={14} strokeWidth={2} /> Add time
        </button>
        <button className="btn btn--primary btn--sm" onClick={save} disabled={!dirty || busy === 'save' || !times.length}>
          {busy === 'save' ? 'Saving…' : 'Save schedule'}
        </button>
        {times.join(',') !== DEFAULT_TIMES.join(',') && (
          <button className="btn btn--ghost btn--sm" onClick={() => setTimes(DEFAULT_TIMES)}>Reset to 00:00 &amp; 06:00</button>
        )}
      </div>

      <p className="muted" style={{ marginTop: 12, fontSize: 12.5, lineHeight: 1.6 }}>
        {st ? <>Next build {new Date(st.next_run_utc).toUTCString().replace(' GMT', '')} UTC
          {last ? ` · last ${last.date} ${last.slot}: ${last.watching} of ${last.considered} instruments`
                : ' · nothing built yet'}
          {dirty ? ' · unsaved changes' : ''}</> : 'Loading…'}
      </p>
    </Card>
  )
}

function Admins({ s, reload, flash }) {
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState('')
  async function add() { if (!email.trim()) return; setBusy('add'); try { await api.adminAddAdmin(email.trim().toLowerCase()); setEmail(''); await reload(); flash('ok', 'Admin added') } catch (e) { flash('danger', e.message) } finally { setBusy('') } }
  async function remove(em) { if (!confirm(`Remove admin ${em}?`)) return; setBusy(em); try { await api.adminRemoveAdmin(em); await reload(); flash('ok', 'Admin removed') } catch (e) { flash('danger', e.message) } finally { setBusy('') } }
  return (
    <Card title="Admins" sub="Who can access this panel. Super-owners are permanent.">
      <table className="admin-table admin-table--rows" style={{ marginBottom: 12 }}>
        <tbody>
          {s.super_owners.map((e) => (<tr key={e}><td>{e}</td><td><span className="pill pill--muted">super-owner</span></td><td /></tr>))}
          {s.admins.map((a) => (
            <tr key={a.email}><td>{a.email}</td><td><span className="pill">{a.role}</span></td>
              <td className="admin-right"><button className="btn btn--danger btn--sm" disabled={busy === a.email} onClick={() => remove(a.email)}>Remove</button></td></tr>
          ))}
        </tbody>
      </table>
      <div className="key-create">
        <input className="input" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="existing user's email" />
        <button className="btn btn--sm" disabled={busy === 'add'} onClick={add}>{busy === 'add' ? 'Adding…' : 'Add admin'}</button>
      </div>
    </Card>
  )
}
