import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bell, ExternalLink, Check, AlertTriangle, Info, ArrowRight } from 'lucide-react'
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

export default function Notifications() {
  const [data, setData] = useState(null)
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
    return () => { dead = true; clearInterval(t) }
  }, [])

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

  return (
    <div className="notif" ref={box}>
      <button className="notif-bell" onClick={() => setOpen((v) => !v)}
              aria-label={items.length ? `${items.length} things need attention` : 'Notifications'}>
        <Bell size={18} strokeWidth={1.9} />
        {blocked > 0 && <span className="notif-dot">{blocked > 9 ? '9+' : blocked}</span>}
      </button>

      {open && (
        <div className="notif-panel">
          <div className="notif-head">
            <span>Needs attention</span>
            {items.length > 0 && <em>{items.length}</em>}
          </div>

          {items.length === 0 ? (
            /* Not an empty box. "Nothing is wrong" is information, and the
               panel that says so is the one worth opening again. */
            <div className="notif-empty">
              <Check size={20} strokeWidth={2} />
              <span>Everything is set up</span>
              <p>Modules are connected and up to date.</p>
            </div>
          ) : (
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
