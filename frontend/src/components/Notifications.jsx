import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bell, ExternalLink, Check, AlertTriangle, Info, ArrowRight,
         Newspaper, CalendarClock, Megaphone, BarChart3, X } from 'lucide-react'
import InstrumentFlag from './InstrumentFlag.jsx'
import { onAlertsChanged } from '../services/alertBus.js'
import { ask, promptForAlert } from '../services/askAgent.js'
import { countryFlag } from '../data/flags.js'
import * as api from '../services/api.js'

// Everything outstanding, where it can be found without looking.
//
// The app can be half-configured in ways that make no noise at all. Sentiment
// with no Myfxbook connection returns nothing and says nothing. The Exness
// module with no account behind it looks exactly like one that works, right up
// until a trade is attempted. None of that is an error, so none of it appears
// anywhere an error would — and somebody concludes the product is quiet when it
// is actually waiting on them.
//
// The badge counts only what is BLOCKED. A bell that lights up because a module
// update exists is a bell people stop reading, and then the one that meant
// something goes unread with it.

const TONE = { blocked: AlertTriangle, todo: Info, info: Info }

// Market alerts live here too, not only in the toasts. A toast is gone in
// twenty seconds and the browser is often not even open when the thing happens
// — the worker runs regardless — so the bell is where the history lives.
const KIND_ICON = { truth: Megaphone, news: Newspaper, calendar_soon: CalendarClock, calendar_out: BarChart3 }
const ago = (iso) => {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 90) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

export default function Notifications() {
  const [data, setData] = useState(null)
  const [feed, setFeed] = useState({ alerts: [], unread: 0 })
  const [open, setOpen] = useState(false)
  const box = useRef(null)
  const navigate = useNavigate()

  useEffect(() => {
    let dead = false
    const load = () => api.notifications()
      .then((d) => { if (!dead) setData(d) })
      .catch(() => {})
    load()
    // Slowly. Nothing here changes on a timescale worth chasing, and the
    // check walks every installed module's connection state.
    const t = setInterval(load, 120000)

    // The alert feed moves on a different timescale from setup problems, so it
    // gets its own, faster poll rather than dragging the module walk with it.
    const loadFeed = () => api.marketAlertFeed()
      .then((f) => { if (!dead) setFeed(f) })
      .catch(() => {})
    loadFeed()
    const t2 = setInterval(loadFeed, 30000)
    const off = onAlertsChanged(loadFeed)   // a toast dismissed → the bell agrees

    return () => { dead = true; clearInterval(t); clearInterval(t2); off() }
  }, [])

  // Opening it clears the badge but keeps the list: seen and dismissed are
  // different, and history you cannot re-read is not history.
  useEffect(() => {
    if (!open || !feed.unread) return
    api.marketAlertsSeen().then(() => setFeed((f) => ({ ...f, unread: 0 }))).catch(() => {})
  }, [open, feed.unread])

  async function clearAlert(key) {
    setFeed((f) => ({ ...f, alerts: f.alerts.filter((a) => a.key !== key),
                      unread: Math.max(0, f.unread - 1) }))
    try { await api.marketAlertDismiss(key) } catch { /* it reappears on the next poll */ }
  }

  // Close on anything that means "not this".
  useEffect(() => {
    if (!open) return
    const away = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false) }
    const esc = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', esc)
    }
  }, [open])

  const items = data?.items || []
  const blocked = data?.blocked || 0
  const alerts = feed.alerts || []
  // The badge counts what is BLOCKED plus what is genuinely NEW. A module
  // update does not light it; a market-moving post does.
  const badge = blocked + (feed.unread || 0)

  return (
    <div className="notif" ref={box}>
      <button className="notif-bell" onClick={() => setOpen((v) => !v)}
              aria-label={items.length ? `${items.length} things need attention` : 'Notifications'}>
        <Bell size={18} strokeWidth={1.9} />
        {badge > 0 && <span className="notif-dot">{badge > 9 ? '9+' : badge}</span>}
      </button>

      {open && (
        <div className="notif-panel">
          <div className="notif-head">
            <span>{alerts.length ? 'Notifications' : 'Needs attention'}</span>
            {alerts.length > 0 && (
              <button className="notif-clear-all" onClick={() => {
                setFeed((f) => ({ ...f, alerts: [], unread: 0 }))
                api.marketAlertDismiss().catch(() => {})
              }}>Clear all</button>
            )}
            {!alerts.length && items.length > 0 && <em>{items.length}</em>}
          </div>

          {/* What HAPPENED, above what is merely outstanding: a release that
              printed two minutes ago matters more than a module update. */}
          {alerts.length > 0 && (
            <ul className="notif-list notif-list--alerts">
              {alerts.map((a) => {
                const Icon = KIND_ICON[a.kind] || Megaphone
                const cf = !a.symbols?.length && a.country ? countryFlag(a.country) : null
                return (
                  <li key={a.key} className={'notif-item notif-alert' + (a.unread ? ' notif-alert--new' : '')}>
                    <span className="notif-item-icon">
                      {a.symbols?.length ? <InstrumentFlag symbol={a.symbols[0]} size="sm" />
                        : cf ? <img className="inst-flag" src={cf.src} alt="" />
                        : <Icon size={15} strokeWidth={2} />}
                    </span>
                    <div className="notif-item-text">
                      <strong>{a.title}</strong>
                      {a.body && <p>{a.body}</p>}
                      <div className="notif-alert-foot">
                        <span className="notif-when">{ago(a.created_at)}</span>
                        {/* Not a link out. The useful thing to do with a story
                            is ask what it means for a position right now. */}
                        <button className="notif-open" onClick={() => {
                          setOpen(false)
                          navigate('/dashboard')
                          ask(promptForAlert(a))
                        }}>
                          Analyse this <ArrowRight size={11} strokeWidth={2.2} />
                        </button>
                      </div>
                    </div>
                    <button className="notif-clear" title="Dismiss"
                            onClick={() => clearAlert(a.key)}>
                      <X size={13} strokeWidth={2.2} />
                    </button>
                  </li>
                )
              })}
            </ul>
          )}

          {items.length === 0 && alerts.length === 0 ? (
            /* Not an empty box. "Nothing is wrong" is information, and the
               panel that says so is the one worth opening again. */
            <div className="notif-empty">
              <Check size={20} strokeWidth={2} />
              <span>Everything is set up</span>
              <p>Modules are connected and up to date.</p>
            </div>
          ) : items.length === 0 ? null : (
            <ul className="notif-list">
              {items.map((n) => {
                const Icon = TONE[n.severity] || Info
                return (
                  <li key={n.id} className={`notif-item notif-item--${n.severity}`}>
                    <span className="notif-item-icon">
                      {n.logo
                        ? <img src={n.logo} alt="" />
                        : <Icon size={15} strokeWidth={2} />}
                    </span>
                    <div className="notif-item-text">
                      <strong>{n.title}</strong>
                      {n.body && <p>{n.body}</p>}
                      <div className="notif-item-actions">
                        {n.to && (
                          <button className="btn btn--sm"
                                  onClick={() => { setOpen(false); navigate(n.to) }}>
                            {n.action || 'Open'} <ArrowRight size={12} strokeWidth={2.2} />
                          </button>
                        )}
                        {/* The way to get the thing you do not have yet. A
                            broker connection can be blocked by having no
                            account at all, and "Connect" is no help there. */}
                        {n.link && (
                          <a className="btn btn--ghost btn--sm" href={n.link}
                             target="_blank" rel="noreferrer">
                            {n.link_label || 'Open'} <ExternalLink size={12} />
                          </a>
                        )}
                      </div>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
