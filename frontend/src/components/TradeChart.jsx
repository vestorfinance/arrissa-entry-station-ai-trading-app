import { useEffect, useRef, useState, useCallback } from 'react'
import { createChart, CandlestickSeries, LineStyle } from 'lightweight-charts'
import { LineChart as LineChartIcon, RefreshCw, Slash, Minus, Square, Ruler,
         ArrowUpRight, ArrowDownRight, Trash2, MousePointer2,
         Maximize2, Minimize2, ScanEye, Loader2 } from 'lucide-react'
import * as api from '../services/api.js'
import { attachDrawings, TOOLS } from './chartDrawings.js'
import { register as registerChart, analysed as chartAnalysed } from './chartRegistry.js'

// How long a chart streams before it auto-expires. After this it collapses to a
// lightweight placeholder — no chart, no WebSocket — so old charts left in a
// conversation stop costing the market-data server anything. Clicking the
// placeholder brings it back to life for another full window.
const LIFETIME_MS = 5 * 60 * 1000

// Drawings belong to an instrument, not to a message: a line drawn on the H1
// gold chart is still true the next time gold is charted. They are kept per
// symbol+timeframe so a 15-minute drawing does not clutter the daily.
const drawingsKey = (symbol, tf) => `chart_drawings:${symbol}:${tf}`
const loadDrawings = (symbol, tf) => {
  try { return JSON.parse(localStorage.getItem(drawingsKey(symbol, tf)) || '[]') } catch { return [] }
}
const saveDrawings = (symbol, tf, shapes) => {
  try {
    if (shapes.length) localStorage.setItem(drawingsKey(symbol, tf), JSON.stringify(shapes))
    else localStorage.removeItem(drawingsKey(symbol, tf))
  } catch { /* private mode / quota — drawings just won't persist */ }
}

const TOOLBAR = [
  { id: null,         Icon: MousePointer2,   title: 'Select — drag a drawing to move it, its handles to reshape' },
  { id: 'trendline',  Icon: Slash,           title: 'Trend line — drag from one point to the other' },
  { id: 'hray',       Icon: Minus,           title: 'Horizontal ray — drag (or click) to place a level' },
  { id: 'rect',       Icon: Square,          title: 'Rectangle / zone — drag out a box' },
  { id: 'fib',        Icon: Ruler,           title: 'Fib retracement — drag from the swing low to the high' },
  { id: 'long',       Icon: ArrowUpRight,    title: 'Long position — drag from entry down to the stop' },
  { id: 'short',      Icon: ArrowDownRight,  title: 'Short position — drag from entry up to the stop' },
]

// A live candlestick chart rendered inside a chat message.
//
// Candles come from the agent's show_chart tool result; the live candle is then
// updated tick-by-tick from /ws/ticks/{symbol} — the same Exness tick stream the
// positions panel uses — so the chart moves without polling.
//
// Streaming is strictly demand-driven: the socket is open ONLY while the chart is
// actually on screen and the tab is focused. Scroll it away (or switch tab) and
// the socket closes; bring it back and it re-reads the candles first, so it
// resumes from current price rather than from a stale bar. An old chart sitting
// far up a long conversation therefore costs the market-data server nothing.
//
// The account's own trades are drawn as price lines: entry, stop and target. A
// level outside the candles' own high/low band is NOT drawn, because auto-scaling
// to a distant stop flattens the price action; those are listed as labels instead.
const COLORS = {
  up: '#6ee7b7',
  down: '#fca5a5',
  entryBuy: '#6ee7b7',
  entrySell: '#fca5a5',
  sl: '#f87171',
  tp: '#34d399',
  pending: '#a5b4fc',
  text: '#8b8b8b',
  grid: 'rgba(255,255,255,0.05)',
}

export default function TradeChart({ spec }) {
  const holder = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)
  const lastRef = useRef(null)          // the live (rightmost) candle
  const wasActive = useRef(false)       // to detect the transition back into view
  const drawRef = useRef(null)          // the drawing layer, while a chart exists
  const barsRef = useRef([])            // the bars as drawn, for hit-testing drawn trades
  const regRef = useRef(null)           // this chart's entry in the registry

  const [data, setData] = useState(spec)     // current candles + trades (refreshed on focus)
  const [onScreen, setOnScreen] = useState(false)
  const [focused, setFocused] = useState(() => !document.hidden)
  const [live, setLive] = useState(false)
  const [bornAt, setBornAt] = useState(() => Date.now())  // reset by "revive"
  const [expired, setExpired] = useState(false)
  const [full, setFull] = useState(false)        // the chart, filling the screen
  const [tool, setTool] = useState(null)         // the drawing tool in hand
  const [drawCount, setDrawCount] = useState(0)  // how many shapes are on this chart
  const [seeing, setSeeing] = useState(false)    // a vision analysis of this chart is running

  // expired charts do no work: streaming, catch-up refetch and the chart itself
  // are all gated on `active`, which an expired chart can never be.
  const active = onScreen && focused && !expired
  const candles = data?.candles || []

  const revive = useCallback(() => setBornAt(Date.now()), [])

  // ── lifetime: expire LIFETIME_MS after creation (or the last revive) ──────────
  useEffect(() => {
    setExpired(false)
    const t = setTimeout(() => setExpired(true), LIFETIME_MS)
    return () => clearTimeout(t)
  }, [bornAt])

  // a genuinely different chart (chat reloaded from the server) replaces this one
  useEffect(() => { setData(spec) }, [spec])

  // ── is the chart actually being looked at? ───────────────────────────────────
  // Re-runs on `expired` so that after a revive we observe the freshly-mounted
  // canvas (a new DOM node) rather than the old detached one.
  useEffect(() => {
    const el = holder.current
    if (!el) return              // no canvas while expired — nothing to observe
    // ancestor clipping counts, so scrolling it out of the chat pane un-observes
    // it even though the pane itself is still in the viewport
    const io = new IntersectionObserver(
      ([entry]) => setOnScreen(entry.isIntersecting),
      { threshold: 0.15 },
    )
    io.observe(el)
    const onVis = () => setFocused(!document.hidden)
    document.addEventListener('visibilitychange', onVis)
    return () => { io.disconnect(); document.removeEventListener('visibilitychange', onVis) }
  }, [expired])

  // ── build the chart whenever the underlying data changes ─────────────────────
  // Depends on `expired` too: when the chart expires the canvas is unmounted, so
  // this re-runs, its cleanup removes the chart instance, and the body no-ops
  // (holder is gone). Reviving remounts the canvas and rebuilds from the data.
  useEffect(() => {
    if (expired || !holder.current || candles.length === 0) return

    const chart = createChart(holder.current, {
      layout: { background: { color: 'transparent' }, textColor: COLORS.text, fontSize: 11 },
      grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
      handleScale: { axisPressedMouseMove: false },
      height: 320,
      autoSize: true,
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: COLORS.up, downColor: COLORS.down,
      wickUpColor: COLORS.up, wickDownColor: COLORS.down,
      borderVisible: false,
    })

    const bars = candles.map((c) => ({
      time: Math.floor(c.epoch_ms / 1000),
      open: c.open, high: c.high, low: c.low, close: c.close,
    }))
    series.setData(bars)
    barsRef.current = bars
    chart.timeScale().fitContent()

    const line = (price, color, style, title) =>
      series.createPriceLine({ price, color, lineWidth: 1, lineStyle: style,
                               axisLabelVisible: true, title })

    // Draw trade entries per SIDE so many trades don't stack dozens of axis labels.
    // A single trade keeps its exact entry + SL/TP; multiple collapse to one
    // volume-weighted line labelled "BUY ×N" / "SELL ×N".
    for (const side of ['buy', 'sell']) {
      const arr = (data.trades || []).filter((t) => t.side === side)
      if (!arr.length) continue
      const color = side === 'buy' ? COLORS.entryBuy : COLORS.entrySell
      if (arr.length === 1) {
        const t = arr[0]
        if (t.open_price?.in_range) line(t.open_price.price, color, LineStyle.Solid, `${side.toUpperCase()} ${t.volume}`)
        if (t.sl?.in_range) line(t.sl.price, COLORS.sl, LineStyle.Dashed, 'SL')
        if (t.tp?.in_range) line(t.tp.price, COLORS.tp, LineStyle.Dashed, 'TP')
      } else {
        const shown = arr.filter((t) => t.open_price?.in_range)
        const wsum = shown.reduce((s, t) => s + (t.volume || 1), 0)
        if (shown.length && wsum > 0) {
          const avg = shown.reduce((s, t) => s + t.open_price.price * (t.volume || 1), 0) / wsum
          line(avg, color, LineStyle.Solid, `${side.toUpperCase()} ×${arr.length}`)
        }
      }
    }
    for (const o of data.orders || []) {
      if (o.price?.in_range) line(o.price.price, COLORS.pending, LineStyle.Dotted, `${o.side.toUpperCase()} pending`)
      if (o.sl?.in_range) line(o.sl.price, COLORS.sl, LineStyle.Dotted, 'SL')
      if (o.tp?.in_range) line(o.tp.price, COLORS.tp, LineStyle.Dotted, 'TP')
    }

    // Drawings sit on top of the series and outlive this particular render:
    // they are stored per instrument and reloaded here.
    const symbol = data.symbol, tf = data.timeframe
    const layer = attachDrawings(chart, series, {
      initial: loadDrawings(symbol, tf),
      trades: data.trades || [],
      onChange: (shapes) => { saveDrawings(symbol, tf, shapes); setDrawCount(shapes.length) },
      onToolDone: () => setTool(null),   // the shape is down; un-light the button
      // The live candle is updated tick by tick, so the position's diagonal
      // tracks price without the drawing layer needing its own feed.
      lastPrice: () => lastRef.current?.close ?? null,
      bars: () => barsRef.current,       // to see whether a drawn trade already finished
    })
    drawRef.current = layer
    setDrawCount(layer.count())

    chartRef.current = chart
    seriesRef.current = series
    lastRef.current = bars[bars.length - 1]

    return () => {
      layer.destroy()
      drawRef.current = null
      chart.remove(); chartRef.current = null; seriesRef.current = null
    }
  }, [data, expired])

  // ── the assistant can be asked to LOOK at this chart ─────────────────────────
  // Registered so the composer can photograph whichever chart is in front of
  // the user when they ask. takeScreenshot() captures the library's own canvas,
  // and the drawing layer paints into it through the primitives API, so their
  // lines are in the picture — which is the entire point.
  useEffect(() => {
    if (!data?.symbol) return
    const h = registerChart({
      symbol: data.symbol,
      timeframe: data.timeframe,
      drawings: drawCount,
      visible: active,
      shot: () => chartRef.current?.takeScreenshot() || null,
    })
    regRef.current = h
    return () => { h.remove(); regRef.current = null }
  }, [data?.symbol, data?.timeframe])

  useEffect(() => { regRef.current?.update({ drawings: drawCount, visible: active }) },
           [drawCount, active])

  useEffect(() => { drawRef.current?.setTool(tool) }, [tool, data, expired])

  // Full screen: an overlay rather than the Fullscreen API, because this chart
  // lives inside a scrolling conversation and the native call would take the
  // whole document with it. Escape closes it, and the body is frozen while it is
  // open so the chat does not scroll away underneath.
  useEffect(() => {
    if (!full) return
    const onKey = (e) => { if (e.key === 'Escape') setFull(false) }
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = prev
      window.removeEventListener('keydown', onKey)
    }
  }, [full])

  // ── coming back into view: catch up on what moved while we were paused ───────
  useEffect(() => {
    if (!active) { wasActive.current = false; return }
    if (wasActive.current) return          // already active — nothing to catch up on
    wasActive.current = true

    let cancelled = false
    api.chartData({
      symbol: spec.symbol, timeframe: spec.timeframe,
      count: spec.count || spec.candles?.length || 150, account: spec.account,
    })
      .then((fresh) => { if (!cancelled && fresh?.candles?.length) setData(fresh) })
      .catch(() => { /* keep showing what we have */ })
    return () => { cancelled = true }
  }, [active, spec])

  // ── ticks only while the chart is being looked at ────────────────────────────
  // Keyed on symbol/timeframe rather than the whole data object, so a candle
  // refresh doesn't needlessly tear down and reopen a healthy socket.
  const streamSymbol = data?.symbol
  const streamTf = data?.timeframe_minutes
  const hasCandles = candles.length > 0

  useEffect(() => {
    if (!active || !streamSymbol || !hasCandles) {
      setLive(false)
      return
    }
    const token = localStorage.getItem('auth_token')
    if (!token) return

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${window.location.host}/ws/ticks/${encodeURIComponent(streamSymbol)}?token=${encodeURIComponent(token)}`
    let ws
    try {
      ws = new WebSocket(url)
    } catch {
      return
    }

    const tfSeconds = (streamTf || 15) * 60

    ws.onopen = () => setLive(true)
    ws.onclose = () => setLive(false)
    ws.onerror = () => setLive(false)
    ws.onmessage = (e) => {
      let msg
      try { msg = JSON.parse(e.data) } catch { return }
      const price = msg.bid
      if (price == null || !seriesRef.current || !lastRef.current) return

      const nowBucket = Math.floor(Date.now() / 1000 / tfSeconds) * tfSeconds
      const last = lastRef.current
      const bar = nowBucket > last.time
        ? { time: nowBucket, open: price, high: price, low: price, close: price }
        : { ...last, high: Math.max(last.high, price), low: Math.min(last.low, price), close: price }
      lastRef.current = bar
      try { seriesRef.current.update(bar) } catch { /* chart torn down */ }
    }

    return () => { setLive(false); try { ws.close() } catch { /* already closed */ } }
  }, [active, streamSymbol, streamTf, hasCandles])

  if (candles.length === 0) return null

  // ── "Analyse chart": a vision read of THIS picture ───────────────────────────
  // Deliberately not routed through the assistant. Asked in words, "analyse the
  // chart" is a sentence it can reasonably hear as "analyse this market", and it
  // did. A button carries the chart it is attached to, so there is nothing left
  // to interpret.
  async function analyseThis() {
    if (seeing) return
    const canvas = chartRef.current?.takeScreenshot()
    if (!canvas) return
    setSeeing(true)
    try {
      const out = await api.analyseChart({
        symbol: data.symbol, timeframe: data.timeframe, drawings: drawCount,
        png: canvas.toDataURL('image/png'),
      })
      chartAnalysed({ ...out, symbol: data.symbol, timeframe: data.timeframe,
                      drawings: drawCount })
    } catch (e) {
      chartAnalysed({ symbol: data.symbol, timeframe: data.timeframe,
                      drawings: drawCount, error: e.message })
    } finally {
      setSeeing(false)
    }
  }

  // ── expired: a lightweight, clickable placeholder (no chart, no socket) ───────
  if (expired) {
    return (
      <div className="tchart tchart--expired" role="button" tabIndex={0}
           onClick={revive}
           onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); revive() } }}
           title="Click to reload this chart for another 5 minutes"
           style={{ cursor: 'pointer' }}>
        <div className="tchart-head">
          <LineChartIcon size={14} strokeWidth={1.75} style={{ opacity: 0.6 }} />
          <span className="tchart-sym">{data.symbol}</span>
          <span className="tchart-tf">{data.timeframe}</span>
          <span className="pill pill--muted">expired</span>
        </div>
        <div className="tchart-expired-body"
             style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
                      height: 96, color: 'var(--muted, #8b8b8b)', fontSize: 13 }}>
          <RefreshCw size={15} strokeWidth={1.75} />
          <span>Chart expired to save resources · click to reload live for 5 min</span>
        </div>
      </div>
    )
  }

  const offRange = []
  for (const t of data.trades || []) {
    if (t.open_price && !t.open_price.in_range) offRange.push(`entry ${t.open_price.price}`)
    if (t.sl && !t.sl.in_range) offRange.push(`SL ${t.sl.price}`)
    if (t.tp && !t.tp.in_range) offRange.push(`TP ${t.tp.price}`)
  }

  return (
    <div className={'tchart' + (full ? ' tchart--full' : '')}>
      <div className="tchart-head">
        <span className="tchart-sym">{data.symbol}</span>
        <span className="tchart-tf">{data.timeframe}</span>
        <span className={`pill ${live ? 'pill--ok' : 'pill--muted'}`}
              title={live ? 'Streaming ticks' : 'Paused — not on screen'}>
          {live ? 'live' : 'paused'}
        </span>
        {(data.trades || []).length > 0 && (() => {
          const trades = data.trades
          const buys = trades.filter((t) => t.side === 'buy')
          const sells = trades.filter((t) => t.side === 'sell')
          const net = (arr) => arr.reduce((s, t) => s + (t.profit || 0), 0)
          const fmt = (n) => (n >= 0 ? '+' : '') + Math.round(n)
          return (
            <span className="tchart-trades">
              {buys.length > 0 && (
                <span className="tchart-trade tchart-trade--buy">
                  {buys.length} buy{buys.length > 1 ? 's' : ''}<b>{' '}{fmt(net(buys))}</b>
                </span>
              )}
              {sells.length > 0 && (
                <span className="tchart-trade tchart-trade--sell">
                  {sells.length} sell{sells.length > 1 ? 's' : ''}<b>{' '}{fmt(net(sells))}</b>
                </span>
              )}
            </span>
          )
        })()}
        <button type="button" className="tchart-see-btn" onClick={analyseThis}
                disabled={seeing}
                title={drawCount
                  ? `Read this chart — your ${drawCount} drawing${drawCount > 1 ? 's' : ''} included`
                  : 'Read this chart exactly as it looks now'}>
          {seeing ? <Loader2 size={14} strokeWidth={1.9} className="tchart-spin" />
                  : <ScanEye size={14} strokeWidth={1.9} />}
          <span>{seeing ? 'Reading…' : 'Analyse chart'}</span>
        </button>
        <button type="button" className="tchart-full-btn"
                title={full ? 'Exit full screen (Esc)' : 'Full screen'}
                onClick={() => setFull((f) => !f)}>
          {full ? <Minimize2 size={15} strokeWidth={1.9} /> : <Maximize2 size={15} strokeWidth={1.9} />}
        </button>
      </div>
      <div className="tchart-tools" role="toolbar" aria-label="Drawing tools">
        {TOOLBAR.map(({ id, Icon, title }) => (
          <button key={id || 'select'} type="button" title={title}
                  className={'tchart-tool' + (tool === id ? ' tchart-tool--on' : '')}
                  onClick={() => setTool((cur) => (cur === id ? null : id))}>
            <Icon size={14} strokeWidth={1.9} />
          </button>
        ))}
        <span className="tchart-tools-sep" />
        <button type="button" className="tchart-tool" title="Delete the selected drawing (Del)"
                disabled={drawCount === 0}
                onClick={() => { if (!drawRef.current?.deleteSelected()) drawRef.current?.clear() }}>
          <Trash2 size={14} strokeWidth={1.9} />
        </button>
        {tool && (
          <span className="tchart-tools-hint">
            {TOOLS[tool].label} — {TOOLS[tool].hint} · Esc to cancel
          </span>
        )}
      </div>
      <div className="tchart-canvas" ref={holder} />
      {offRange.length > 0 && (
        <p className="tchart-note">Outside the visible range: {offRange.join(' · ')}</p>
      )}
    </div>
  )
}
