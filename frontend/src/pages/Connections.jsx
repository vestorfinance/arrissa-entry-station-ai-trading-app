import { useCallback, useEffect, useState } from 'react'
import { Plug, Plus, Trash2, Check, RefreshCw, Power, PowerOff, ExternalLink, Pencil,
         Search, X } from 'lucide-react'
import DashboardLayout from '../components/DashboardLayout.jsx'
import { useCapabilities } from '../services/capabilities.js'
import { backdrop } from '../services/backdrop.js'
import * as api from '../services/api.js'

// Everything this app can be joined to, in one place.
//
// A connection is NAMED. Three columns on a settings row could hold one OpenAI
// key and never tell you which of two you were using; a named row can hold both,
// say which is live, and be edited without being retyped.
//
// The marks are circles. Where a logo exists it fills the circle; where it does
// not, the initial does — so a new integration can be added the day it works
// rather than the day its artwork arrives.

// Above this many models the list gets a filter box, and never more than the
// cap is drawn at once. Chosen models are always shown regardless.
const MODEL_FILTER_FROM = 24
const MODEL_CAP = 60

const TONE = { teal: '45, 212, 191', orange: '251, 146, 60', blue: '59, 130, 246',
               indigo: '79, 70, 229', pink: '244, 114, 182', slate: '148, 163, 184' }

function Mark({ type, size = 44 }) {
  const rgb = TONE[type.tone] || TONE.slate
  const [broken, setBroken] = useState(false)
  useEffect(() => setBroken(false), [type.logo])
  // A type may supply a short code — Gemini, Grok and Groq would otherwise be
  // three identical G circles. Two letters need to sit smaller than one.
  const code = type.mark || type.name.slice(0, 1).toUpperCase()
  return (
    <span className="conn-mark" style={{
      width: size, height: size, minWidth: size,
      background: `rgba(${rgb}, 0.18)`, color: `rgb(${rgb})`,
      fontSize: Math.round(size * (code.length > 1 ? 0.3 : 0.36)),
      letterSpacing: code.length > 1 ? '-0.02em' : undefined,
    }}>
      {/* A logo that will not load leaves the same circle, filled with the code
          — an empty ring reads as a broken page, a lettered one does not. */}
      {type.logo && !broken
        ? <img src={type.logo} alt={type.name} onError={() => setBroken(true)} />
        : code}
    </span>
  )
}

export default function Connections() {
  const caps = useCapabilities()
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [msg, setMsg] = useState(null)
  const [editing, setEditing] = useState(null)   // {type, conn?}
  const [models, setModels] = useState({})
  const [aiCfg, setAiCfg] = useState(null)
  const [loading, setLoading] = useState(null)
  const [query, setQuery] = useState('')
  const [modelQ, setModelQ] = useState({})   // per-kind model filter
  // Brokers keep their state in their own module rather than in the connections
  // table, because their password is used once and never stored. So whether one
  // is connected has to be ASKED, per kind, from the endpoint it named.
  const [managedState, setManagedState] = useState({})

  const load = useCallback(() => {
    api.listConnections().then((data) => {
      setD(data)
      for (const t of data?.types || []) {
        if (!t.managed_by?.status) continue
        api.get(t.managed_by.status)
          .then((st) => setManagedState((m) => ({ ...m, [t.kind]: st })))
          // A module that is installed but not yet migrated has no status
          // endpoint. Not connected is the honest answer, not an error banner.
          .catch(() => setManagedState((m) => ({ ...m, [t.kind]: { connected: false } })))
      }
    }).catch((e) => setErr(e.message))
    // Asked always, not only where the EDITION is byok: a cloud instance may
    // allow its own keys too, and the server is the one that knows.
    api.aiConfig().then(setAiCfg).catch(() => {})
  }, [caps?.byok])
  useEffect(() => { load() }, [load])

  async function disconnectManaged(t) {
    setErr(null); setMsg(null)
    try {
      await api.post(t.managed_by.disconnect, {})
      setMsg(`${t.name} disconnected.`)
      load()
    } catch (e) { setErr(e.message) }
  }

  const allTypes = d?.types || []
  const conns = d?.connections || []
  const byKind = (k) => conns.filter((c) => c.kind === k)

  // Match the name, the kind, or a word from the blurb — and the NAMES the user
  // gave their own connections, which is what they will actually reach for.
  const q = query.trim().toLowerCase()
  const hit = (s) => String(s || '').toLowerCase().includes(q)
  const types = q
    ? allTypes.filter((t) => hit(t.name) || hit(t.kind) || hit(t.blurb)
        || byKind(t.kind).some((c) => hit(c.name)))
    : allTypes
  const shownConns = q
    ? conns.filter((c) => hit(c.name) || hit(c.kind)
        || hit(allTypes.find((t) => t.kind === c.kind)?.name))
    : conns

  // Asked of the provider as soon as there is a key to ask with — nobody adds a
  // key for its own sake, they add it to pick a model.
  const fetchModels = useCallback(async (kind) => {
    setLoading(kind)
    try {
      const r = await api.listAiModels(kind)
      if (r.error) setErr(r.error)
      // A model the provider has retired is dropped from your selection the
      // moment they tell us it is gone — say so, or a model quietly vanishing
      // from the picker looks like a bug.
      if (r.dropped?.length) {
        setMsg(`${r.dropped.join(', ')} — no longer offered by ${kind}, removed from your models.`)
      }
      setModels((m) => ({ ...m, [kind]: r.models || [] }))
    } catch (e) { setErr(e.message) } finally { setLoading(null) }
  }, [])
  // Whether keys of your own may be used here at all — the server decides, since
  // on a metered instance it is an operator switch rather than a build flag.
  // Declared BEFORE the effect below, which names it in its dependency array:
  // that array is evaluated during render, so a later `const` would throw.
  const ownKeys = !!aiCfg?.own_keys
  const markup = aiCfg?.markup_pct
  const chosen = (aiCfg?.models || []).filter((m) => m.own !== false)

  // Only AI providers have a model list. A messaging connection asked "what can
  // you run" would just be a wasted call and a red error on the page.
  const isAi = useCallback((kind) =>
    (d?.types || []).find((t) => t.kind === kind)?.group === 'ai', [d?.types])
  useEffect(() => {
    if (!ownKeys) return
    conns.filter((c) => c.enabled && isAi(c.kind) && !models[c.kind])
      .forEach((c) => fetchModels(c.kind))
  }, [ownKeys, conns.length]) // eslint-disable-line react-hooks/exhaustive-deps
  const isChosen = (p, m) => chosen.some((x) => x.key === `${p}:${m}`)
  async function toggleModel(p, m) {
    const asPairs = (list) => list.map((x) => ({
      provider: x.key.split(':')[0], model: x.key.split(':').slice(1).join(':') }))
    const next = isChosen(p, m)
      ? asPairs(chosen.filter((x) => x.key !== `${p}:${m}`))
      : [...asPairs(chosen), { provider: p, model: m }]
    setAiCfg(await api.chooseAiModels(next))
  }

  async function act(fn, note) {
    setErr(null); setMsg(null)
    try { setD(await fn()); setMsg(note); api.aiConfig().then(setAiCfg) }
    catch (e) { setErr(e.message) }
  }

  return (
    <DashboardLayout title="Connections">
      <div className="guide guide--wide">
        <div className="page-search">
          <Search size={17} strokeWidth={1.9} />
          <input className="page-search-input" value={query} placeholder="Search connections…"
                 onChange={(e) => setQuery(e.target.value)}
                 onKeyDown={(e) => { if (e.key === 'Escape') setQuery('') }} />
          {query && (
            <button className="page-search-clear" onClick={() => setQuery('')} title="Clear">
              <X size={15} strokeWidth={2} />
            </button>
          )}
        </div>

        <div className="guide-intro card">
          <div className="card-body">
            <h2 className="card-title">Connections</h2>
            <p className="card-sub">
              Everything this app can be joined to. Add one, give it a name you will recognise,
              and edit it later without retyping the secret — keys are stored encrypted and never
              shown again.
            </p>
          </div>
        </div>

        {err && <div className="alert alert--danger">{err}</div>}
        {msg && <div className="alert alert--ok"><Check size={14} strokeWidth={2} /> {msg}</div>}

        {!!types.length && (
          <div className="mod-group-head">
            <h3 className="mod-group-title">Available</h3>
            <span className="mod-group-blurb">Pick one to connect. You can add more than one of each.</span>
          </div>
        )}
        {q && !types.length && !shownConns.length && (
          <p className="muted" style={{ textAlign: 'center', padding: '18px 0' }}>
            Nothing matches “{query}”.
          </p>
        )}

        <div className="conn-grid">
          {types.map((t) => {
            const mine = byKind(t.kind)
            // A broker keeps its state in its own module, not in this table, so
            // "connected" has to be asked for rather than counted here.
            const managed = t.managed_by ? (managedState[t.kind] || null) : null
            const isOn = t.managed_by ? !!managed?.connected : mine.length > 0
            return (
              <div className="conn-card" key={t.kind}>
                <div className="conn-card-head">
                  <Mark type={t} />
                  <div className="conn-card-title">
                    <span className="conn-card-name">{t.name}</span>
                    <span className="conn-card-count">
                      {t.managed_by
                        ? (isOn ? (managed?.exness_email || managed?.connections?.[0]?.email
                                   || 'Connected') : 'Not connected')
                        : (mine.length ? `${mine.length} connected` : 'Not connected')}
                    </span>
                  </div>
                </div>
                <p className="conn-card-blurb">{t.blurb}</p>
                <div className="conn-card-foot">
                  {t.docs && (
                    <a className="btn btn--ghost btn--sm" href={t.docs} target="_blank" rel="noreferrer">
                      {t.docs_label || 'Get a key'} <ExternalLink size={12} />
                    </a>
                  )}
                  {t.managed_by && isOn && (
                    <button className="btn btn--danger btn--sm"
                            onClick={() => disconnectManaged(t)}>
                      Disconnect
                    </button>
                  )}
                  <button className="btn btn--primary btn--sm"
                          onClick={() => setEditing({ type: t, conn: null })}>
                    <Plus size={14} strokeWidth={2} /> {isOn ? 'Reconnect' : 'Connect'}
                  </button>
                </div>
              </div>
            )
          })}
        </div>

        {!!shownConns.length && (
          <>
            <div className="mod-group-head">
              <h3 className="mod-group-title">Your connections</h3>
              <span className="mod-group-blurb">
                The first enabled connection of each kind is the one in use.
              </span>
            </div>
            {shownConns.map((c) => {
              const t = types.find((x) => x.kind === c.kind) || { name: c.kind, tone: 'slate' }
              const mine = models[c.kind] || []
              return (
                <section className="card conn-row" key={c.id}>
                  <div className="conn-row-head">
                    <Mark type={t} size={34} />
                    <div className="conn-row-title">
                      <span className="conn-row-name">{c.name}</span>
                      <span className="conn-row-kind">{t.name}</span>
                    </div>
                    <span className={'mod-state mod-state--' + (c.enabled ? 'on' : 'off')}>
                      {c.enabled ? 'In use' : 'Disabled'}
                    </span>
                    <span className="conn-row-actions">
                      <button className="btn btn--ghost btn--sm"
                              onClick={() => setEditing({ type: t, conn: c })}>
                        <Pencil size={13} strokeWidth={2} /> Edit
                      </button>
                      <button className="btn btn--ghost btn--sm"
                              onClick={() => act(() => api.updateConnection(c.id, { enabled: !c.enabled }),
                                                 c.enabled ? 'Disabled.' : 'Enabled.')}>
                        {c.enabled ? <PowerOff size={13} strokeWidth={2} /> : <Power size={13} strokeWidth={2} />}
                      </button>
                      <button className="btn btn--danger btn--icon btn--sm" title="Remove"
                              onClick={() => window.confirm(`Remove "${c.name}"?`) &&
                                act(() => api.deleteConnection(c.id), 'Connection removed.')}>
                        <Trash2 size={13} strokeWidth={1.9} />
                      </button>
                    </span>
                  </div>

                  {ownKeys && c.enabled && t.group === 'ai' && (
                    <div className="conn-row-models">
                      <div className="conn-row-models-head">
                        <span>Models to offer in chat</span>
                        <button className="btn btn--ghost btn--sm" disabled={loading === c.kind}
                                onClick={() => fetchModels(c.kind)}>
                          <RefreshCw size={12} strokeWidth={2} />
                          {loading === c.kind ? 'Loading…' : 'Refresh'}
                        </button>
                      </div>
                      {loading === c.kind && !mine.length && (
                        <p className="muted">Asking {t.name} what it can run…</p>
                      )}
                      {mine.length > MODEL_FILTER_FROM && (
                        <input className="input conn-model-filter"
                               value={modelQ[c.kind] || ''}
                               placeholder={`Filter ${mine.length} models…`}
                               onChange={(e) => setModelQ((v) => ({ ...v, [c.kind]: e.target.value }))} />
                      )}
                      {!!mine.length && (() => {
                        // OpenRouter alone lists 337 models. Drawing them all is
                        // a wall nobody reads, so the list is filtered and capped
                        // — but everything already CHOSEN stays on screen and at
                        // the front, or a model could be selected and then become
                        // impossible to deselect.
                        const q = (modelQ[c.kind] || '').trim().toLowerCase()
                        const on = mine.filter((m) => isChosen(c.kind, m.model))
                        const rest = mine.filter((m) => !isChosen(c.kind, m.model)
                          && (!q || m.model.toLowerCase().includes(q)))
                        const shown = [...on, ...rest.slice(0, MODEL_CAP)]
                        const hidden = rest.length - Math.max(0, shown.length - on.length)
                        return (
                          <>
                            <div className="ai-models">
                              {shown.map((m) => (
                                <button key={m.model} type="button"
                                        className={'pill pill-opt' + (isChosen(c.kind, m.model) ? ' pill-opt--on' : '')}
                                        onClick={() => toggleModel(c.kind, m.model)}>
                                  {isChosen(c.kind, m.model) && <Check size={12} strokeWidth={2.5} />}
                                  {m.model}
                                </button>
                              ))}
                            </div>
                            {hidden > 0 && (
                              <p className="muted" style={{ marginTop: 8, fontSize: 12 }}>
                                {hidden} more — type above to narrow.
                              </p>
                            )}
                            {q && !rest.length && !on.length && (
                              <p className="muted" style={{ marginTop: 8, fontSize: 12 }}>
                                No model matches “{modelQ[c.kind]}”.
                              </p>
                            )}
                          </>
                        )
                      })()}
                    </div>
                  )}
                </section>
              )
            })}
            {ownKeys && (
              <p className="card-sub" style={{ textAlign: 'center' }}>
                {chosen.length
                  ? `${chosen.length} model${chosen.length === 1 ? '' : 's'} available in chat: `
                    + chosen.map((m) => m.name).join(', ')
                  : 'No models selected yet — pick one and it joins the built-in models in chat.'}
                {/* Their key does not mean free. Say the number here, where they
                    are choosing, not only in a billing page they may never open. */}
                {!caps?.byok && markup != null && (
                  <> Running on your own key costs <strong>{markup}%</strong> of what those
                    tokens would have cost us{markup === 0 ? ' — nothing' : ''}.</>
                )}
              </p>
            )}
          </>
        )}

        {editing && (
          <ConnectionForm
            type={editing.type} conn={editing.conn}
            onClose={() => setEditing(null)}
            onSaved={(data, note) => { setD(data); setEditing(null); setMsg(note); load() }}
            onError={setErr} />
        )}
      </div>
    </DashboardLayout>
  )
}

function ConnectionForm({ type, conn, onClose, onSaved, onError }) {
  const [name, setName] = useState(conn?.name || type.name)
  const [values, setValues] = useState({})
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault(); setBusy(true); onError(null)
    try {
      // A broker is managed by its own module, and the difference is not
      // cosmetic: its password is used once to obtain a session and must never
      // be written down. Storing these fields the ordinary way would encrypt
      // and keep them, which is the one thing the product promises not to do.
      // So they go straight to the module's endpoint and nothing lands here.
      if (type.managed_by?.connect) {
        await api.post(type.managed_by.connect, values)
        onSaved(null, `${type.name} connected.`)
        return
      }
      const data = conn
        ? await api.updateConnection(conn.id, { name, config: values })
        : await api.createConnection({ kind: type.kind, name, config: values })
      onSaved(data, conn ? 'Connection updated.' : `${type.name} connected.`)
    } catch (e2) { onError(e2.message) } finally { setBusy(false) }
  }

  return (
    <div className="modal-overlay" {...backdrop(onClose)}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <div className="modal-head">
          <Plug size={16} strokeWidth={1.9} />
          <span className="modal-title">{conn ? `Edit ${conn.name}` : `Connect ${type.name}`}</span>
        </div>

        <label className="field">
          <span className="field-label">Name</span>
          <input className="input" value={name} autoFocus
                 placeholder={type.name} onChange={(e) => setName(e.target.value)} />
        </label>
        <p className="card-sub">Yours to choose — "Personal", "Work", whatever tells them apart.</p>

        {(type.fields || []).map((f) => (
          <label className="field" key={f.key}>
            <span className="field-label">{f.label}</span>
            <input className="input" type={f.secret ? 'password' : 'text'}
                   value={values[f.key] ?? ''}
                   placeholder={conn && conn.config[f.key] === 'set'
                     ? '•••••••• (stored — leave blank to keep it)'
                     : (f.placeholder || '')}
                   onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))} />
          </label>
        ))}

        <div className="modal-actions">
          <button type="button" className="btn btn--ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn--primary" type="submit" disabled={busy}>
            {busy ? 'Saving…' : conn ? 'Save changes' : 'Connect'}
          </button>
        </div>
      </form>
    </div>
  )
}
