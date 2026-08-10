// Drawing tools for Lightweight Charts.
//
// The library renders series and nothing else — no trendlines, no boxes, no
// Fibonacci, no position tool. What it does give us is the v5 primitives API: a
// hook to paint our own canvas in the series' own coordinate space. Everything
// here is built on that one hook, so nothing needs a licensed library.
//
// Three decisions shape the whole file:
//
//  1. A point is stored as {logical, time, price}. LOGICAL is the bar index and
//     it keeps counting past the last bar, so a line can be drawn — and stay —
//     out in the empty space to the right of price, where `time` does not exist
//     and time-based anchoring simply fails. `time` is kept alongside it as the
//     stable anchor for points that DO sit on a bar, because a refetch can shift
//     indices while times stay put. At paint time we prefer time and fall back
//     to logical, which is right in both directions.
//
//  2. Drawing is DRAG, not click-click. You press, pull the shape out, and let
//     go — one gesture, no stray clicks landing on the chart between points.
//
//  3. The chart is moved by SCROLLING, never by dragging. Press-and-drag belongs
//     to the drawings: it creates them, moves them, and reshapes them by their
//     handles. Wheel and pinch still pan and zoom exactly as before.
//
// Everything is editable after the fact: click to select, drag the body to move
// it, drag a handle to reshape it, Del to remove it.

export const TOOLS = {
  trendline: { label: 'Trend line',      hint: 'drag from one point to the other' },
  hray:      { label: 'Horizontal ray',  hint: 'drag to place a level' },
  rect:      { label: 'Rectangle / zone', hint: 'drag out a box' },
  fib:       { label: 'Fib retracement', hint: 'drag from the swing low to the high' },
  long:      { label: 'Long position',   hint: 'drag from entry down to the stop' },
  short:     { label: 'Short position',  hint: 'drag from entry up to the stop' },
}

const C = {
  line:     '#60a5fa',
  fib:      '#c084fc',
  profit:   '#34d399',
  loss:     '#f87171',
  entry:    '#e5e7eb',
  selected: '#fbbf24',
  text:     '#d1d5db',
  handle:   '#fbbf24',
}

const FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]
const HANDLE = 4          // half-size of a drag handle, in pixels
const GRAB = 7            // how close counts as grabbing something
const DEFAULT_RR = 2      // a fresh position tool targets 2R, then you drag it
const DEFAULT_BARS = 24   // how wide a new position box starts, in bars

let _seq = 0
const nextId = () => `d${Date.now().toString(36)}${(_seq++).toString(36)}`

function distToSegment(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1
  const len2 = dx * dx + dy * dy
  if (len2 === 0) return Math.hypot(px - x1, py - y1)
  const t = Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / len2))
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
}

// Type that follows the chart. A chart in a chat message is a third the height
// of a full-screen one, and 10px is small there and mean here — so the size is
// taken from the pane and clamped to what stays legible either way.
function fontFor(height) {
  return Math.max(9, Math.min(14, Math.round(height / 26)))
}

function label(ctx, text, x, y, color, { size = 10, bold = false, plate = true } = {}) {
  ctx.font = `${bold ? '600 ' : ''}${size}px -apple-system, system-ui, sans-serif`
  const pad = Math.round(size * 0.4)
  if (plate) {
    const w = ctx.measureText(text).width + pad * 2
    ctx.fillStyle = 'rgba(0,0,0,0.55)'
    ctx.fillRect(x, y - size / 2 - 2, w, size + 4)
  }
  ctx.fillStyle = color
  ctx.fillText(text, x + (plate ? pad : 0), y + 1)
}

const digitsFor = (v) => (Math.abs(v) >= 1000 ? 2 : Math.abs(v) >= 10 ? 3 : 5)

/**
 * Attach a drawing layer to a chart + series pair.
 *
 * The caller owns persistence: every change arrives through `onChange(shapes)`,
 * and whatever it stored comes back through `initial`.
 */
export function attachDrawings(chart, series,
                               { initial = [], onChange = () => {}, onToolDone = () => {},
                                 trades = [], lastPrice = () => null,
                                 onTradeLevel = () => {},
                                 proposal = null,
                                 bars = () => [] } = {}) {
  let shapes = normalise(initial)
  let tool = null            // the armed tool, if any
  let draft = null           // the shape being dragged into existence
  let drag = null            // { id, handle, from, orig } while moving/reshaping
  let selected = null
  let requestUpdate = () => {}

  const ts = () => chart.timeScale()
  const priceY = (p) => (p == null ? null : series.priceToCoordinate(p))
  const yPrice = (y) => series.coordinateToPrice(y)

  // x for a stored point: its bar if it has one, otherwise its index — which is
  // what carries a drawing out past the last candle.
  function xOf(pt) {
    if (!pt) return null
    if (pt.time != null) {
      const x = ts().timeToCoordinate(pt.time)
      if (x != null) return x
    }
    return pt.logical == null ? null : ts().logicalToCoordinate(pt.logical)
  }

  function pointAt(x, y) {
    const logical = ts().coordinateToLogical(x)
    const price = yPrice(y)
    if (logical == null || price == null) return null
    return { logical, time: ts().coordinateToTime(x), price }
  }

  // Shapes written by an older version stored only {time, price}; give them a
  // logical so they keep working instead of vanishing.
  function normalise(list) {
    return (Array.isArray(list) ? list : []).map((s) => {
      const pts = (s.pts || []).map((p) => ({
        logical: p.logical ?? null, time: p.time ?? null, price: p.price }))
      // Positions drawn before boxes had a right edge kept only their entry.
      // Give them one, so they gain the handle instead of running off forever.
      if (s.kind === 'position' && pts.length < 2 && pts[0]) {
        pts.push({ logical: pts[0].logical == null ? null : pts[0].logical + DEFAULT_BARS,
                   time: null, price: pts[0].price })
      }
      return { ...s, pts }
    })
  }

  // ── what a shape's control points are ──────────────────────────────────────
  // Each handle is {key, x, y}; dragging one is defined by `moveHandle` below.
  function handlesOf(s) {
    const out = []
    if (s.kind === 'position') {
      const x = xOf(s.pts[0]) ?? 8
      for (const key of ['entry', 'sl', 'tp']) {
        const y = priceY(s[key])
        if (y != null) out.push({ key, x: x + 14, y })
      }
      const xEnd = xOf(s.pts[1])
      const yE = priceY(s.entry)
      if (xEnd != null && yE != null) out.push({ key: 'end', x: xEnd, y: yE })
      return out
    }
    s.pts.forEach((p, i) => {
      const x = xOf(p), y = priceY(p.price)
      if (x != null && y != null) out.push({ key: `p${i}`, x, y })
    })
    return out
  }

  function moveHandle(s, key, pt) {
    if (s.kind === 'position') {
      if (key === 'end') {          // only the width moves; the prices stay put
        s.pts[1] = { logical: pt.logical, time: pt.time, price: s.entry }
        return
      }
      if (key === 'entry') {
        const dp = pt.price - s.entry
        s.entry = pt.price
        if (s.sl != null) s.sl += dp
        if (s.tp != null) s.tp += dp
        s.pts[0] = { ...pt }
      } else {
        s[key] = pt.price
      }
      return
    }
    const i = Number(key.slice(1))
    s.pts[i] = { ...pt }
  }

  function movedBy(s, dLogical, dPrice) {
    const next = { ...s, pts: s.pts.map((p) => ({
      logical: p.logical == null ? null : p.logical + dLogical,
      time: null,                       // the bar it sat on is no longer the one it sits on
      price: p.price + dPrice,
    })) }
    if (s.kind === 'position') {
      next.entry = s.entry + dPrice
      if (s.sl != null) next.sl = s.sl + dPrice
      if (s.tp != null) next.tp = s.tp + dPrice
    }
    return next
  }

  // ── painting ───────────────────────────────────────────────────────────────
  function drawShape(ctx, s, width, isSelected, font = 10) {
    const xs = s.pts.map(xOf)
    const ys = s.pts.map((p) => priceY(p.price))
    const stroke = isSelected ? C.selected : (s.color || C.line)

    ctx.save()
    ctx.lineWidth = isSelected ? 2 : 1.5
    ctx.strokeStyle = stroke
    ctx.setLineDash([])

    if (s.kind === 'trendline' && xs[0] != null && xs[1] != null && ys[0] != null && ys[1] != null) {
      ctx.beginPath(); ctx.moveTo(xs[0], ys[0]); ctx.lineTo(xs[1], ys[1]); ctx.stroke()
    } else if (s.kind === 'hray' && ys[0] != null) {
      const x0 = xs[0] ?? 0
      ctx.beginPath(); ctx.moveTo(x0, ys[0]); ctx.lineTo(width, ys[0]); ctx.stroke()
      // No price written across the line: it goes on the price axis, where every
      // other price on this chart already is.
    } else if (s.kind === 'rect' && xs[0] != null && xs[1] != null && ys[0] != null && ys[1] != null) {
      const x = Math.min(xs[0], xs[1]), y = Math.min(ys[0], ys[1])
      ctx.fillStyle = 'rgba(96,165,250,0.10)'
      ctx.fillRect(x, y, Math.abs(xs[1] - xs[0]), Math.abs(ys[1] - ys[0]))
      ctx.strokeRect(x, y, Math.abs(xs[1] - xs[0]), Math.abs(ys[1] - ys[0]))
    } else if (s.kind === 'fib' && ys[0] != null && ys[1] != null) {
      const [a, b] = s.pts
      const x1 = Math.min(xs[0] ?? 0, xs[1] ?? width)
      const x2 = Math.max(xs[0] ?? 0, xs[1] ?? width)
      for (const lvl of FIB) {
        const price = a.price + (b.price - a.price) * lvl
        const y = priceY(price)
        if (y == null) continue
        ctx.strokeStyle = isSelected ? C.selected : C.fib
        ctx.globalAlpha = lvl === 0 || lvl === 1 ? 0.9 : 0.55
        ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(Math.max(x2, x1 + 40), y); ctx.stroke()
        ctx.globalAlpha = 1
        // The ratio, and only the ratio — sat on the left end of its own line
        // and vertically centred on it. The price is on the axis; repeating it
        // seven times across the chart is the clutter this replaces.
        label(ctx, lvl.toFixed(3), x1 - 34, y, C.fib, { size: font, bold: true, plate: false })
      }
    } else if (s.kind === 'position') {
      drawPosition(ctx, s, width, isSelected, font)
    }

    if (isSelected) {
      ctx.fillStyle = C.handle
      for (const h of handlesOf(s)) ctx.fillRect(h.x - HANDLE, h.y - HANDLE, HANDLE * 2, HANDLE * 2)
    }
    ctx.restore()
  }

  // The position tool, and the same picture for trades that actually exist: the
  // risk half in red, the reward half in green, measured from the entry.
  function drawPosition(ctx, s, width, isSelected, font = 10) {
    const { entry, sl, tp } = s
    const yE = priceY(entry)
    if (yE == null) return
    const x0 = xOf(s.pts[0]) ?? 8
    // The right edge is a point of its own, so the box is as long as the trade
    // is meant to last — and can be pulled longer. Only a box with no right edge
    // recorded falls back to the canvas.
    const x1 = Math.max((xOf(s.pts[1]) ?? (width - 2)), x0 + 24)
    const w = x1 - x0

    const band = (to, color) => {
      const yT = priceY(to)
      if (to == null || yT == null) return
      ctx.fillStyle = color
      ctx.fillRect(x0, Math.min(yE, yT), w, Math.abs(yT - yE))
    }
    band(tp, 'rgba(52,211,153,0.14)')
    band(sl, 'rgba(248,113,113,0.14)')

    ctx.strokeStyle = isSelected ? C.selected : C.entry
    ctx.lineWidth = 1.5
    ctx.beginPath(); ctx.moveTo(x0, yE); ctx.lineTo(x1, yE); ctx.stroke()

    ctx.setLineDash([4, 3])
    for (const [price, color] of [[tp, C.profit], [sl, C.loss]]) {
      const y = priceY(price)
      if (price == null || y == null) continue
      ctx.strokeStyle = color
      ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke()
    }
    ctx.setLineDash([])

    const d = digitsFor(entry)
    const isLong = !(s.side === 'sell' || s.side === 'short')
    const side = isLong ? 'LONG' : 'SHORT'
    const risk = sl == null ? null : Math.abs(entry - sl)
    const reward = tp == null ? null : Math.abs(tp - entry)
    const rr = risk && reward ? (reward / risk).toFixed(2) : null

    // Where price actually went, against where the trade wanted it to go: a
    // dashed diagonal from the entry. It ENDS where the trade ended — a target
    // that has been hit is a finished trade, and a line that keeps tracking past
    // it is drawing a position nobody is in.
    const done = outcomeOf(s, isLong)
    const endPrice = done ? done.price : lastPrice()
    // The tracking line shows where price ACTUALLY went, so it stops at the last
    // bar — the box may now run on to the right edge, but price has not been
    // there yet and a diagonal drawn into empty space claims it has.
    const lastBar = bars()[bars().length - 1]
    const nowX = (lastBar ? xOf({ time: lastBar.time, logical: null }) : null) ?? x1
    const endX = Math.min(done && done.x != null ? done.x : nowX, x1)
    const yC = priceY(endPrice)
    if (endPrice != null && yC != null) {
      const moved = isLong ? endPrice - entry : entry - endPrice
      const ahead = moved >= 0
      ctx.save()
      ctx.setLineDash([3, 3])
      ctx.lineWidth = 1.25
      ctx.strokeStyle = ahead ? C.profit : C.loss
      ctx.beginPath(); ctx.moveTo(x0, yE); ctx.lineTo(endX, yC); ctx.stroke()
      ctx.setLineDash([])
      ctx.beginPath(); ctx.arc(endX - (done ? 0 : 3), yC, done ? 3.5 : 2.5, 0, Math.PI * 2)
      ctx.fillStyle = ahead ? C.profit : C.loss
      ctx.fill()
      ctx.restore()

      // How far it came, in R — the unit the trade was sized in. Without a stop
      // there is no R, so it reports the distance instead of inventing one.
      const openR = risk ? moved / risk : null
      const value = openR != null
        ? `${openR >= 0 ? '+' : ''}${openR.toFixed(2)}R`
        : `${moved >= 0 ? '+' : ''}${moved.toFixed(d)}`
      const tail = done
        ? `  ·  ${done.hit} hit`
        : (tp == null ? '' : `  ·  ${Math.abs(tp - endPrice).toFixed(d)} to TP`)
      label(ctx, `${value}${tail}`, Math.max(x0 + 4, endX - 150), yC - 12,
            ahead ? C.profit : C.loss, { size: font })
    }

    // Side and reward:risk on the entry; the three PRICES are on the axis.
    label(ctx, `${side}${rr ? `  ·  ${rr}R` : ''}`, x0 + 4, yE - 4, C.text, { size: font })
  }

  // Did this trade finish? Walk the bars from the entry onwards and take the
  // FIRST touch of either level: whichever a bar reached first is where the
  // trade ended, and everything after it belongs to a position nobody holds.
  //
  // Only for positions someone DREW. A trade the account actually holds is open
  // by definition — if its target had been hit it would not still be open — so
  // that one keeps tracking the live price.
  function outcomeOf(s, isLong) {
    if (s.id?.startsWith('auto-')) return null
    if (s.tp == null && s.sl == null) return null
    const anchor = s.pts[0]
    const from = anchor?.time
    if (from == null) return null

    for (const b of bars()) {
      if (b.time < from) continue
      const hitTp = s.tp != null && (isLong ? b.high >= s.tp : b.low <= s.tp)
      const hitSl = s.sl != null && (isLong ? b.low <= s.sl : b.high >= s.sl)
      if (!hitTp && !hitSl) continue
      // Both inside one bar: the stop is the honest answer, because a bar does
      // not say which side it reached first and assuming the good one flatters
      // every trade that ever gapped through.
      const hit = hitSl ? 'SL' : 'TP'
      const price = hitSl ? s.sl : s.tp
      const x = xOf({ time: b.time, logical: null })
      return { hit, price, x: x == null ? null : x }
    }
    return null
  }

  const renderer = {
    draw(target) {
      target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
        ctx.textBaseline = 'middle'
        const font = fontFor(mediaSize.height)
        for (const t of autoPositions()) drawShape(ctx, t, mediaSize.width, false, font)
        for (const s of shapes) drawShape(ctx, s, mediaSize.width, s.id === selected, font)
        if (draft) drawShape(ctx, draft, mediaSize.width, true, font)
      })
    },
  }

  // ── prices go on the PRICE AXIS ────────────────────────────────────────────
  // Where every other price on this chart already is. Writing them across the
  // lines instead put seven numbers through the middle of a Fibonacci and three
  // through a position, none of them lined up with anything.
  //
  // The array is rebuilt only when the prices actually change: the library keeps
  // caches keyed on the array's identity, so handing it a fresh array on every
  // frame would throw that away.
  let axisViews = []
  let axisKey = ''

  function axisLabel(price, color) {
    return {
      coordinate: () => priceY(price) ?? -1e6,
      text: () => price.toFixed(digitsFor(price)),
      textColor: () => '#0b0b0b',
      backColor: () => color,
      visible: () => priceY(price) != null,
      tickVisible: () => true,
    }
  }

  function buildAxisViews() {
    const out = []
    const key = []
    for (const s of shapes) {
      if (s.kind === 'hray') {
        out.push(axisLabel(s.pts[0].price, s.color || C.line))
        key.push(`h${s.pts[0].price}`)
      } else if (s.kind === 'fib') {
        const [a, b] = s.pts
        for (const lvl of FIB) {
          const price = a.price + (b.price - a.price) * lvl
          out.push(axisLabel(price, C.fib))
          key.push(`f${price}`)
        }
      } else if (s.kind === 'position') {
        for (const [price, color] of [[s.entry, C.entry], [s.tp, C.profit], [s.sl, C.loss]]) {
          if (price == null) continue
          out.push(axisLabel(price, color))
          key.push(`p${price}`)
        }
      }
    }
    for (const t of autoPositions()) {
      for (const [price, color] of [[t.entry, C.entry], [t.tp, C.profit], [t.sl, C.loss]]) {
        if (price == null) continue
        out.push(axisLabel(price, color))
        key.push(`a${price}`)
      }
    }
    const joined = key.join('|')
    if (joined !== axisKey) { axisKey = joined; axisViews = out }
    return axisViews
  }

  const primitive = {
    attached(p) { requestUpdate = p.requestUpdate || (() => {}) },
    detached() { requestUpdate = () => {} },
    updateAllViews() {},
    paneViews: () => [{ renderer: () => renderer, zOrder: () => 'top' }],
    priceAxisViews: () => buildAxisViews(),
  }
  series.attachPrimitive(primitive)

  // ── the account's own open trades, drawn without being asked ───────────────
  // While a real trade's stop or target is being dragged, the new price lives
  // here rather than in the trade: the trade is the broker's, and it must not
  // appear moved until the broker has agreed. Cleared when fresh trades arrive.
  let liveEdit = null       // { position_id, sl?, tp? }

  function autoPositions() {
    const out = []
    // A trade that does not exist yet, drawn exactly like one that does so the
    // levels can be seen against the candles and moved by hand. It reuses the
    // whole live-position path — same shape, same handles, same drag — and is
    // told apart only by its id, which is what routes the change back to the
    // caller instead of to the broker.
    if (proposal && proposal.entry) {
      const p = liveEdit && liveEdit.position_id === '_proposal' ? liveEdit : null
      const lastBar = bars()[bars().length - 1]
      out.push({
        id: 'auto-_proposal', kind: 'position', side: proposal.side,
        position_id: '_proposal', live: true,
        entry: proposal.entry,
        sl: p && p.sl != null ? p.sl : (proposal.sl ?? null),
        tp: p && p.tp != null ? p.tp : (proposal.tp ?? null),
        pts: [{ time: lastBar ? lastBar.time : null, logical: null, price: proposal.entry },
              { time: null, logical: null, price: null }],
      })
    }
    for (const t of trades || []) {
      const entry = t.open_price?.price
      if (!entry) continue
      const list = bars()
      const last = list[list.length - 1]
      // Where the trade STARTED — snapped to the BAR that contains it.
      //
      // This is the whole bug: timeToCoordinate() answers null for any time that
      // is not exactly a bar's own timestamp, and a trade opens mid-bar (12:03,
      // not the 12:00 the M15 candle is stamped with). So the entry never
      // resolved, x fell through to the canvas's left edge, and the risk and
      // reward bands painted backwards across bars from before the trade
      // existed. Snapping to the containing bar gives a time the axis can
      // actually place, and the logical index is carried too so it still lands
      // if the axis refuses the time.
      const opened = openedAt(t.opened_at)
      let time = last ? last.time : null
      let logical = null
      if (opened != null && list.length) {
        let i = -1
        for (let k = list.length - 1; k >= 0; k--) {
          if (list[k].time <= opened) { i = k; break }
        }
        if (i >= 0) { time = list[i].time; logical = i }
        else { time = list[0].time; logical = 0 }   // opened before the window
      }
      const edit = liveEdit && String(liveEdit.position_id) === String(t.position_id)
        ? liveEdit : null
      out.push({
        id: `auto-${t.position_id}`, kind: 'position', side: t.side,
        position_id: t.position_id, live: true,
        entry,
        sl: edit && edit.sl != null ? edit.sl : (t.sl?.price ?? null),
        tp: edit && edit.tp != null ? edit.tp : (t.tp?.price ?? null),
        // It runs from the bar it was opened on to the RIGHT EDGE. A live trade
        // is about what happens next, so its stop and target belong in the empty
        // space ahead of price — not stopped at the last bar, and never trailing
        // off to the left. A null right point is what drawPosition reads as
        // "extend to the edge".
        pts: [{ time, logical, price: entry },
              { time: null, logical: null, price: null }],
      })
    }
    return out
  }

  // The broker sends the open time as epoch (seconds or ms) or as ISO; anything
  // unreadable just starts the box at the left edge.
  function openedAt(value) {
    if (value == null || value === '') return null
    const n = typeof value === 'number' ? value : Number(value)
    if (Number.isFinite(n) && n > 0) return n > 1e11 ? Math.floor(n / 1000) : Math.floor(n)
    const ms = Date.parse(String(value).endsWith('Z') ? value : `${value}Z`)
    return Number.isFinite(ms) ? Math.floor(ms / 1000) : null
  }

  // ── hit testing ────────────────────────────────────────────────────────────
  function handleAt(x, y) {
    if (!selected) return null
    const s = shapes.find((sh) => sh.id === selected)
    if (!s) return null
    for (const h of handlesOf(s)) {
      if (Math.abs(x - h.x) <= GRAB && Math.abs(y - h.y) <= GRAB) return h.key
    }
    return null
  }

  // A real trade's stop or target, under the cursor. Entry is deliberately not
  // grabbable: an open position's entry is a fact, not a setting.
  function liveHandleAt(x, y) {
    for (const s of autoPositions()) {
      for (const key of ['sl', 'tp']) {
        const yy = priceY(s[key])
        if (s[key] != null && yy != null && Math.abs(y - yy) <= GRAB) {
          return { id: s.id, position_id: s.position_id, key, from: s[key] }
        }
      }
    }
    return null
  }

  function shapeAt(x, y) {
    for (let i = shapes.length - 1; i >= 0; i--) {
      const s = shapes[i]
      if (s.kind === 'position') {
        for (const key of ['entry', 'sl', 'tp']) {
          const yy = priceY(s[key])
          if (s[key] != null && yy != null && Math.abs(y - yy) <= GRAB) return s.id
        }
        continue
      }
      const xs = s.pts.map(xOf), ys = s.pts.map((p) => priceY(p.price))
      if (ys.some((v) => v == null)) continue
      if (s.kind === 'hray' && Math.abs(y - ys[0]) <= GRAB) return s.id
      if (xs.some((v) => v == null)) continue
      if (s.kind === 'trendline' && distToSegment(x, y, xs[0], ys[0], xs[1], ys[1]) <= GRAB) return s.id
      if ((s.kind === 'rect' || s.kind === 'fib')
          && x >= Math.min(xs[0], xs[1]) - 4 && x <= Math.max(xs[0], xs[1]) + 4
          && y >= Math.min(ys[0], ys[1]) - 4 && y <= Math.max(ys[0], ys[1]) + 4) return s.id
    }
    return null
  }

  // ── pointer handling: draw by dragging, move by dragging, never pan ────────
  const el = chart.chartElement()

  // Press-and-drag stays ON, because dragging empty chart should move the chart.
  // What decides who gets a given gesture is the code below, not this option.
  chart.applyOptions({
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
  })

  // The chart's own canvas handles the press and does not pass it on, so these
  // listen in the CAPTURE phase — ahead of it — and take the gesture ONLY when
  // it is ours: a tool in hand, a handle under the cursor, or a drawing under
  // it. Anything we do not take reaches the chart untouched, which is what keeps
  // press-and-drag panning working on empty space.
  //
  // A press fires pointerdown and then mousedown, and the library listens for
  // both depending on the input, so once we have claimed a gesture we swallow
  // the mouse and touch events that follow it too.
  const own = { active: false }

  function take(ev) {
    own.active = true
    ev.preventDefault()
    ev.stopPropagation()
    el.setPointerCapture?.(ev.pointerId)
  }

  const swallow = (ev) => {
    if (!own.active) return
    ev.preventDefault()
    ev.stopPropagation()
  }

  const localPoint = (ev) => {
    const r = el.getBoundingClientRect()
    return { x: ev.clientX - r.left, y: ev.clientY - r.top }
  }

  function startDraft(pt) {
    const kind = tool === 'long' || tool === 'short' ? 'position' : tool
    if (kind === 'position') {
      return { id: '_draft', kind, side: tool, entry: pt.price, sl: pt.price, tp: pt.price,
               pts: [{ ...pt }, endPointFrom(pt)] }
    }
    return { id: '_draft', kind, pts: [{ ...pt }, { ...pt }],
             color: kind === 'fib' ? C.fib : C.line }
  }

  function extendDraft(pt) {
    if (draft.kind === 'position') {
      draft.sl = pt.price
      const risk = draft.entry - draft.sl              // signed: sign says long or short
      draft.tp = draft.entry + risk * DEFAULT_RR
      draft.side = risk > 0 ? 'long' : 'short'
    } else {
      draft.pts[1] = { ...pt }
    }
  }

  // A drag that never really moved is a click, and a click should not leave a
  // zero-length line behind.
  const tooSmall = (a, b) => Math.hypot(a.x - b.x, a.y - b.y) < 4

  // A tidy step near `v`: 1, 2, 2.5 or 5 times a power of ten. A stop of 12.7
  // helps nobody; 10 is a number you can hold in your head and adjust from.
  function nice(v) {
    if (!(v > 0)) return 0
    const e = Math.pow(10, Math.floor(Math.log10(v)))
    const steps = [1, 2, 2.5, 5, 10]
    let best = steps[0] * e
    for (const st of steps) {
      if (Math.abs(st * e - v) < Math.abs(best - v)) best = st * e
    }
    return best
  }

  // What a single CLICK of the position tool should put on the chart: a whole
  // trade, sized off what is actually on screen — a stop about a twelfth of the
  // visible height away, rounded to a tidy number, and a target at 2R from it.
  // The point is to have something complete to adjust, not something to build.
  function defaultPosition(pt, side) {
    let risk = 0
    try {
      const h = chart.paneSize().height
      const top = series.coordinateToPrice(0)
      const bottom = series.coordinateToPrice(h)
      if (top != null && bottom != null) risk = nice(Math.abs(top - bottom) / 12)
    } catch { /* fall through to the price-based default */ }
    if (!(risk > 0)) risk = nice(Math.abs(pt.price) * 0.004)
    const long = side !== 'short'
    const entry = pt.price
    return {
      id: '_draft', kind: 'position', side: long ? 'long' : 'short',
      entry,
      sl: long ? entry - risk : entry + risk,
      tp: long ? entry + risk * DEFAULT_RR : entry - risk * DEFAULT_RR,
      pts: [{ ...pt }, endPointFrom(pt)],
    }
  }

  // A box starts a couple of dozen bars wide — long enough to read, short enough
  // to say "this is the window I expect it in" — and is dragged from there.
  function endPointFrom(pt) {
    return { logical: pt.logical == null ? null : pt.logical + DEFAULT_BARS,
             time: null, price: pt.price }
  }

  const onDown = (ev) => {
    if (ev.button !== 0) return
    const { x, y } = localPoint(ev)
    const pt = pointAt(x, y)
    if (!pt) return

    if (tool) {
      draft = startDraft(pt)
      draft._startPx = { x, y }
      take(ev)
      requestUpdate()
      return
    }

    const key = handleAt(x, y)
    if (key) {
      drag = { id: selected, handle: key }
      take(ev)
      return
    }
    // A live trade's own stop/target is grabbed before any drawing, because it
    // is the thing physically under the cursor and the one with consequences.
    const lh = liveHandleAt(x, y)
    if (lh) {
      drag = { live: true, position_id: lh.position_id, handle: lh.key, from: lh.from }
      liveEdit = { position_id: lh.position_id, [lh.key]: lh.from }
      take(ev)
      return
    }

    const hit = shapeAt(x, y)
    if (hit !== selected) { selected = hit; requestUpdate() }
    if (hit) {
      const s = shapes.find((sh) => sh.id === hit)
      drag = { id: hit, handle: null, from: pt, orig: JSON.parse(JSON.stringify(s)) }
      take(ev)
      return
    }
    // Nothing of ours under the cursor: the chart keeps the gesture and pans.
  }

  const onMove = (ev) => {
    if (!draft && !drag) {
      // Idle: the cursor says what a press would do here — draw, reshape, move
      // the drawing, or move the chart.
      const p = localPoint(ev)
      el.style.cursor = tool ? 'crosshair'
        : handleAt(p.x, p.y) ? 'nwse-resize'
        : liveHandleAt(p.x, p.y) ? 'ns-resize'
        : shapeAt(p.x, p.y) ? 'move' : ''
      return
    }
    ev.stopPropagation()
    const { x, y } = localPoint(ev)
    const pt = pointAt(x, y)
    if (!pt) return

    if (draft) {
      extendDraft(pt)
    } else if (drag.live) {
      liveEdit = { position_id: drag.position_id, [drag.handle]: pt.price }
    } else if (drag.handle) {
      const s = shapes.find((sh) => sh.id === drag.id)
      if (s) moveHandle(s, drag.handle, pt)
    } else {
      const i = shapes.findIndex((sh) => sh.id === drag.id)
      if (i >= 0) {
        const base = drag.orig
        const dLogical = pt.logical - drag.from.logical
        const dPrice = pt.price - drag.from.price
        shapes[i] = { ...movedBy(base, dLogical, dPrice), id: base.id }
      }
    }
    requestUpdate()
  }

  const onUp = (ev) => {
    if (draft) {
      const { x, y } = localPoint(ev)
      const started = draft._startPx
      const isClick = started && tooSmall(started, { x, y })
      // A click is a complete instruction for some tools and half of one for
      // others. A ray is one point. A position clicked rather than dragged gets
      // a default stop and a 2R target, so it lands whole and is adjusted by its
      // handles — which is how every charting package behaves and what makes the
      // tool worth having. A line or a box, though, needs the drag.
      if (isClick && draft.kind === 'position') {
        const pt = pointAt(x, y)
        if (pt) draft = defaultPosition(pt, draft.side)
      }
      if (!isClick || draft.kind === 'hray' || draft.kind === 'position') {
        delete draft._startPx
        draft.id = nextId()
        shapes = shapes.concat([draft])
        selected = draft.id
        onChange(strip(shapes))
      }
      draft = null
      // One shape per arming, as every charting tool does — but the TOOLBAR has
      // to hear about it. It owns the button state, and a button still lit for a
      // tool this layer has already put down is a button that does nothing when
      // pressed: the caller's state never changes, so nothing is ever re-armed.
      tool = null
      el.style.cursor = ''
      onToolDone()
      requestUpdate()
    } else if (drag && drag.live) {
      // Letting go IS the instruction. The override stays on screen until the
      // caller comes back with fresh trades, so the line does not snap to the
      // old price for the second it takes the broker to answer.
      const { position_id, handle } = drag
      const price = liveEdit && liveEdit[handle]
      drag = null
      requestUpdate()
      if (price != null) onTradeLevel({ position_id, [handle]: price })
    } else if (drag) {
      drag = null
      onChange(strip(shapes))
      requestUpdate()
    }
    own.active = false
    el.releasePointerCapture?.(ev.pointerId)
  }

  const strip = (list) => list.map(({ _startPx, ...rest }) => rest)

  el.addEventListener('pointerdown', onDown, true)
  el.addEventListener('pointermove', onMove, true)
  el.addEventListener('pointerup', onUp, true)
  el.addEventListener('pointercancel', onUp, true)
  for (const type of ['mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove'])
    el.addEventListener(type, swallow, true)

  const onKey = (e) => {
    if (e.key === 'Escape') { draft = null; tool = null; el.style.cursor = ''; requestUpdate() }
    if ((e.key === 'Delete' || e.key === 'Backspace') && selected) {
      shapes = shapes.filter((s) => s.id !== selected)
      selected = null
      onChange(strip(shapes))
      requestUpdate()
    }
  }
  window.addEventListener('keydown', onKey)

  return {
    setTool(next) { tool = next; draft = null; el.style.cursor = next ? 'crosshair' : ''; requestUpdate() },
    getTool: () => tool,
    setTrades(next) {
      // Fresh truth from the broker replaces any pending drag, whether it was
      // accepted or refused — either way the trade now says what it says. A
      // PROPOSAL is not the broker's, so a tick arriving must not undo a level
      // somebody just dragged.
      trades = next || []
      if (!liveEdit || liveEdit.position_id !== '_proposal') liveEdit = null
      requestUpdate()
    },
    setProposal(next) { proposal = next || null; liveEdit = null; requestUpdate() },
    // A refused change: drop the override so the line returns to where the
    // broker still has it, rather than lying about a level that was not set.
    revertLive() { liveEdit = null; requestUpdate() },
    deleteSelected() {
      if (!selected) return false
      shapes = shapes.filter((s) => s.id !== selected)
      selected = null
      onChange(strip(shapes))
      requestUpdate()
      return true
    },
    hasSelection: () => selected != null,
    clear() { shapes = []; selected = null; draft = null; onChange([]); requestUpdate() },
    count: () => shapes.length,
    destroy() {
      window.removeEventListener('keydown', onKey)
      el.removeEventListener('pointerdown', onDown, true)
      el.removeEventListener('pointermove', onMove, true)
      el.removeEventListener('pointerup', onUp, true)
      el.removeEventListener('pointercancel', onUp, true)
      for (const type of ['mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove'])
        el.removeEventListener(type, swallow, true)
      try { series.detachPrimitive(primitive) } catch { /* chart already gone */ }
    },
  }
}
