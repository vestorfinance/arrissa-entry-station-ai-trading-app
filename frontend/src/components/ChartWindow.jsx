import { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import TradeChart from './TradeChart.jsx'
import Dropdown from './Dropdown.jsx'
import * as api from '../services/api.js'

const TFS = ['M5', 'M15', 'M30', 'H1', 'H4', 'D1']
const layoutKey = (id) => `arrissa.chart.${id}`
const readJSON = (k) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : null } catch { return null } }

// A detachable, draggable, resizable floating window showing a live chart for one
// instrument. Reuses TradeChart (tick-streamed candles + the account's trade lines).
// Its position and size are remembered (per id) across refreshes.
export default function ChartWindow({ id, symbol, account, index = 0, onClose }) {
  const saved = useRef(readJSON(layoutKey(id || `${account}:${symbol}`))).current
  const [spec, setSpec] = useState(null)
  const [timeframe, setTimeframe] = useState(saved?.timeframe || 'M15')
  const [sym, setSym] = useState(saved?.symbol || symbol)       // the instrument shown (editable)
  const [draft, setDraft] = useState(saved?.symbol || symbol)   // the input's working text
  const [err, setErr] = useState(null)

  function commitSym() {
    const v = (draft || '').trim().toUpperCase()
    if (v && v !== sym) setSym(v)
    setDraft(v || sym)
  }
  const [pos, setPos] = useState(() => (saved
    ? { left: saved.left, top: saved.top }
    : { left: 120 + index * 34, top: 96 + index * 34 }))
  const [size, setSize] = useState(() => (saved?.width ? { width: saved.width, height: saved.height } : null))
  const [mobile, setMobile] = useState(() => typeof window !== 'undefined'
    && window.matchMedia('(max-width: 860px)').matches)
  const ref = useRef(null)
  const drag = useRef(null)

  // on small screens the window is a full-width bottom sheet, not a floating box
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 860px)')
    const onChange = () => setMobile(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  // persist position/size/timeframe so a refresh restores this window as it was
  useEffect(() => {
    const key = layoutKey(id || `${account}:${symbol}`)
    localStorage.setItem(key, JSON.stringify({ ...pos, ...(size || {}), timeframe, symbol: sym }))
  }, [id, account, symbol, pos, size, timeframe, sym])

  // track the native corner-resize so React keeps (and re-applies) the chosen size
  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      setSize((s) => (s && s.width === el.offsetWidth && s.height === el.offsetHeight
        ? s : { width: el.offsetWidth, height: el.offsetHeight }))
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // fetch (and re-fetch on timeframe change) the initial candles + trade markers
  useEffect(() => {
    let cancelled = false
    setErr(null)
    api.chartData({ symbol: sym, timeframe, count: 150, account })
      .then((s) => { if (!cancelled) setSpec(s) })
      .catch((e) => { if (!cancelled) setErr(e.message) })
    return () => { cancelled = true }
  }, [sym, timeframe, account])

  // drag by the header (width/height are left to native CSS resize)
  function onDragStart(e) {
    if (mobile) return                 // no dragging the bottom sheet on mobile
    if (e.target.closest('button') || e.target.closest('select') || e.target.closest('input')) return
    const rect = ref.current.getBoundingClientRect()
    drag.current = { dx: e.clientX - rect.left, dy: e.clientY - rect.top }
    window.addEventListener('mousemove', onDragMove)
    window.addEventListener('mouseup', onDragEnd)
    e.preventDefault()
  }
  function onDragMove(e) {
    const { dx, dy } = drag.current || {}
    const el = ref.current
    if (!el) return
    const w = el.offsetWidth, h = el.offsetHeight
    const left = Math.max(8, Math.min(e.clientX - dx, window.innerWidth - w - 8))
    const top = Math.max(8, Math.min(e.clientY - dy, window.innerHeight - h - 8))
    setPos({ left, top })
  }
  function onDragEnd() {
    window.removeEventListener('mousemove', onDragMove)
    window.removeEventListener('mouseup', onDragEnd)
  }

  return (
    <div className={'chart-window' + (mobile ? ' chart-window--mobile' : '')} ref={ref}
         style={mobile ? undefined
           : { left: pos.left, top: pos.top, ...(size ? { width: size.width, height: size.height } : {}) }}>
      <div className="chart-window-head" onMouseDown={onDragStart}>
        <input className="chart-window-sym" value={draft} spellCheck={false}
               title="Change instrument — type a symbol and press Enter"
               onChange={(e) => setDraft(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter') { commitSym(); e.currentTarget.blur() } }}
               onBlur={commitSym} />
        <Dropdown className="chart-window-tf" value={timeframe} onChange={setTimeframe}
                  align="right" options={TFS.map((tf) => ({ value: tf, label: tf }))} />
        <button className="live-rowbtn" title="Close chart" onClick={onClose} style={{ marginLeft: 'auto' }}>
          <X size={16} strokeWidth={1.9} />
        </button>
      </div>
      <div className="chart-window-body">
        {err ? (
          <div className="chart-window-loading">{err}</div>
        ) : !spec ? (
          <div className="chart-window-loading">Loading chart…</div>
        ) : (
          <TradeChart spec={spec} key={`${sym}:${timeframe}`} />
        )}
      </div>
    </div>
  )
}
