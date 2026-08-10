import { useEffect, useMemo, useRef, useState } from 'react'
import { CalendarDays, ChevronLeft, ChevronRight, Loader2, X, GripHorizontal } from 'lucide-react'
import * as api from '../services/api.js'
import { currencyFlag } from '../data/flags.js'
import { useModule } from '../services/capabilities.js'

// The day's high-impact releases, one click from anywhere.
//
// It behaves like the Live card rather than a dropdown: it can be dragged
// somewhere useful and it STAYS there while you work. A menu that closes on the
// next click is fine for something you glance at; this is something you keep
// open next to a chart while waiting for a number.
//
// The window is computed HERE and sent as an explicit since/until, because a
// "day" begins and ends in the reader's own timezone — a server picking that
// boundary would put releases either side of midnight on the wrong day.

const POS_KEY = 'arrissa.cal.pos'
// How long before a release it starts flashing, and how long it may keep
// flashing if the actual never arrives — without a cap a stuck event pulses for
// ever and the signal stops meaning anything.
const LEAD_MS = 30 * 1000
const GIVE_UP_MS = 30 * 60 * 1000

const readJSON = (k, fb) => {
  try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : fb } catch { return fb }
}

const fmtTime = (iso) => {
  try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }
  catch { return '' }
}

const dayLabel = (offset) => {
  if (offset === 0) return 'Today'
  if (offset === -1) return 'Yesterday'
  if (offset === 1) return 'Tomorrow'
  const d = new Date(); d.setDate(d.getDate() + offset)
  return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })
}

const IMPACT = { high: 'hi', moderate: 'mid', medium: 'mid', low: 'lo' }
const hasValue = (v) => v != null && v !== '' && v !== '-'

export default function CalendarPanel() {
  // A module. Polling an endpoint that is not installed is a 404 on every open.
  const has = useModule('economic-calendar')
  const [open, setOpen] = useState(false)
  const [offset, setOffset] = useState(0)
  const [events, setEvents] = useState(null)
  const [err, setErr] = useState(null)
  const [loading, setLoading] = useState(false)
  const [pos, setPos] = useState(() => readJSON(POS_KEY, null))
  const [now, setNow] = useState(() => Date.now())
  const [mobile, setMobile] = useState(
    () => typeof window !== 'undefined' && window.innerWidth <= 860)
  const cardRef = useRef(null)
  const dragRef = useRef(null)

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 860px)')
    const on = (e) => setMobile(e.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])

  useEffect(() => { if (pos) localStorage.setItem(POS_KEY, JSON.stringify(pos)) }, [pos])

  const window_ = useMemo(() => {
    const start = new Date()
    start.setDate(start.getDate() + offset)
    start.setHours(0, 0, 0, 0)
    const end = new Date(start)
    end.setDate(end.getDate() + 1)
    return { since: start.toISOString(), until: end.toISOString() }
  }, [offset])

  const load = useMemo(() => () => {
    setErr(null)
    return api.calendarDay(window_.since, window_.until, 'high')
      .then((r) => setEvents(r.events || []))
      .catch((e) => { setErr(e.message); setEvents([]) })
  }, [window_.since, window_.until])

  useEffect(() => {
    if (!open) return
    let dead = false
    setLoading(true)
    load().finally(() => { if (!dead) setLoading(false) })
    return () => { dead = true }
  }, [open, load])

  // Which rows are live right now: from LEAD_MS before the release until its
  // actual appears. Recomputed on a one-second tick, which is also what drives
  // the flash starting and stopping without a re-fetch.
  const pending = useMemo(() => {
    const out = new Set()
    for (const e of events || []) {
      if (hasValue(e.actual)) continue
      const t = Date.parse(e.time)
      if (Number.isNaN(t)) continue
      if (now >= t - LEAD_MS && now <= t + GIVE_UP_MS) out.add(`${e.time}:${e.event}`)
    }
    return out
  }, [events, now])

  useEffect(() => {
    if (!open) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [open])

  // While something is due, ask more often — the flash stops when the actual
  // lands, and it can only land if we go and look for it.
  useEffect(() => {
    if (!open) return
    const ms = pending.size ? 15000 : 120000
    const t = setInterval(() => load(), ms)
    return () => clearInterval(t)
  }, [open, pending.size, load])

  // Escape closes it. Clicking elsewhere deliberately does NOT: this is a card
  // you keep open beside a chart, not a menu.
  useEffect(() => {
    if (!open) return
    const esc = (e) => {
      if (e.key === 'Escape') setOpen(false)
      else if (e.key === 'ArrowLeft') setOffset((o) => o - 1)
      else if (e.key === 'ArrowRight') setOffset((o) => o + 1)
    }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [open])

  function onDragStart(e) {
    if (mobile) return                        // the full-width sheet does not move
    if (e.target.closest('button')) return    // let the day arrows through
    const rect = cardRef.current.getBoundingClientRect()
    dragRef.current = { dx: e.clientX - rect.left, dy: e.clientY - rect.top }
    window.addEventListener('mousemove', onDragMove)
    window.addEventListener('mouseup', onDragEnd)
    e.preventDefault()
  }
  function onDragMove(e) {
    const { dx, dy } = dragRef.current || {}
    const el = cardRef.current
    if (!el) return
    const w = el.offsetWidth, h = el.offsetHeight
    setPos({
      left: Math.max(8, Math.min(e.clientX - dx, window.innerWidth - w - 8)),
      top: Math.max(8, Math.min(e.clientY - dy, window.innerHeight - h - 8)),
    })
  }
  function onDragEnd() {
    window.removeEventListener('mousemove', onDragMove)
    window.removeEventListener('mouseup', onDragEnd)
  }

  if (!has) return null

  // A dragged position is desktop-only. The mobile view is a full-width sheet,
  // and the two share a browser — a position saved on a wide screen would push
  // the card off a narrow one.
  const style = !mobile && pos ? { left: pos.left, top: pos.top, right: 'auto' } : undefined

  return (
    <div className="cal">
      <button className="cal-btn" onClick={() => setOpen((v) => !v)}
              title="Economic calendar" aria-label="Economic calendar">
        <CalendarDays size={18} strokeWidth={1.9} />
      </button>

      {open && (
        <div className={'cal-panel' + (pos && !mobile ? ' cal-panel--moved' : '')}
             ref={cardRef} style={style}>
          <div className="cal-head" onMouseDown={onDragStart}>
            <GripHorizontal className="cal-grip" size={14} strokeWidth={1.9} />
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
            <button className="cal-x" onClick={() => setOpen(false)} title="Close">
              <X size={15} strokeWidth={2.2} />
            </button>
          </div>

          {loading && !events ? (
            <p className="cal-empty"><Loader2 size={15} className="rk-spin" strokeWidth={2} /> Loading…</p>
          ) : err ? (
            <p className="cal-empty">{err}</p>
          ) : !events?.length ? (
            <p className="cal-empty">
              No high-impact releases {offset === 0 ? 'today' : 'that day'}.
            </p>
          ) : (
            <ul className="cal-list">
              {events.map((e, i) => {
                const f = currencyFlag(e.currency)
                const imp = IMPACT[String(e.impact || '').toLowerCase()] || 'lo'
                // A number that beat or missed its forecast is the whole story,
                // so it is coloured rather than left as one figure among three.
                const a = e.actual, fc = e.forecast
                const beat = hasValue(a) && hasValue(fc)
                  && !Number.isNaN(Number(a)) && !Number.isNaN(Number(fc))
                  ? (Number(a) > Number(fc) ? 'up' : Number(a) < Number(fc) ? 'down' : 'same')
                  : null
                const live = pending.has(`${e.time}:${e.event}`)
                return (
                  <li key={`${e.time}:${e.event}:${i}`}
                      className={'cal-item' + (live ? ' cal-item--due' : '')}>
                    <span className="cal-time">{fmtTime(e.time)}</span>
                    <span className={`cal-imp cal-imp--${imp}`} title={e.impact || ''} />
                    {f ? <img className="inst-flag cal-flag" src={f.src} alt={e.currency} title={e.currency} />
                       : <span className="cal-ccy">{e.currency}</span>}
                    <span className="cal-name">{e.event}</span>
                    <span className="cal-nums">
                      {hasValue(a)
                        ? <b className={beat ? `cal-a cal-a--${beat}` : 'cal-a'}>{a}</b>
                        : <b className="cal-a cal-a--pending">·</b>}
                      <em>{hasValue(fc) ? fc : '–'}</em>
                      <em className="cal-prev">{hasValue(e.previous) ? e.previous : '–'}</em>
                    </span>
                  </li>
                )
              })}
            </ul>
          )}
          <div className="cal-foot">
            <span className="cal-foot-note">High impact only</span>
            <span>actual</span><span>forecast</span><span>previous</span>
          </div>
        </div>
      )}
    </div>
  )
}
