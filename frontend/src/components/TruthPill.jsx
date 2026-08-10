import { useEffect, useMemo, useRef, useState } from 'react'
import { X, ArrowRight, Megaphone } from 'lucide-react'
import * as api from '../services/api.js'
import { useAuth } from '../context/AuthContext.jsx'
import { useModule } from '../services/capabilities.js'
import { ask, promptForAlert } from '../services/askAgent.js'

// A market-moving Truth Social post, on screen the moment it lands.
//
// Collapsed it is just his face, parked wherever you left it. When something
// market-moving arrives it opens itself and pulses — the one thing this is for
// is the case where you were not looking at the app.
//
// Closing it is honoured for the post you closed, not for ever: dismissing
// today's post must not silence tomorrow's, which is the difference between a
// control and a mute button nobody remembers pressing.
const POS_KEY = 'arrissa.potus.pos'
const SEEN_KEY = 'arrissa.potus.seen'      // the last post id dismissed
const POLL_MS = 60000
const WAVE_MS = 45000                      // how long it keeps pulsing after arriving

const readJSON = (k, fb) => {
  try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : fb } catch { return fb }
}
const ago = (iso) => {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 90) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

export default function TruthPill() {
  const has = useModule('truth-social')
  const { user } = useAuth()
  const [post, setPost] = useState(null)
  const [openCard, setOpenCard] = useState(false)
  const [waving, setWaving] = useState(false)
  const [pos, setPos] = useState(() => readJSON(POS_KEY, null))
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(SEEN_KEY) || '')
  const [mobile, setMobile] = useState(
    () => typeof window !== 'undefined' && window.innerWidth <= 860)
  const cardRef = useRef(null)
  const dragRef = useRef(null)
  const dragged = useRef(false)
  const waveTimer = useRef(null)

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 860px)')
    const on = (e) => setMobile(e.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])

  useEffect(() => { if (pos) localStorage.setItem(POS_KEY, JSON.stringify(pos)) }, [pos])

  useEffect(() => {
    if (!has || !user) return
    let dead = false
    const load = () => api.truthLatest(24, 'high', 1)
      .then((r) => {
        const p = (r.posts || [])[0]
        if (dead || !p) return
        setPost((prev) => {
          // A DIFFERENT post is the event. It opens itself and pulses, even if
          // the last one was closed — that dismissal was about that post.
          if (!prev || prev.post_id !== p.post_id) {
            if (p.post_id !== dismissed) {
              setOpenCard(true)
              setWaving(true)
              clearTimeout(waveTimer.current)
              waveTimer.current = setTimeout(() => setWaving(false), WAVE_MS)
            }
          }
          return p
        })
      })
      .catch(() => { /* module absent or offline — the pill just stays quiet */ })
    load()
    const t = setInterval(load, POLL_MS)
    return () => { dead = true; clearInterval(t); clearTimeout(waveTimer.current) }
  }, [has, user, dismissed])

  function onDragStart(e) {
    if (mobile) return
    if (e.target.closest('button')) return
    dragged.current = false
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
    dragged.current = true
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

  const style = useMemo(
    () => (!mobile && pos ? { left: pos.left, top: pos.top, right: 'auto', bottom: 'auto' } : undefined),
    [mobile, pos])

  if (!has || !user || !post) return null

  const close = () => {
    setOpenCard(false)
    setWaving(false)
    setDismissed(post.post_id)
    localStorage.setItem(SEEN_KEY, post.post_id)
  }

  const analyse = () => {
    ask(promptForAlert({
      kind: 'truth',
      title: `${post.handle || 'realDonaldTrump'} — market-moving post`,
      body: post.content,
      country: 'us',
      at: post.datetime,
    }))
    setWaving(false)
  }

  return (
    <div className={'potus' + (openCard ? ' potus--open' : '') + (waving ? ' potus--wave' : '')}
         ref={cardRef} style={style} onMouseDown={onDragStart}>
      <button className="potus-face"
              title={openCard ? 'Hide the post' : 'A market-moving post'}
              onClick={() => { if (!dragged.current) setOpenCard((v) => !v) }}>
        {/* Three rings, offset in time, so it reads as a wave leaving the pill
            rather than one border blinking. */}
        <span className="potus-ring" /><span className="potus-ring" /><span className="potus-ring" />
        <img src="/img/potus.jpg" alt="" draggable="false" />
      </button>

      {openCard && (
        <div className="potus-card">
          <div className="potus-card-head">
            <Megaphone size={13} strokeWidth={2} />
            <span>Market-moving · {ago(post.datetime)}</span>
            <button className="potus-x" onClick={close} title="Dismiss">
              <X size={13} strokeWidth={2.4} />
            </button>
          </div>
          <p className="potus-text">{post.content}</p>
          {post.impact_reason ? <p className="potus-why">{post.impact_reason}</p> : null}
          <button className="potus-ask" onClick={analyse}>
            Analyse this <ArrowRight size={12} strokeWidth={2.2} />
          </button>
        </div>
      )}
    </div>
  )
}
