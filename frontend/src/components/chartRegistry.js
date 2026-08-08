// Which chart the person is actually looking at, and how to photograph it.
//
// The assistant can read prices from the server, but it cannot see a trendline
// somebody drew: drawings live in this browser's localStorage and are painted
// into the chart's own canvas. So when the question is "look at my chart", the
// only honest source is a picture taken here, on their screen, at that moment.
//
// Every mounted TradeChart registers itself. On send, `capture()` picks the one
// in front of them and posts the PNG; the `analyse_chart` tool then reads that
// row. A module-level registry rather than context because the chat composer
// and the chart are in different trees — the floating ChartWindow is not even
// a descendant of the conversation it belongs to.
const charts = new Map()
let seq = 0

export function register(entry) {
  const id = ++seq
  charts.set(id, { ...entry, at: Date.now() })
  return {
    update: (patch) => {
      const cur = charts.get(id)
      // `at` moves only when the chart becomes visible, so it means "when this
      // last came into view" — which is what decides ties below.
      if (cur) charts.set(id, { ...cur, ...patch, at: patch.visible && !cur.visible ? Date.now() : cur.at })
    },
    remove: () => charts.delete(id),
  }
}

// The one to analyse. Visible beats hidden, then drawn-on beats bare — if two
// charts are on screen and only one has their lines on it, that is the one they
// are asking about — then whichever came into view most recently.
function best() {
  const all = [...charts.values()].filter((c) => c.shot)
  if (!all.length) return null
  const score = (c) => (c.visible ? 1000 : 0) + (c.drawings ? 100 : 0)
  return all.sort((a, b) => (score(b) - score(a)) || (b.at - a.at))[0]
}

export function have() {
  return !!best()
}

// Take the picture and hand it over. Returns what was sent, or null.
//
// Never throws and never blocks the message: a chart that will not photograph
// is a reason for the assistant to say it cannot see one, not a reason for the
// question to fail to send.
export async function capture(api) {
  const c = best()
  if (!c) return null
  try {
    const canvas = c.shot()
    if (!canvas) return null
    const png = canvas.toDataURL('image/png')
    // A blank canvas still stringifies to a few hundred bytes; anything real is
    // far larger. Cheaper than shipping an empty picture to a vision model.
    if (!png || png.length < 5000) return null
    await api.chartSnapshot({
      symbol: c.symbol, timeframe: c.timeframe,
      drawings: c.drawings || 0, png,
    })
    return { symbol: c.symbol, timeframe: c.timeframe, drawings: c.drawings || 0 }
  } catch {
    return null
  }
}

// ── the result of a chart analysis, on its way into the conversation ──────────
// The button lives on the chart, the transcript lives in Dashboard, and the
// floating ChartWindow is not even a descendant of the chat it belongs to — so
// a window event rather than a prop threaded through three components that do
// not otherwise care.
const ANALYSED = 'entrystation:chart-analysed'

export const analysed = (detail) =>
  window.dispatchEvent(new CustomEvent(ANALYSED, { detail }))

export function onAnalysed(fn) {
  const h = (e) => fn(e.detail)
  window.addEventListener(ANALYSED, h)
  return () => window.removeEventListener(ANALYSED, h)
}
