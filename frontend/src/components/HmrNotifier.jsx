import { useEffect, useRef, useState } from 'react'
import { ShieldAlert, ShieldCheck, X } from 'lucide-react'
import { useAuth } from '../context/AuthContext.jsx'
import * as api from '../services/api.js'
import { useModule } from '../services/capabilities.js'

// Polls HMR alerts for the user's open positions and pops bottom-left toasts when
// a High Margin Requirement window is about to apply, is active, or has just lifted.
const POLL_MS = 60000

const fmtTime = (iso) => {
  try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) } catch { return '' }
}
const fmtMins = (sec) => {
  const m = Math.max(0, Math.round((sec || 0) / 60))
  return m <= 1 ? 'under a minute' : `~${m} min`
}

export default function HmrNotifier() {
  // HMR is a module. Polling its endpoint when it is not installed is a 404
  // every 60 seconds, forever, for an answer we already have.
  const hasHmr = useModule('hmr')
  const { user } = useAuth()
  const [toasts, setToasts] = useState([])
  const shown = useRef(new Set())        // keys already toasted (dedupe across polls)
  const timers = useRef({})

  const remove = (key) => {
    setToasts((ts) => ts.filter((x) => x.key !== key))
    if (timers.current[key]) { clearTimeout(timers.current[key]); delete timers.current[key] }
  }

  useEffect(() => {
    if (!user || !hasHmr) return
    let alive = true
    const push = (t) => {
      setToasts((ts) => [...ts, t])
      timers.current[t.key] = setTimeout(() => remove(t.key), 15000)
    }
    async function poll() {
      let a
      try { a = await api.getHmrAlerts() } catch { return }
      if (!alive || !a) return
      const nextTxt = a.next ? ` Next HMR ${fmtTime(a.next.start)}.` : ''
      const once = (key, toast) => { if (!shown.current.has(key)) { shown.current.add(key); push({ key, ...toast }) } }
      for (const p of a.applying_soon || [])
        once(`soon:${p.id}`, { tone: 'warn', title: 'HMR applying soon',
          body: `${(p.symbols || []).join(', ')} — leverage capped 1:${p.leverage} in ${fmtMins(p.in_seconds)}. Lifts ${fmtTime(p.end)}.` })
      for (const p of a.active || [])
        once(`active:${p.id}`, { tone: 'warn', title: 'HMR active now',
          body: `${(p.symbols || []).join(', ')} — leverage capped 1:${p.leverage}. Lifts ${fmtTime(p.lifts_at)}.` })
      for (const p of a.lifted_recently || [])
        once(`lifted:${p.id}`, { tone: 'ok', title: 'HMR lifted',
          body: `${(p.symbols || []).join(', ')} back to normal leverage.${nextTxt}` })
    }
    poll()
    const iv = setInterval(poll, POLL_MS)
    return () => {
      alive = false
      clearInterval(iv)
      Object.values(timers.current).forEach(clearTimeout)
      timers.current = {}
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, hasHmr])

  if (!user || toasts.length === 0) return null
  return (
    <div className="hmr-toasts">
      {toasts.map((t) => (
        <div key={t.key} className={`hmr-toast hmr-toast--${t.tone}`}>
          <span className="hmr-toast-icon">
            {t.tone === 'ok' ? <ShieldCheck size={18} strokeWidth={1.9} /> : <ShieldAlert size={18} strokeWidth={1.9} />}
          </span>
          <div className="hmr-toast-main">
            <div className="hmr-toast-title">{t.title}</div>
            <div className="hmr-toast-body">{t.body}</div>
          </div>
          <button className="hmr-toast-x" onClick={() => remove(t.key)} title="Dismiss"><X size={14} /></button>
        </div>
      ))}
    </div>
  )
}
