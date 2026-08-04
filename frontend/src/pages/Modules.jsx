import { useCallback, useEffect, useRef, useState } from 'react'
import * as Icons from 'lucide-react'
import { Puzzle, Upload, Trash2, Power, PowerOff, RefreshCw, Check, KeyRound,
         ShoppingCart, Download, ExternalLink, AlertTriangle, Search, X,
         Sparkles, ArrowUpCircle, Lock, Fingerprint, Copy} from 'lucide-react'
import DashboardLayout from '../components/DashboardLayout.jsx'
import * as api from '../services/api.js'
import * as moduleBus from '../services/moduleBus.js'

// The Modules page is a shop window and a control panel at once, and the two
// jobs pull in opposite directions: a shop wants to show you what you do not
// have, a control panel wants to show you what is running. So each module gets
// ONE card whose primary button says the single next thing that makes sense —
// Buy, Install, Activate, Deactivate — and nothing else competes with it.

const GROUPS = [
  { key: 'broker',   label: 'Brokers',  blurb: 'Where your trades actually go. You need at least one.' },
  { key: 'trading',  label: 'Trading',  blurb: 'Things that act on an account.' },
  { key: 'analysis', label: 'Analysis', blurb: 'Data your agents and the assistant can read.' },
  { key: 'connect',  label: 'Connections', blurb: 'Ways in and out — messaging, notifications, other apps.' },
  { key: 'other',    label: 'Installed from a file', blurb: 'Not listed in the store.' },
]
const KNOWN = new Set(GROUPS.map((g) => g.key))

// Every tone the icon tile can wear. A module names one in the catalogue; a
// module the store has never heard of still gets a stable colour rather than a
// grey box, picked from its id so it is the same one every time.
const TONES = ['indigo', 'sky', 'amber', 'orange', 'blue', 'ok', 'pink', 'warn',
               'violet', 'teal', 'fuchsia']
const toneFor = (m) => m.tone || TONES[
  [...String(m.id)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % TONES.length]

// What a module costs, in three words or fewer. A module with no price is not
// free — it is bundled, and saying which parent it comes with is the whole
// answer to "why can I not buy this on its own".
const PER = { yearly: '/yr', monthly: '/mo', once: '' }

const priceLabel = (m, nameOf, billing) =>
  m.included_with ? `With ${nameOf(m.included_with)}`
    : m.price_usd === 0 ? 'Free'
    : m.price_usd ? `$${m.price_usd}${PER[billing] ?? '/yr'}`
    : '—' 

// The buyer should never have to type where they are. This instance already
// knows: it is the host serving this page. Carrying it to the checkout — with
// the operator's email — means Buy lands on a form that is already filled in,
// and the licence binds to the box that asked for it rather than to whatever
// the buyer typed from memory.
// The operator's email, held at module scope because BOTH the all-access link
// (in Modules) and each card's Buy button (in ModuleCard, a different component)
// need it. Threading a prop through for a prefill is not worth the wiring — and
// getting that wiring wrong is exactly what broke this page: the state lived in
// one component and was read in the other, so every card threw on render.
let operatorEmail = ''
// This installation's own id. Held at module scope for the same reason as the
// email: the Buy button lives in a different component from the page that
// fetches it.
let instanceId = ''

function buyLink(url) {
  if (!url) return url
  try {
    const u = new URL(url)
    // The id, not the hostname. `localhost` is every install in the world, so a
    // licence bound to it is bound to all of them and provable by none — which
    // is what sent people to type a key by hand. Falls back to the host only
    // until the id has loaded.
    u.searchParams.set('instance', instanceId || window.location.host)
    if (operatorEmail) u.searchParams.set('email', operatorEmail)
    // Where to come back to. The id says WHICH box owns the licence and nothing
    // about how to reach it — different questions, and answering only the first
    // is what left a local buyer on a page of raw JSON with nowhere to go.
    u.searchParams.set('return_url', window.location.origin)
    return u.toString()
  } catch { return url }
}

export default function Modules() {
  // Prefills the checkout so the operator confirms rather than types.
  useEffect(() => { api.me().then((p) => { operatorEmail = p?.email || '' }).catch(() => {}) }, [])

  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(null)          // module id, 'install' or 'licence'
  const [note, setNote] = useState(null)
  const [upd, setUpd] = useState(null)     // the updates summary, incl. whether they self-install
  const [inst, setInst] = useState(null)   // this installation's own id
  const [idShown, setIdShown] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [licence, setLicence] = useState('')
  const [showLicence, setShowLicence] = useState(false)
  const [query, setQuery] = useState('')
  const fileRef = useRef(null)

  const load = useCallback(() => {
    api.moduleCatalog()
      .then((d) => { setData(d); setErr(null) })
      .catch((e) => setErr(e.message))
  }, [])
  // Ask the store what this box has bought, and apply it. This is what makes a
  // purchase arrive on its own: the operator pays on entrystation.com and comes
  // back here, and the module is simply theirs. Nobody types a key.
  const claim = useCallback(async (loud) => {
    setBusy('licence'); setErr(null)
    try {
      const d = await api.claimEntitlements()
      // Bought and installed are one state now, so the page has to show the new
      // cards immediately rather than the ones it loaded a second ago.
      if (d.installed?.length) {
        api.moduleCatalog().then((v) => { setData(v) }).catch(() => {})
        moduleBus.changed()
      }
      setData(d)
      if (d.installed?.length) setNote(`Purchase applied — ${d.installed.join(', ')} installed and ready.`)
      else if (d.applied) setNote(`Purchase found and applied${d.claimed?.length ? ` — ${d.claimed.join(', ')}` : ''}. `
                             + 'Click Install on what you have bought.')
      else if (loud) setNote(d.reason || 'Nothing new to collect for this instance.')
      return d
    } catch (e) {
      if (loud) setErr(e.message)
      return null
    } finally { setBusy(null) }
  }, [])

  // On arrival: load the page, then collect anything owed to this box.
  //
  // Not on every visit — proving this host costs the store a round trip back to
  // us, and there is nothing to learn when the licence is already in place. So
  // it runs when there is a reason: straight back from a purchase, or on a box
  // that holds no licence at all.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    const fromUrl = (q.get('licence_key') || '').trim()
    const bought = q.get('bought')
    // A licence key does not belong in browser history or in a screenshot.
    if (fromUrl || bought) window.history.replaceState({}, '', window.location.pathname)

    let dead = false
    ;(async () => {
      let view = null
      try { view = await api.moduleCatalog(); if (!dead) { setData(view); setErr(null) } }
      catch (e) { if (!dead) setErr(e.message) }
      // Whether updates arrive on their own. Its own call because the store
      // being unreachable must not take the toggle down with it.
      api.moduleUpdates().then((u) => { if (!dead) setUpd(u) }).catch(() => {})
      api.moduleInstance().then((i) => {
        instanceId = i.instance_id || ''
        if (!dead) setInst(i)
      }).catch(() => {})
      if (dead || (!bought && !fromUrl && view?.has_licence)) return

      const d = await claim(false)
      if (dead || d?.applied) return
      // The key on the URL is the fallback, for a box the store cannot reach
      // back to — behind NAT, on a private name, or simply down at that moment.
      // It is only ever a fallback now, which is the point: the entitlement is
      // the mechanism and the key is the spare.
      if (!fromUrl) return
      try {
        await api.setModuleLicence(fromUrl)
        // The key is only half of it. Somebody who has just paid should not
        // then be told to press Install — the purchase already said that.
        const got = await claim(false)
        setData(await api.moduleCatalog())
        moduleBus.changed()
        setNote(got?.installed?.length
          ? `Payment received — ${got.installed.join(', ')} installed and ready.`
          : `Payment received — licence applied${bought ? ` for ${bought}` : ''}.`)
      } catch (e) {
        setErr(`Paid, but the key could not be applied here: ${e.message}. `
               + `Your key is ${fromUrl} — enter it under Licence.`)
      }
    })()
    return () => { dead = true }
  }, [claim])

  async function act(id, fn, confirmText) {
    if (confirmText && !window.confirm(confirmText)) return
    setBusy(id); setErr(null); setNote(null)
    try {
      const res = await fn()
      setNote(res.note || 'Done.')
      load(); moduleBus.changed()
    } catch (e) { setErr(e.message) } finally { setBusy(null) }
  }

  async function upload(file) {
    if (!file) return
    setBusy('install'); setErr(null); setNote(null)
    try {
      const res = await api.installModule(file)
      setNote(`${res.name} ${res.version} — ${res.note}`)
      load(); moduleBus.changed()
    } catch (e) { setErr(e.message) } finally { setBusy(null) }
  }

  const mods = data?.modules || []
  const running = mods.filter((m) => m.installed && m.enabled).length

  // Search what a person would actually type: the name, the id, or a word from
  // the blurb. Not the group — that is what the headings are for.
  // A bundled module names its parent, which may be a MODULE (hmr → exness) or
  // a BUNDLE (news → market-data). Both have names; neither should surface as a
  // slug on a card.
  const nameOf = (id) =>
    mods.find((x) => x.id === id)?.name
    || (data?.bundles || []).find((b) => b.id === id)?.name
    || id
  const q = query.trim().toLowerCase()
  const shown = q
    ? mods.filter((m) => [m.name, m.id, m.tagline].some(
        (f) => String(f || '').toLowerCase().includes(q)))
    : mods

  return (
    <DashboardLayout title="Module Store">
      <div className="guide guide--wide">
        <div className="page-search">
          <Search size={17} strokeWidth={1.9} />
          <input className="page-search-input" value={query} placeholder="Search modules…"
                 onChange={(e) => setQuery(e.target.value)}
                 onKeyDown={(e) => { if (e.key === 'Escape') setQuery('') }} />
          {query && (
            <button className="page-search-clear" onClick={() => setQuery('')} title="Clear">
              <X size={15} strokeWidth={2} />
            </button>
          )}
        </div>

        <input ref={fileRef} type="file" accept=".zip" hidden
               onChange={(e) => { upload(e.target.files?.[0]); e.target.value = '' }} />

        {/* Whether an entitled update arrives on its own. The cards say
            "v1.0.2 available" either way; this is the difference between a
            button somebody has to come here and find, and a fix that lands
            while nobody is looking. Self-hosted only — the hosted service is
            deployed, not updated from its own store. */}
        {upd && (
          <div className="mod-auto">
            <label className="mod-auto-toggle">
              <input type="checkbox" checked={!!upd.auto}
                     onChange={(e) => {
                       const on = e.target.checked
                       setUpd((u) => ({ ...u, auto: on }))       // answer the click at once
                       api.setAutoUpdate(on)
                         .then((r) => setUpd((u) => ({ ...u, auto: r.auto })))
                         .catch(() => setUpd((u) => ({ ...u, auto: !on })))
                     }} />
              <span>Install updates automatically</span>
            </label>
            {/* Everything eligible, now. The per-module buttons are still
                there and still right for taking one; this is for the case the
                page usually presents — several at once, all entitled, and no
                reason to press them one at a time. */}
            {upd.count > 0 && upd.modules?.some((m) => m.can_update) && (
              <button className="btn btn--primary btn--sm" disabled={busy === '*'}
                      onClick={() => act('*', async () => {
                        const r = await api.updateAll()
                        const n = (r.took || []).length
                        setNote(n
                          ? `${n} module${n === 1 ? '' : 's'} updated`
                            + (r.restarting ? ' — restarting to apply, reload in a moment.' : '.')
                          : 'Nothing to update.')
                        return r
                      })}>
                <ArrowUpCircle size={14} strokeWidth={2} />
                {busy === '*' ? 'Updating…' : 'Update everything eligible'}
              </button>
            )}
            <span className="mod-auto-note">
              {upd.auto
                ? 'Checked every six hours. Only modules your subscription still covers, '
                  + 'and only while the instance is idle.'
                : 'Updates wait for you to press the button on each module.'}
              {upd.blocked > 0 && (
                <> {upd.blocked} update{upd.blocked === 1 ? '' : 's'} held back — the subscription lapsed.</>
              )}
            </span>
          </div>
        )}

        {/* What this box is called when it buys something. Shown rather than
            hidden: the operator has to be able to give it to the store, and a
            credential nobody can read is a credential nobody can use. Folded
            away by default because Buy carries it on its own and almost nobody
            needs to look. */}
        {inst?.instance_id && (
          <div className="mod-ident">
            <button type="button" className="mod-ident-head" onClick={() => setIdShown((v) => !v)}>
              <Fingerprint size={14} strokeWidth={2} />
              This installation <em>{inst.short}</em>
              <span>{idShown ? 'hide' : 'show'}</span>
            </button>
            {idShown && (
              <div className="mod-ident-body">
                <code className="mod-ident-id">{inst.instance_id}</code>
                <button className="btn btn--sm"
                        onClick={() => {
                          navigator.clipboard?.writeText(inst.instance_id)
                          setNote('Instance id copied.')
                        }}>
                  <Copy size={13} strokeWidth={2} /> Copy
                </button>
                <p className="mod-ident-note">
                  Purchases bind to this, not to the address in your browser —
                  so a licence works on <code>localhost</code>, behind NAT, and
                  after you move the box to a real domain. Buy carries it for
                  you; you only need it if you are paying from another machine.
                  Treat it like a password.
                </p>
              </div>
            )}
          </div>
        )}

        {err && <div className="alert alert--danger">{err}</div>}
        {note && <div className="alert alert--ok"><Check size={14} strokeWidth={2} /> {note}</div>}
        {data?.error && (
          <div className="alert">
            <AlertTriangle size={14} strokeWidth={2} /> {data.error} — prices and one-click install
            are unavailable, but everything already installed still works.
          </div>
        )}

        <section
          className={'card mod-drop' + (dragging ? ' mod-drop--over' : '')}
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); upload(e.dataTransfer.files?.[0]) }}>
          {/* One row, three zones: what this is, where it comes from, and the
              action. The buttons are grouped on the right so the eye lands on
              the primary one instead of picking through a list. */}
          <div className="mod-install">
            <span className="mod-install-icon"><Upload size={19} strokeWidth={1.9} /></span>
            <div className="mod-install-text">
              <span className="mod-install-title">
                {busy === 'install' ? 'Installing…' : 'Install a module'}
              </span>
              <span className="mod-install-sub">
                Drop a <code>.zip</code> here or choose a file — signature-checked before anything
                is written.
              </span>
            </div>
            <div className="mod-install-actions">
              {/* The manual way to do what the page already did on arrival. It
                  exists for the case the automatic one cannot cover: a purchase
                  made while this box was down, or from someone else's laptop. */}
              <button className="btn btn--ghost btn--sm" disabled={busy === 'licence'}
                      onClick={() => { setNote(null); claim(true) }}>
                <RefreshCw size={14} strokeWidth={2} />
                {busy === 'licence' ? 'Checking…' : 'Check for purchases'}
              </button>
              <button className="btn btn--ghost btn--sm" onClick={() => setShowLicence((v) => !v)}>
                <KeyRound size={14} strokeWidth={2} />
                {data?.has_licence ? 'Licence' : 'Enter licence'}
              </button>
              <a className="btn btn--ghost btn--sm" target="_blank" rel="noreferrer"
                 href={`${data?.store_url || 'https://entrystation.com'}/modules`}>
                <ShoppingCart size={14} strokeWidth={2} /> Store <ExternalLink size={12} />
              </a>
              <button className="btn btn--primary btn--sm" disabled={busy === 'install'}
                      onClick={() => fileRef.current?.click()}>
                <Upload size={14} strokeWidth={2} />
                {busy === 'install' ? 'Installing…' : 'Choose file'}
              </button>
            </div>
          </div>

          {showLicence && (
            <div className="mod-licence">
              <form className="mod-licence-form"
                    onSubmit={async (e) => {
                      e.preventDefault(); setBusy('licence'); setErr(null); setNote(null)
                      try {
                        const d = await api.setModuleLicence(licence.trim())
                        setData(d); setShowLicence(false); setLicence('')
                        setNote(d.has_licence ? 'Licence saved — what it covers is unlocked below.'
                                              : 'Licence cleared.')
                      } catch (e2) { setErr(e2.message) } finally { setBusy(null) }
                    }}>
                <label className="field">
                  <span className="field-label">
                    Licence key from entrystation.com — only needed if this instance cannot be
                    reached from the store, or you are moving it to a new server
                  </span>
                  <input className="input" value={licence} placeholder="ES-XXXXX-XXXXX-XXXXX-XXXXX"
                         onChange={(e) => setLicence(e.target.value)} autoFocus />
                </label>
                <button className="btn btn--primary btn--sm" type="submit" disabled={busy === 'licence'}>
                  {busy === 'licence' ? 'Saving…' : 'Save'}
                </button>
              </form>
            </div>
          )}
        </section>

        {/* Everything, for less than the parts. Only worth showing to someone
            who does not already own it — an offer to buy what you have is an
            advert, not an offer. The numbers come from the catalogue, so a
            price change anywhere cannot leave this quoting a stale saving. */}
        {(() => {
          const all = (data?.bundles || []).find((b) => b.all_access)
          if (!all || !all.saving_usd) return null
          const missing = mods.filter((m) => m.price_usd !== 0 && !m.owned)
          if (!missing.length) return null
          return (
            <a className="mod-allaccess" href={buyLink(all.purchase_url)} target="_blank" rel="noreferrer">
              <span className="mod-allaccess-icon"><Sparkles size={20} strokeWidth={1.8} /></span>
              <span className="mod-allaccess-main">
                <span className="mod-allaccess-title">
                  {all.name} — every paid module, {all.discount_pct}% off
                </span>
                <span className="mod-allaccess-sub">
                  {all.tagline} Bought separately that is ${all.full_price_usd}
                  {PER[data?.billing] ?? '/yr'}.
                </span>
              </span>
              <span className="mod-allaccess-price">
                <span className="mod-allaccess-was">${all.full_price_usd}</span>
                <span className="mod-allaccess-now">
                  ${all.price_usd}{PER[data?.billing] ?? '/yr'}
                </span>
                <span className="mod-allaccess-save">save ${all.saving_usd}</span>
              </span>
            </a>
          )
        })()}

        {!data && <p className="muted">Reading the catalogue…</p>}

        {data && q && shown.length === 0 && (
          <p className="muted" style={{ textAlign: 'center', padding: '18px 0' }}>
            Nothing matches “{query}”.
          </p>
        )}

        {GROUPS.map(({ key, label, blurb }) => {
          const items = shown.filter((m) => {
            const g = m.group || 'other'
            return KNOWN.has(g) ? g === key : key === 'other'
          })
          if (!items.length) return null
          return (
            <section key={key}>
              <div className="mod-group-head">
                <h3 className="mod-group-title">{label}</h3>
                <span className="mod-group-blurb">{blurb}</span>
              </div>
              <div className="mod-grid">
                {items.map((m) => (
                  <ModuleCard key={m.id} m={m} busy={busy === m.id} act={act}
                              nameOf={nameOf} billing={data?.billing} />
                ))}
              </div>
            </section>
          )
        })}

        {data && (
          <p className="card-sub" style={{ textAlign: 'center' }}>
            {q ? `${shown.length} of ${mods.length} shown · ` : ''}{running} of {mods.length} modules running
            <button className="btn btn--ghost btn--sm" style={{ marginLeft: 10 }} onClick={load}>
              <RefreshCw size={13} strokeWidth={1.9} /> Refresh
            </button>
          </p>
        )}
      </div>
    </DashboardLayout>
  )
}

function ModuleCard({ m, busy, act, nameOf, billing }) {
  const Icon = Icons[m.icon] || Puzzle
  const installed = m.installed
  const on = installed && m.enabled
  const failed = ['failed', 'unmet', 'invalid'].includes(m.status)

  return (
    <div className={'mod-card' + (on ? ' mod-card--on' : '') + (failed ? ' mod-card--bad' : '')}>
      <div className="mod-card-head">
        <span className={`mod-card-icon mod-card-icon--${toneFor(m)}`}>
          <Icon size={19} strokeWidth={1.8} />
        </span>
        <div className="mod-card-title-wrap">
          <span className="mod-card-name">{m.name}</span>
          <span className="mod-card-version">
            {m.installed_version || m.version || ''}
            {m.update_available && (
              <em className={m.can_update ? 'mod-upd' : 'mod-upd mod-upd--blocked'}>
                {' · '}v{m.version} available
              </em>
            )}
          </span>
        </div>
        <span className={'mod-price'
                         + (m.price_usd === 0 ? ' mod-price--free' : '')
                         + (m.included_with ? ' mod-price--bundled' : '')}>
          {priceLabel(m, nameOf, billing)}
        </span>
      </div>

      <p className="mod-card-blurb">{m.tagline}</p>

      {failed && m.error && <div className="mod-error">{m.error}</div>}
      {!!(m.requires || []).length && (
        <div className="mod-card-needs">Needs {m.requires.join(', ')}</div>
      )}

      <div className="mod-card-foot">
        <span className={'mod-state mod-state--' + (on ? 'on' : installed ? 'off' : 'none')}>
          {on ? 'Active' : installed ? 'Inactive' : m.owned ? 'Not installed' : 'Not purchased'}
        </span>

        <span className="mod-card-actions">
          {/* One primary action: the single next thing that makes sense here. */}
          {!m.owned && (
            <a className="btn btn--primary btn--sm" href={buyLink(m.purchase_url)}
               target="_blank" rel="noreferrer">
              <ShoppingCart size={14} strokeWidth={2} />
              {m.included_with ? `Buy ${nameOf(m.included_with)}` : 'Buy'}
            </a>
          )}
          {m.owned && !installed && (
            <button className="btn btn--primary btn--sm" disabled={busy || !m.deliverable}
                    title={m.deliverable ? '' : 'No published build yet'}
                    onClick={() => act(m.id, () => api.installFromStore(m.id))}>
              <Download size={14} strokeWidth={2} /> {busy ? 'Installing…' : 'Install'}
            </button>
          )}
          {m.update_available && (
            m.can_update ? (
              <button className="btn btn--primary btn--sm" disabled={busy}
                      title={`Update to v${m.version}`}
                      onClick={() => act(m.id, () => api.updateModule(m.id))}>
                <ArrowUpCircle size={14} strokeWidth={2} />
                {busy ? 'Updating…' : `Update to v${m.version}`}
              </button>
            ) : (
              /* Say why BEFORE it is pressed. A button that answers 402 when
                 clicked is worse than one that explains itself. */
              <span className="mod-upd-why" title={m.update_blocked}>
                <Lock size={13} strokeWidth={2} /> Update needs a live subscription
              </span>
            )
          )}
          {installed && !on && (
            <button className="btn btn--primary btn--sm" disabled={busy}
                    onClick={() => act(m.id, () => api.enableModule(m.id))}>
              <Power size={14} strokeWidth={2} /> Activate
            </button>
          )}
          {on && (
            <button className="btn btn--ghost btn--sm" disabled={busy}
                    onClick={() => act(m.id, () => api.disableModule(m.id))}>
              <PowerOff size={14} strokeWidth={2} /> Deactivate
            </button>
          )}
          {installed && (
            <button className="btn btn--ghost btn--icon btn--sm" title="Remove" disabled={busy}
                    onClick={() => act(m.id, () => api.removeModule(m.id),
                      `Remove ${m.name}?\n\nIt stops serving immediately and its files go. ` +
                      `Its stored data is KEPT, so reinstalling loses nothing.`)}>
              <Trash2 size={14} strokeWidth={1.9} />
            </button>
          )}
        </span>
      </div>
    </div>
  )
}
