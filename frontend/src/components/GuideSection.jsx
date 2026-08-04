import { useCallback, useEffect, useMemo, useState } from 'react'
import * as Icons from 'lucide-react'
import { Link } from 'react-router-dom'

// The live parts of a module guide, drawn from DATA rather than code.
//
// Every hand-written guide page in this app turned out to be the same two
// widgets: one that loads by itself and refreshes (a live table, a latest list),
// and one with an input that loads on submit (look up a symbol, explore by
// filter). So those are the two, described in guide.json, and a module author
// writes JSON instead of React — which is the only way a ZIP can add a page to
// a bundle that was built without it.
//
// A section that needs something genuinely bespoke is not served by this, and
// should not be: it wants its own component, which means it wants to be part of
// core. That line is the point of the format.

// ── template strings: "{count} rows · {age_seconds}s ago" ──────────────────────
const pick = (obj, path) =>
  String(path).split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj)

function fill(tpl, ctx) {
  if (!tpl) return ''
  return String(tpl).replace(/\{([\w.]+)\}/g, (_, p) => {
    const v = pick(ctx, p)
    return v == null ? '—' : String(v)
  })
}

// ── value formatting ──────────────────────────────────────────────────────────
function format(value, col) {
  const { format: f = 'text', digits = 2, limit = 5 } = col || {}
  if (value == null || value === '') return '—'
  switch (f) {
    case 'code':
      return <code>{String(value)}</code>
    case 'number':
      return Number.isFinite(+value) ? (+value).toFixed(digits) : String(value)
    case 'signed': {
      if (!Number.isFinite(+value)) return String(value)
      const n = +value
      return `${n > 0 ? '+' : ''}${n.toFixed(digits)}`
    }
    case 'percent':
      return Number.isFinite(+value) ? `${(+value).toFixed(digits)}%` : String(value)
    case 'list': {
      const arr = Array.isArray(value) ? value : [value]
      return arr.slice(0, limit).join(', ') + (arr.length > limit ? '…' : '')
    }
    case 'time': {
      const d = new Date(value)
      return Number.isNaN(d.getTime()) ? String(value)
        : d.toISOString().slice(0, 16).replace('T', ' ') + 'Z'
    }
    case 'badge':
      return <span className="pill">{String(value)}</span>
    default:
      return String(value)
  }
}

// A column may colour itself by the sign of its own number, which is the one
// piece of meaning worth expressing declaratively — up is up everywhere.
function toneClass(value, col) {
  if (col?.tone !== 'sign' || !Number.isFinite(+value)) return ''
  return +value > 0 ? 'stat-ok' : +value < 0 ? 'stat-bad' : ''
}

/**
 * A button a guide declares.
 *
 * A guide that documents a capability should also be able to point at the place
 * you USE it — reading how the TradeLocker module works and then having to go
 * hunting for where to connect an account is a documentation page failing at the
 * one job it had.
 */
export function GuideAction({ action }) {
  const Icon = Icons[action.icon] || null
  const cls = 'btn ' + (action.primary === false ? 'btn--ghost' : 'btn--primary')
  const inner = (
    <>
      {Icon && <Icon size={15} strokeWidth={2} />}
      {action.label}
    </>
  )
  return action.to
    ? <Link className={cls} to={action.to}>{inner}</Link>
    : <a className={cls} href={action.href} target="_blank" rel="noreferrer">{inner}</a>
}


// ── the section ───────────────────────────────────────────────────────────────
function GuideCode({ children }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="guide-code">
      <button className="guide-code-copy"
              onClick={() => { navigator.clipboard?.writeText(children).then(() => setCopied(true)); setTimeout(() => setCopied(false), 1600) }}>
        {copied ? 'Copied' : 'Copy'}
      </button>
      <pre>{children}</pre>
    </div>
  )
}

export default function GuideSection({ section, apiKey, base }) {
  // Static setup, with commands to copy. The other section types are windows
  // onto an endpoint; this one is the instructions for the part that happens on
  // the user's own machine, where this app cannot see or do anything.
  if (section.type === 'steps') {
    // Filled in, not left as placeholders. A configuration somebody has to edit
    // in two places before it works is a configuration they get wrong once and
    // then blame the product for — and both values are already known here: the
    // address they are reading this on, and their own key.
    const fill = (t) => String(t || '')
      .replaceAll('{origin}', window.location.origin)
      .replaceAll('{api_key}', apiKey || 'YOUR_API_KEY')
    return (
      <section className="guide-sec">
        <div className="guide-sec-head">
          <div>
            <h2 className="guide-sec-title">{section.title}</h2>
            {section.sub && <p className="guide-sec-sub">{section.sub}</p>}
          </div>
        </div>
        <ol className="guide-steps">
          {(section.steps || []).map((st, i) => (
            <li key={i}>
              <strong>{st.title}</strong>
              {st.body && <p>{fill(st.body)}</p>}
              {st.code && <GuideCode>{fill(st.code)}</GuideCode>}
            </li>
          ))}
        </ol>
      </section>
    )
  }

  const inputs = useMemo(() => section.inputs || [], [section.inputs])
  const manual = !!section.submit || inputs.length > 0

  const [values, setValues] = useState(() =>
    Object.fromEntries(inputs.map((i) => [i.name, i.default ?? ''])))
  const [data, setData] = useState(null)
  const [status, setStatus] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)

  // Two kinds of route, two kinds of credential. The documented API takes an
  // api_key in the query; an account-level route (connecting a broker, listing
  // your own accounts) takes the session bearer token, because it is an action
  // on the account rather than a data read. A section says which it needs.
  const session = section.auth === 'session'
  const ready = session || !!apiKey

  const call = useCallback(async (path, params) => {
    const q = new URLSearchParams(session ? {} : { api_key: apiKey })
    Object.entries(params || {}).forEach(([k, v]) => {
      if (v !== '' && v != null) q.set(k, String(v))
    })
    const token = session ? localStorage.getItem('auth_token') : null
    return fetch(`${base}${path}?${q.toString()}`,
                 token ? { headers: { Authorization: `Bearer ${token}` } } : undefined)
  }, [apiKey, base, session])

  const load = useCallback(async (vals) => {
    if (!ready) return
    setBusy(true)
    try {
      const res = await call(section.endpoint, { ...(section.query || {}), ...(vals || {}) })
      const body = await res.json()
      if (!res.ok) throw new Error(body.detail || `request failed (${res.status})`)
      if (body.error) throw new Error(body.error)
      setData(body); setErr(null)
    } catch (e) {
      setErr(e.message); setData(null)
    } finally {
      setBusy(false)
    }
  }, [ready, call, section.endpoint, section.query])

  const loadStatus = useCallback(async () => {
    if (!ready || !section.status?.endpoint) return
    try {
      const res = await call(section.status.endpoint, {})
      setStatus(res.ok ? await res.json() : null)
    } catch { setStatus(null) }
  }, [ready, call, section.status])

  // A section with no inputs loads itself, and keeps itself current if it asked
  // to. One with inputs waits — firing a search the user has not typed yet is
  // just a wasted call against their quota.
  useEffect(() => {
    if (manual) { loadStatus(); return }
    load({}); loadStatus()
    if (!section.refresh_seconds) return
    const t = setInterval(() => { load({}); loadStatus() }, section.refresh_seconds * 1000)
    return () => clearInterval(t)
  }, [manual, load, loadStatus, section.refresh_seconds])

  const Icon = Icons[section.icon] || Icons.Activity
  const rows = (() => {
    if (!data) return null
    const r = section.rows ? pick(data, section.rows) : data
    return Array.isArray(r) ? r : null
  })()

  const st = section.status
  const healthy = st && status ? !!pick(status, st.ok || 'healthy') : null
  const statusError = st && status ? pick(status, st.error || 'last_error') : null

  return (
    <section className="card">
      <div className="card-head">
        <Icon size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">{section.title}</h2>
          {section.sub && <p className="card-sub">{section.sub}</p>}
        </div>
        {healthy !== null && (
          <span className={`pill ${healthy ? 'pill--ok' : 'pill--warn'}`} style={{ marginLeft: 'auto' }}>
            {healthy ? (st.label_ok || 'fetching') : (st.label_bad || 'not fetching')}
          </span>
        )}
        {section.action && (
          <span className="guide-actions" style={{ marginLeft: healthy !== null ? 0 : 'auto' }}>
            <GuideAction action={section.action} />
          </span>
        )}
      </div>

      <div className="card-body">
        {inputs.length > 0 && (
          <form className="form-grid" onSubmit={(e) => { e.preventDefault(); load(values) }}>
            {inputs.map((i) => (
              <label className="field" key={i.name}>
                <span className="field-label">{i.label || i.name}</span>
                {i.options ? (
                  <select className="input" value={values[i.name] ?? ''}
                          onChange={(e) => setValues((v) => ({ ...v, [i.name]: e.target.value }))}>
                    {i.options.map((o) => (
                      <option key={String(o.value ?? o)} value={o.value ?? o}>{o.label ?? o}</option>
                    ))}
                  </select>
                ) : (
                  <input className="input" value={values[i.name] ?? ''} placeholder={i.placeholder || ''}
                         onChange={(e) => setValues((v) => ({ ...v, [i.name]: e.target.value }))} />
                )}
              </label>
            ))}
            <div className="field form-submit">
              <button className="btn btn--primary" type="submit" disabled={busy || !ready}>
                {busy ? 'Loading…' : (section.submit || 'Run')}
              </button>
            </div>
          </form>
        )}

        {!ready && <p className="muted">Generate an API key in Settings to run this.</p>}
        {err && <div className="alert alert--danger" style={{ marginTop: 12 }}>{err}</div>}

        {section.lead && data && (
          <div className="sym-section-title" style={{ marginTop: 6 }}>{fill(section.lead, data)}</div>
        )}

        {rows && rows.length === 0 && (
          <p className="muted" style={{ marginTop: 12 }}>{section.empty || 'Nothing to show.'}</p>
        )}

        {rows && rows.length > 0 && section.type === 'list' && (
          <ItemList rows={rows} item={section.item || {}} />
        )}
        {rows && rows.length > 0 && section.type !== 'list' && (
          <RowTable rows={rows} columns={section.columns || []} />
        )}

        {section.footer && data && (
          <p className="card-sub" style={{ marginTop: 14 }}>{fill(section.footer, data)}</p>
        )}

        {statusError && (
          <div className="alert alert--warn" style={{ marginTop: 12 }}>Last fetch error: {statusError}</div>
        )}
      </div>
    </section>
  )
}

function RowTable({ rows, columns }) {
  return (
    <div style={{ overflowX: 'auto', marginTop: 12 }}>
      <table className="params">
        <thead>
          <tr>{columns.map((c) => <th key={c.key}>{c.label || c.key}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.id ?? r.pid ?? r.symbol ?? i}>
              {columns.map((c) => {
                const v = pick(r, c.key)
                return (
                  <td key={c.key} className={[toneClass(v, c), c.muted ? 'muted' : ''].filter(Boolean).join(' ')}>
                    {format(v, c)}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ItemList({ rows, item }) {
  return (
    <div className="guide-items" style={{ marginTop: 12 }}>
      {rows.map((r, i) => (
        <div className="guide-item" key={r.id ?? r.url ?? i}>
          <div className="guide-item-head">
            <span className="guide-item-title">{fill(item.title || '{title}', r)}</span>
            {item.badge && pick(r, item.badge.replace(/[{}]/g, '')) != null && (
              <span className="pill">{fill(item.badge, r)}</span>
            )}
          </div>
          {item.meta && <div className="guide-item-meta muted">{fill(item.meta, r)}</div>}
          {item.body && <div className="guide-item-body">{fill(item.body, r)}</div>}
        </div>
      ))}
    </div>
  )
}
