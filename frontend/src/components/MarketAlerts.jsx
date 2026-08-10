import { useEffect, useRef, useState } from 'react'
import { Newspaper, CalendarClock, Megaphone, BarChart3, X, Volume2, VolumeX } from 'lucide-react'
import { useAuth } from '../context/AuthContext.jsx'
import * as api from '../services/api.js'
import { flagsFor, countryFlag } from '../data/flags.js'

// Toasts for things that HAPPEN: a market-moving Truth post, a high-impact news
// story, a big economic release five minutes out, and that release printing.
//
// The watermark is ours, not the server's: we send back the `now` the server
// gave us last time, so a browser opened an hour later still gets what it
// missed. A pure broadcast would have lost it.
const POLL_MS = 30000
const SHOW_MS = 20000
const MUTE_KEY = 'market_alerts_muted'
const SEEN_KEY = 'market_alerts_since'

const ICON = {
  truth: Megaphone,
  news: Newspaper,
  calendar_soon: CalendarClock,
  calendar_out: BarChart3,
}

// Browsers refuse to play audio until the page has been interacted with. Rather
// than let the first alert fail silently, the element is primed on the first
// click or key anywhere — by which time an alert can actually be heard.
function useChime(muted) {
  const ready = useRef(false)
  const cache = useRef({})

  useEffect(() => {
    const prime = () => {
      ready.current = true
      window.removeEventListener('pointerdown', prime)
      window.removeEventListener('keydown', prime)
    }
    window.addEventListener('pointerdown', prime)
    window.addEventListener('keydown', prime)
    return () => {
      window.removeEventListener('pointerdown', prime)
      window.removeEventListener('keydown', prime)
    }
  }, [])

  return (name) => {
    if (muted || !ready.current) return
    try {
      const src = `/sounds/${name === 'alert' ? 'alert' : 'notice'}.wav`
      let a = cache.current[src]
      if (!a) { a = new Audio(src); a.volume = 0.5; cache.current[src] = a }
      a.currentTime = 0
      a.play().catch(() => {})     // a refusal is not worth surfacing
    } catch { /* no audio on this device */ }
  }
}

function Flags({ alert }) {
  // What the alert is ABOUT, in pictures: the instruments if it named any,
  // otherwise the country whose number or politics moved.
  const marks = []
  for (const s of (alert.symbols || []).slice(0, 3)) {
    for (const f of flagsFor(s)) marks.push({ ...f, key: `${s}:${f.src}` })
  }
  if (!marks.length && alert.country) {
    const c = countryFlag(alert.country)
    if (c) marks.push({ ...c, key: c.src })
  }
  if (!marks.length) return null
  return (
    <span className="ma-flags">
      {marks.map((m) => <img key={m.key} className="ma-flag" src={m.src} alt={m.label} title={m.label} />)}
    </span>
  )
}

export default function MarketAlerts() {
  const { user } = useAuth()
  const [toasts, setToasts] = useState([])
  const [muted, setMuted] = useState(() => localStorage.getItem(MUTE_KEY) === '1')
  const sinceRef = useRef(localStorage.getItem(SEEN_KEY) || '')
  const timers = useRef({})
  const chime = useChime(muted)

  const remove = (key) => {
    setToasts((ts) => ts.filter((t) => t.key !== key))
    if (timers.current[key]) { clearTimeout(timers.current[key]); delete timers.current[key] }
  }

  useEffect(() => {
    if (!user) return
    let alive = true

    async function poll() {
      let r
      try { r = await api.marketAlerts(sinceRef.current) } catch { return }
      if (!alive || !r) return

      // First run on a fresh browser: take the watermark and show nothing. Three
      // days of backlog arriving at once is noise, not news.
      const first = !sinceRef.current
      if (r.now) { sinceRef.current = r.now; localStorage.setItem(SEEN_KEY, r.now) }
      if (first || !r.alerts?.length) return

      let loudest = null
      for (const a of r.alerts) {
        setToasts((ts) => (ts.some((t) => t.key === a.key) ? ts : [...ts, a]))
        timers.current[a.key] = setTimeout(() => remove(a.key), SHOW_MS)
        if (a.sound === 'alert') loudest = 'alert'
        else loudest = loudest || 'notice'
      }
      if (loudest) chime(loudest)
    }

    poll()
    const iv = setInterval(poll, POLL_MS)
    return () => { alive = false; clearInterval(iv) }
  }, [user, muted])

  if (!user || !toasts.length) return null

  return (
    <div className="ma-wrap" role="status" aria-live="polite">
      {toasts.map((a) => {
        const Icon = ICON[a.kind] || Megaphone
        return (
          <div key={a.key} className={'ma-toast' + (a.sound === 'alert' ? ' ma-toast--hot' : '')}>
            <div className="ma-head">
              <Icon size={15} strokeWidth={1.9} />
              <Flags alert={a} />
              <span className="ma-title">{a.title}</span>
              <button className="ma-x" onClick={() => remove(a.key)} title="Dismiss">
                <X size={14} strokeWidth={2} />
              </button>
            </div>
            {a.body ? <p className="ma-body">{a.body}</p> : null}
            <div className="ma-foot">
              {a.url
                ? <a className="ma-link" href={a.url} target="_blank" rel="noreferrer">Read it</a>
                : <span />}
              <button
                className="ma-mute"
                title={muted ? 'Alert sounds are off' : 'Alert sounds are on'}
                onClick={() => {
                  const v = !muted
                  setMuted(v)
                  localStorage.setItem(MUTE_KEY, v ? '1' : '0')
                }}>
                {muted ? <VolumeX size={13} strokeWidth={1.9} /> : <Volume2 size={13} strokeWidth={1.9} />}
              </button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
