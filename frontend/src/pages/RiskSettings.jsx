import { useEffect, useState, useCallback } from 'react'
import { ShieldCheck, Plus, Trash2, Clock, Info, RotateCcw } from 'lucide-react'
import DashboardLayout from '../components/DashboardLayout.jsx'
import * as api from '../services/api.js'
import BrokerLogo from '../components/BrokerLogo.jsx'

const STYLES = ['scalp', 'intraday', 'swing', 'position']
const BASES = ['equity', 'balance']
// A small, common set of trading timezones; the value is any IANA name the browser has.
const TZS = ['UTC', 'Africa/Johannesburg', 'Europe/London', 'Europe/Berlin',
  'America/New_York', 'America/Chicago', 'Asia/Dubai', 'Asia/Singapore', 'Asia/Tokyo', 'Australia/Sydney']

const EMPTY = {
  risk_pct: '', reward_rr: '', max_dd_day: '', max_dd_week: '', max_dd_month: '',
  risk_basis: 'equity', trade_style: '', trading_tz: 'UTC', trading_hours: [],
}

// Map a stored row (numbers/null) → form strings.
function toForm(row) {
  if (!row) return { ...EMPTY }
  const s = (v) => (v === null || v === undefined ? '' : String(v))
  return {
    risk_pct: s(row.risk_pct), reward_rr: s(row.reward_rr),
    max_dd_day: s(row.max_dd_day), max_dd_week: s(row.max_dd_week), max_dd_month: s(row.max_dd_month),
    risk_basis: row.risk_basis || 'equity', trade_style: row.trade_style || '',
    trading_tz: row.trading_tz || 'UTC', trading_hours: (row.trading_hours || []).map((w) => ({ ...w })),
  }
}

// Form strings → API payload (numbers/null).
function toPayload(account, f) {
  const n = (v) => (v === '' || v === null ? null : Number(v))
  return {
    account,
    risk_pct: n(f.risk_pct), reward_rr: n(f.reward_rr),
    max_dd_day: n(f.max_dd_day), max_dd_week: n(f.max_dd_week), max_dd_month: n(f.max_dd_month),
    risk_basis: f.risk_basis, trade_style: f.trade_style || null,
    trading_tz: f.trading_tz || 'UTC',
    trading_hours: (f.trading_hours || []).filter((w) => w.start && w.end),
  }
}

export default function RiskSettings() {
  const [data, setData] = useState({ profile: null, accounts: [] })
  const [accountList, setAccountList] = useState([])   // [{id,label,broker}]
  const [scope, setScope] = useState('')               // '' = profile; else account id
  const [form, setForm] = useState({ ...EMPTY })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => {
    const [rs, accts] = await Promise.all([api.getRiskSettings(), api.getAllAccounts().catch(() => null)])
    setData(rs)
    const list = []
    for (const a of accts?.exness?.accounts || []) {
      if (a.is_archived) continue
      list.push({ id: String(a.account_number), label: `${a.account_number} · ${a.currency || ''}`.trim(), broker: 'exness' })
    }
    for (const c of accts?.tradelocker?.connections || []) {
      for (const a of c.accounts || []) list.push({ id: String(a.account_id), label: `${a.account_id} · ${a.currency || 'TL'}`, broker: 'tradelocker' })
    }
    setAccountList(list)
  }, [])

  useEffect(() => { load().catch((e) => setMsg({ type: 'danger', text: e.message })) }, [load])

  // load the selected scope's stored row into the form
  useEffect(() => {
    const row = scope === '' ? data.profile : (data.accounts || []).find((a) => String(a.account) === scope)
    setForm(toForm(row))
  }, [scope, data])

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }))
  const setHour = (i, k, v) => setForm((f) => {
    const hours = f.trading_hours.map((w, j) => (j === i ? { ...w, [k]: v } : w))
    return { ...f, trading_hours: hours }
  })
  const addHour = () => setForm((f) => ({ ...f, trading_hours: [...f.trading_hours, { start: '09:00', end: '17:00' }] }))
  const removeHour = (i) => setForm((f) => ({ ...f, trading_hours: f.trading_hours.filter((_, j) => j !== i) }))

  async function save() {
    setBusy(true); setMsg(null)
    try {
      await api.saveRiskSettings(toPayload(scope, form))
      setMsg({ type: 'ok', text: scope === '' ? 'Profile risk parameters saved.' : `Risk parameters saved for account ${scope}.` })
      await load()
    } catch (e) { setMsg({ type: 'danger', text: e.message }) } finally { setBusy(false) }
  }
  async function clearScope() {
    setBusy(true); setMsg(null)
    try {
      await api.clearRiskSettings(scope)
      setMsg({ type: 'ok', text: scope === '' ? 'Profile parameters cleared.' : 'Override cleared — this account now inherits the profile.' })
      await load()
    } catch (e) { setMsg({ type: 'danger', text: e.message }) } finally { setBusy(false) }
  }

  const isProfile = scope === ''

  return (
    <DashboardLayout title="Risk Settings">
      <div className="settings-stack">
        <section className="card">
          <div className="card-head">
            <ShieldCheck size={18} strokeWidth={1.75} />
            <div>
              <h2 className="card-title">Risk parameters</h2>
              <p className="card-sub">
                Your own risk rules — used by the SL/TP engine and the AI agent on every trade. Set a
                <strong> profile default</strong> that applies everywhere, and optionally override it
                <strong> per account</strong>. If you set nothing, trades default to 2% risk.
              </p>
            </div>
          </div>
          <div className="card-body">
            {msg && <div className={`alert alert--${msg.type === 'ok' ? 'ok' : 'danger'}`}>{msg.text}</div>}
            <div className="risk-scopes">
              <button className={'pill-opt' + (isProfile ? ' pill-opt--on' : '')} onClick={() => setScope('')}>
                Profile default
              </button>
              {accountList.map((a) => (
                <button key={a.id} className={'pill-opt' + (scope === a.id ? ' pill-opt--on' : '')}
                        onClick={() => setScope(a.id)} title={`Override for ${a.label}`}>
                  <BrokerLogo size={15} broker={a.broker} />
                  {a.label}{data.accounts?.some((x) => String(x.account) === a.id) ? ' •' : ''}
                </button>
              ))}
            </div>
            {!isProfile && (
              <p className="risk-inherit-note"><Info size={13} strokeWidth={1.9} /> Leave a field blank to inherit the profile default. A dot (•) marks accounts that already have an override.</p>
            )}
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <div>
              <h2 className="card-title">{isProfile ? 'Profile default' : `Account ${scope}`}</h2>
              <p className="card-sub">Per-trade sizing, drawdown ceilings and when trading is allowed.</p>
            </div>
          </div>
          <div className="card-body">
            <div className="risk-grid">
              <NumField label="Risk per trade" suffix="%" value={form.risk_pct} onChange={(v) => set('risk_pct', v)}
                        hint="How much of the account each trade may lose." placeholder="2" />
              <NumField label="Reward : Risk" suffix="R" value={form.reward_rr} onChange={(v) => set('reward_rr', v)}
                        hint="Target reward as a multiple of the risk (2 = 2×)." placeholder="2" />
              <NumField label="Max drawdown / day" suffix="%" value={form.max_dd_day} onChange={(v) => set('max_dd_day', v)}
                        hint="Stop trading once the day's loss reaches this." placeholder="5" />
              <NumField label="Max drawdown / week" suffix="%" value={form.max_dd_week} onChange={(v) => set('max_dd_week', v)} placeholder="10" />
              <NumField label="Max drawdown / month" suffix="%" value={form.max_dd_month} onChange={(v) => set('max_dd_month', v)} placeholder="20" />
            </div>

            <div className="risk-row2">
              <div className="field">
                <span className="field-label">Risk is a % of</span>
                <div className="pill-row">
                  {BASES.map((b) => (
                    <button key={b} className={'pill-opt' + (form.risk_basis === b ? ' pill-opt--on' : '')}
                            onClick={() => set('risk_basis', b)}>{b === 'equity' ? 'Equity' : 'Balance'}</button>
                  ))}
                </div>
              </div>
              <div className="field">
                <span className="field-label">Default trade style</span>
                <div className="pill-row">
                  <button className={'pill-opt' + (form.trade_style === '' ? ' pill-opt--on' : '')} onClick={() => set('trade_style', '')}>Auto</button>
                  {STYLES.map((s) => (
                    <button key={s} className={'pill-opt' + (form.trade_style === s ? ' pill-opt--on' : '')}
                            onClick={() => set('trade_style', s)}>{s[0].toUpperCase() + s.slice(1)}</button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <Clock size={18} strokeWidth={1.75} />
            <div>
              <h2 className="card-title">Trading hours</h2>
              <p className="card-sub">When the agent may open trades. Add as many windows as you like (e.g. 10:00–12:00 and 13:20–17:00). No windows = allowed any time.</p>
            </div>
          </div>
          <div className="card-body">
            <div className="field" style={{ maxWidth: 280 }}>
              <span className="field-label">Timezone</span>
              <select className="input" value={form.trading_tz} onChange={(e) => set('trading_tz', e.target.value)}>
                {TZS.map((z) => <option key={z} value={z}>{z}</option>)}
              </select>
            </div>

            <div className="hours-list">
              {form.trading_hours.length === 0 && (
                <p className="muted" style={{ margin: '4px 0' }}>No windows — trading allowed at any time.</p>
              )}
              {form.trading_hours.map((w, i) => (
                <div className="hours-row" key={i}>
                  <input type="time" className="input hours-time" value={w.start} onChange={(e) => setHour(i, 'start', e.target.value)} />
                  <span className="hours-dash">to</span>
                  <input type="time" className="input hours-time" value={w.end} onChange={(e) => setHour(i, 'end', e.target.value)} />
                  <button className="btn btn--danger btn--icon btn--sm" title="Remove window" onClick={() => removeHour(i)}>
                    <Trash2 size={15} />
                  </button>
                </div>
              ))}
            </div>
            <button className="btn btn--ghost btn--sm" onClick={addHour} style={{ marginTop: 10 }}>
              <Plus size={15} strokeWidth={2} /> Add window
            </button>
          </div>
        </section>

        <div className="risk-actions">
          <button className="btn btn--ghost" onClick={clearScope} disabled={busy} title="Remove this scope's saved parameters">
            <RotateCcw size={15} strokeWidth={2} /> {isProfile ? 'Clear profile' : 'Clear override'}
          </button>
          <button className="btn btn--primary" onClick={save} disabled={busy}>
            {busy ? 'Saving…' : isProfile ? 'Save profile default' : `Save for ${scope}`}
          </button>
        </div>
      </div>
    </DashboardLayout>
  )
}

function NumField({ label, suffix, value, onChange, hint, placeholder }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <div className="num-wrap">
        <input className="input" type="number" min="0" step="any" inputMode="decimal"
               value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
        {suffix && <span className="num-suffix">{suffix}</span>}
      </div>
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  )
}
