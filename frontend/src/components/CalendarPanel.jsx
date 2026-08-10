import { useEffect, useMemo, useRef, useState } from 'react'
import { CalendarDays, ChevronLeft, ChevronRight, Loader2 } from 'lucide-react'
import * as api from '../services/api.js'
import { currencyFlag } from '../data/flags.js'
import { useModule } from '../services/capabilities.js'

// The day's economic releases, one click from anywhere.
//
// The window is computed HERE and sent as an explicit since/until, because a
// "day" begins and ends in the reader's own timezone — a server picking the
// boundary would put the events either side of midnight on the wrong day.

const fmtTime = (iso) => {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch { return '' }
}

const dayLabel = (offset) => {
  if (offset === 0) return 'Today'
  if (offset === -1) return 'Yesterday'
  if (offset === 1) return 'Tomorrow'
  const d = new Date(); d.setDate(d.getDate() + offset)
  return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })
}

const IMPACT = { high: 'hi', moderate: 'mid', medium: 'mid', low: 'lo' }

export default function CalendarPanel() {
  // A module. Polling an endpoint that is not installed is a 404 on every open.
  const has = useModule('economic-calendar')
  const [open, setOpen] = useState(false)
  const [offset, setOffset] = useState(0)
  const [events, setEvents] = useState(null)
  const [err, setErr] = useState(null)
  const [loading, setLoading] = useState(false)
  const box = useRef(null)

  const window_ = useMemo(() => {
    const start = new Date()
    start.setDate(start.getDate() + offset)
    start.setHours(0, 0, 0, 0)
    const end = new Date(start)
    end.setDate(end.getDate() + 1)
    return { since: start.toISOString(), until: end.toISOString() }
  }, [offset])

  useEffect(() => {
    if (!open) return
    let dead = false
    setLoading(true); setErr(null)
    api.calendarDay(window_.since, window_.until)
      .then((r) => { if (!dead) setEvents(r.events || []) })
      .catch((e) => { if (!dead) { setErr(e.message); setEvents([]) } })
      .finally(() => { if (!dead) setLoading(false) })
    return () => { dead = true }
  }, [open, window_.since, window_.until])

  useEffect(() => {
    if (!open) return
    const away = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false) }
    const esc = (e) => {
      if (e.key === 'Escape') setOpen(false)
      if (e.key === 'ArrowLeft') setOffset((o) => o - 1)
      if (e.key === 'ArrowRight') setOffset((o) => o + 1)
    }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', esc)
    }
  }, [open])

  if (!has) return null

  return (
    <div className="cal" ref={box}>
      <button className="cal-btn" onClick={() => setOpen((v) => !v)}
              title="Economic calendar" aria-label="Economic calendar">
        <CalendarDays size={18} strokeWidth={1.9} />
      </button>

      {open && (
        <div className="cal-panel">
          <div className="cal-head">
            <button className="cal-nav" onClick={() => setOffset((o) => o - 1)} title="Previous day">
              <ChevronLeft size={16} strokeWidth={2.2} />
            </button>
            <button className="cal-day" onClick={() => setOffset(0)}
                    title={offset ? 'Back to today' : 'Today'}>
              {dayLabel(offset)}
            </button>
            <button className="cal-nav" onClick={() => setOffset((o) => o + 1)} title="Next day">
              <ChevronRight size={16} strokeWidth={2.2} />
            </button>
          </div>

          {loading && !events ? (
            <p className="cal-empty"><Loader2 size={15} className="rk-spin" strokeWidth={2} /> Loading…</p>
          ) : err ? (
            <p className="cal-empty">{err}</p>
          ) : !events?.length ? (
            <p className="cal-empty">Nothing scheduled {offset === 0 ? 'today' : 'that day'}.</p>
          ) : (
            <ul className="cal-list">
              {events.map((e, i) => {
                const f = currencyFlag(e.currency)
                const imp = IMPACT[String(e.impact || '').toLowerCase()] || 'lo'
                // A number that beat or missed its forecast is the whole story,
                // so it is coloured rather than left as one figure among three.
                const a = e.actual, fc = e.forecast
                const beat = a != null && fc != null && !Number.isNaN(Number(a)) && !Number.isNaN(Number(fc))
                  ? (Number(a) > Number(fc) ? 'up' : Number(a) < Number(fc) ? 'down' : 'same')
                  : null
                return (
                  <li key={`${e.time}:${e.event}:${i}`} className="cal-item">
                    <span className="cal-time">{fmtTime(e.time)}</span>
                    <span className={`cal-imp cal-imp--${imp}`} title={e.impact || ''} />
                    {f ? <img className="inst-flag cal-flag" src={f.src} alt={e.currency} title={e.currency} />
                       : <span className="cal-ccy">{e.currency}</span>}
                    <span className="cal-name">{e.event}</span>
                    <span className="cal-nums">
                      {a != null && a !== ''
                        ? <b className={beat ? `cal-a cal-a--${beat}` : 'cal-a'}>{a}</b>
                        : <b className="cal-a cal-a--pending">·</b>}
                      <em>{fc != null && fc !== '' ? fc : '–'}</em>
                      <em className="cal-prev">{e.previous != null && e.previous !== '' ? e.previous : '–'}</em>
                    </span>
                  </li>
                )
              })}
            </ul>
          )}
          <div className="cal-foot"><span>actual</span><span>forecast</span><span>previous</span></div>
        </div>
      )}
    </div>
  )
}
