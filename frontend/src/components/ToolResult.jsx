import { useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries } from 'lightweight-charts'
import { ChevronDown } from 'lucide-react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useDevMode } from '../services/devmode.js'

marked.setOptions({ breaks: true, gfm: true })
export const mdHtml = (t) => ({ __html: DOMPurify.sanitize(marked.parse(String(t || ''))) })

// Renders a chat tool-call / flow-trace result. Developer mode ON → raw JSON.
// OFF (default) → purpose-built, readable visuals: candlestick chart for market
// data, long/short bars for sentiment, cards for news, probability bars for the
// Fed, rendered markdown for text, and tables for everything else.
export default function ToolResult({ result }) {
  const dev = useDevMode()
  const val = parseMaybe(result)
  if (val == null) return null
  if (val && typeof val === 'object' && val.error) return <pre className="action-result">{String(val.error)}</pre>
  if (dev) return <pre className="action-result">{JSON.stringify(val, null, 2)}</pre>
  return <div className="dv"><Formatted result={val} /></div>
}

function Formatted({ result }) {
  if (result == null || result === '') return <span className="dv-null">—</span>
  if (typeof result !== 'object') return <DataView value={result} />
  const r = result
  if (r.chart === true && Array.isArray(r.candles)) return <div className="dv-note">Chart shown above · {r.symbol} {r.timeframe}</div>
  if (Array.isArray(r.candles) && r.candles.length && r.candles[0]?.epoch_ms != null) return <CandleChart data={r} />
  if (isTrace(r)) return <TraceView steps={r} />
  // analysis-agent result: rendered response + a formatted step-by-step trace
  if (Array.isArray(r.trace) && (r.response != null || r.agent != null)) {
    return (
      <div className="dv-agent">
        {typeof r.response === 'string' && r.response &&
          <div className="dv-md msg-md" dangerouslySetInnerHTML={mdHtml(r.response)} />}
        {r.trace.length > 0 && <TraceView steps={r.trace} />}
      </div>
    )
  }
  if (Array.isArray(r.sentiment) && r.sentiment.length) return <SentimentView items={r.sentiment} />
  if (Array.isArray(r.articles)) return r.articles.length ? <NewsView articles={r.articles} /> : <Empty label="No matching news" />
  if (Array.isArray(r.distribution) && r.summary) return <FedView data={r} />
  if (Array.isArray(r.events)) return r.events.length ? <DataTable rows={r.events} /> : <Empty label="No high-impact events" />
  if (Array.isArray(r.posts)) return r.posts.length ? <PostsView posts={r.posts} name={r.display_name || r.user} /> : <Empty label="No recent posts" />
  if (r.unavailable && r.note) return <div className="dv-md msg-md" dangerouslySetInnerHTML={mdHtml(r.note)} />
  if (typeof r.response === 'string' && Object.keys(r).length <= 2) {
    return <div className="dv-md msg-md" dangerouslySetInnerHTML={mdHtml(r.response)} />
  }
  return <DataView value={r} />
}

// ── agent flow trace: one expandable, formatted step per node ────────────────────
export function TraceView({ steps }) {
  if (!Array.isArray(steps) || !steps.length) return null
  return <div className="dv-trace">{steps.map((s, i) => <TraceStep key={i} step={s} />)}</div>
}

function TraceStep({ step }) {
  const [open, setOpen] = useState(false)
  const parsed = parseMaybe(step.result)
  return (
    <div className="dv-trace-step">
      <button type="button" className="dv-trace-head" onClick={() => setOpen((o) => !o)}>
        <ChevronDown size={13} strokeWidth={2} className={'dv-trace-caret' + (open ? ' open' : '')} />
        <span className="dv-trace-kind">{humanize(step.kind)}</span>
        {step.name && <span className="dv-trace-name">{step.name}</span>}
        {/* A tentacle ran because the Octo body chose it, not because an edge
            led here — the history should say which, and on which round. */}
        {step.via === 'octo' && (
          <span className="dv-trace-badge dv-trace-badge--octo">
            tentacle{step.round > 1 ? ` · round ${step.round}` : ''}
          </span>
        )}
        {step.opinion && <span className="dv-trace-badge">opinion</span>}
        {step.node && <span className="dv-trace-node">{step.node}</span>}
      </button>
      {open && (
        <div className="dv-trace-body">
          {step.input?.request && (
            <div className="dv-trace-io"><span className="dv-trace-io-label">Request in</span>{step.input.request}</div>
          )}
          {step.input?.chain_in?.length > 0 && (
            <div className="dv-trace-chain">
              <div className="dv-trace-io-label">Chain fed into this node ({step.input.chain_in.length} prior {step.input.chain_in.length === 1 ? 'read' : 'reads'})</div>
              {step.input.chain_in.map((c, i) => (
                <div key={i} className="dv-trace-chainline">
                  <span className="dv-trace-chaintag">{c.name || humanize(c.kind)}</span>
                  <span>{c.read}</span>
                </div>
              ))}
            </div>
          )}
          {step.text && <div className="dv-trace-instruction"><span className="dv-trace-io-label">Instruction</span>{step.text}</div>}
          {step.opinion && <div className="dv-trace-opinion"><strong>Opinion (this node's read). </strong>{step.opinion}</div>}
          <ToolResult result={parsed} />
        </div>
      )}
    </div>
  )
}

// ── market data: candlestick chart ──────────────────────────────────────────────
function CandleChart({ data }) {
  const holder = useRef(null)
  useEffect(() => {
    if (!holder.current || !data.candles?.length) return
    const chart = createChart(holder.current, {
      layout: { background: { color: 'transparent' }, textColor: '#8b8b8b', fontSize: 11 },
      grid: { vertLines: { color: 'rgba(128,128,128,0.08)' }, horzLines: { color: 'rgba(128,128,128,0.08)' } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 }, height: 240, autoSize: true,
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#6ee7b7', downColor: '#fca5a5', wickUpColor: '#6ee7b7', wickDownColor: '#fca5a5', borderVisible: false,
    })
    series.setData(data.candles.map((c) => ({
      time: Math.floor(c.epoch_ms / 1000), open: c.open, high: c.high, low: c.low, close: c.close,
    })))
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [data])
  const last = data.candles[data.candles.length - 1]
  return (
    <div className="dv-chart">
      <div className="dv-chart-head">
        {data.symbol && <span className="dv-chart-sym">{data.symbol}</span>}
        {data.timeframe && <span className="dv-chart-tf">{data.timeframe}</span>}
        {last && <span className="dv-chart-price">{fmtNum(last.close)}</span>}
        <span className="dv-chart-count">{data.count ?? data.candles.length} candles</span>
      </div>
      <div className="dv-chart-canvas" ref={holder} />
    </div>
  )
}

// ── sentiment: long/short bars ──────────────────────────────────────────────────
function SentimentView({ items }) {
  return (
    <div className="dv-sent">
      {items.map((s, i) => {
        const long = num(s.long_percent), short = num(s.short_percent) || (100 - long)
        return (
          <div className="dv-sent-item" key={i}>
            <div className="dv-sent-head">
              <b>{s.symbol}</b>
              {s.bias && <span className={`dv-badge dv-badge--${s.bias === 'long' ? 'high' : 'low'}`}>{s.bias}</span>}
              {s.current_price != null && <span className="dv-sent-price">{fmtNum(s.current_price)}</span>}
            </div>
            <div className="dv-sent-bar">
              <div className="dv-sent-long" style={{ width: `${long}%` }}>{long}%</div>
              <div className="dv-sent-short" style={{ width: `${short}%` }}>{short}%</div>
            </div>
            <div className="dv-sent-legend"><span className="dv-dot dv-dot--long" />Long <span className="dv-dot dv-dot--short" />Short</div>
          </div>
        )
      })}
    </div>
  )
}

// ── Fed watch: probability distribution bars ────────────────────────────────────
function FedView({ data }) {
  const dist = data.distribution || []
  const max = Math.max(...dist.map((d) => num(d.now)), 1)
  const s = data.summary || {}
  return (
    <div className="dv-fed">
      <div className="dv-fed-meta">
        Next meeting <b>{data.next_meeting}</b> · current <b>{data.current_target_rate_bps}</b> bps
        {s.no_change != null && <> · hold <b>{fmtNum(s.no_change)}%</b> · hike <b>{fmtNum(s.hike)}%</b> · cut <b>{fmtNum(s.ease)}%</b></>}
      </div>
      {dist.map((d, i) => (
        <div className="dv-fed-row" key={i}>
          <span className="dv-fed-label">{d.target_rate_bps}{d.is_current ? ' ·now' : ''}</span>
          <div className="dv-fed-track"><div className="dv-fed-fill" style={{ width: `${(num(d.now) / max) * 100}%` }} /></div>
          <span className="dv-fed-pct">{fmtNum(d.now)}%</span>
        </div>
      ))}
    </div>
  )
}

// ── news / truth-social: cards ──────────────────────────────────────────────────
function NewsView({ articles }) {
  return (
    <div className="dv-cards">
      {articles.map((a, i) => (
        <div className="dv-card" key={i}>
          <div className="dv-card-top">
            {a.impact && <span className={`dv-badge dv-badge--${a.impact}`}>{a.impact}</span>}
            {a.source && <span className="dv-card-src">{a.source}</span>}
            {a.time && <span className="dv-card-time">{fmtTime(a.time)}</span>}
          </div>
          {a.url
            ? <a className="dv-card-title" href={a.url} target="_blank" rel="noreferrer">{a.title}</a>
            : <span className="dv-card-title">{a.title}</span>}
          {a.description && <p className="dv-card-desc">{a.description}</p>}
        </div>
      ))}
    </div>
  )
}

function PostsView({ posts, name }) {
  return (
    <div className="dv-cards">
      {posts.map((p, i) => (
        <div className="dv-card" key={i}>
          <div className="dv-card-top">
            <span className="dv-card-src">{name || p.handle}</span>
            {p.datetime && <span className="dv-card-time">{fmtTime(p.datetime)}</span>}
          </div>
          <p className="dv-card-desc" style={{ color: 'var(--text-primary)' }}>{p.content}</p>
        </div>
      ))}
    </div>
  )
}

function Empty({ label }) {
  return <div className="dv-empty">{label}</div>
}

// ── generic renderer ────────────────────────────────────────────────────────────
function DataView({ value, depth = 0 }) {
  if (value == null || value === '') return <span className="dv-null">—</span>
  if (typeof value !== 'object') {
    const s = String(value)
    if (looksMarkdown(s)) return <div className="dv-md msg-md" dangerouslySetInnerHTML={mdHtml(s)} />
    if (s.length > 100 || s.includes('\n')) return <div className="dv-text">{s}</div>
    return <span className="dv-scalar">{fmtScalar(value)}</span>
  }
  if (Array.isArray(value)) {
    if (!value.length) return <span className="dv-null">none</span>
    if (isTrace(value)) return <TraceView steps={value} />
    if (value[0]?.epoch_ms != null) return <CandleChart data={{ candles: value }} />
    if (value.every((x) => x && typeof x === 'object' && !Array.isArray(x))) return <DataTable rows={value} />
    return (
      <div className="dv-chips">
        {value.slice(0, 60).map((v, i) => <span className="dv-chip" key={i}>{fmtScalar(v)}</span>)}
        {value.length > 60 && <span className="dv-chip dv-chip--more">+{value.length - 60}</span>}
      </div>
    )
  }
  const entries = Object.entries(value).filter(([, v]) => v !== null && v !== undefined && v !== '')
  if (!entries.length) return <span className="dv-null">—</span>
  return (
    <div className={'dv-obj' + (depth ? ' dv-obj--nested' : '')}>
      {entries.map(([k, v]) => {
        const block = v && typeof v === 'object'
        return (
          <div className={block ? 'dv-row dv-row--block' : 'dv-row'} key={k}>
            <span className="dv-key">{humanize(k)}</span>
            <div className="dv-vwrap"><DataView value={v} depth={depth + 1} /></div>
          </div>
        )
      })}
    </div>
  )
}

function DataTable({ rows }) {
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))].slice(0, 8)
  const shown = rows.slice(0, 40)
  return (
    <div className="dv-table-wrap">
      <table className="dv-table">
        <thead><tr>{cols.map((c) => <th key={c}>{humanize(c)}</th>)}</tr></thead>
        <tbody>{shown.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c}>{fmtCell(r[c])}</td>)}</tr>)}</tbody>
      </table>
      {rows.length > shown.length && <div className="dv-more">+{rows.length - shown.length} more rows</div>}
    </div>
  )
}

// ── helpers ─────────────────────────────────────────────────────────────────────
function parseMaybe(v) {
  if (typeof v === 'string') {
    const s = v.trim()
    if (s[0] === '{' || s[0] === '[') {
      try { return JSON.parse(s) } catch { /* maybe truncated — try to salvage */ }
      const salv = salvageJson(s)
      if (salv !== undefined) return salv
    }
  }
  return v
}

// Recover a valid object from a TRUNCATED JSON string (old traces were cut mid-way):
// trim to the last complete '}' and balance any open brackets, so e.g. a chopped
// candle array still parses (with the candles that survived) → renders as a chart.
function salvageJson(s) {
  const cut = s.lastIndexOf('}')
  if (cut < 0) return undefined
  let t = s.slice(0, cut + 1)
  const stack = []
  let inStr = false, esc = false
  for (const ch of t) {
    if (esc) { esc = false; continue }
    if (ch === '\\') { esc = true; continue }
    if (ch === '"') { inStr = !inStr; continue }
    if (inStr) continue
    if (ch === '{' || ch === '[') stack.push(ch)
    else if (ch === '}' || ch === ']') stack.pop()
  }
  for (let i = stack.length - 1; i >= 0; i--) t += stack[i] === '{' ? '}' : ']'
  try { return JSON.parse(t) } catch { return undefined }
}
function isTrace(v) {
  return Array.isArray(v) && v.length > 0 && v.every((x) => x && typeof x === 'object' && 'kind' in x && 'result' in x)
}
function looksMarkdown(s) {
  return typeof s === 'string' && (s.length > 80 || /\n/.test(s)) && /(\*\*|##?#?\s|^\s*[-*]\s|\|)/m.test(s)
}
function humanize(k) {
  return String(k).replace(/[_-]/g, ' ').replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/^./, (c) => c.toUpperCase())
}
function num(v) { const n = Number(v); return Number.isFinite(n) ? n : 0 }
function fmtNum(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  return n.toLocaleString('en-US', { maximumFractionDigits: 6 })
}
function fmtScalar(v) {
  if (v == null || v === '') return '—'
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (typeof v === 'number') return fmtNum(v)
  if (typeof v === 'string' && /^\d{4}-\d\d-\d\dT/.test(v)) return fmtTime(v)
  return String(v)
}
function fmtCell(v) {
  if (v == null || v === '') return '—'
  if (typeof v === 'object') return Array.isArray(v) ? `${v.length} items` : '{…}'
  return fmtScalar(v)
}
function fmtTime(v) {
  try {
    const d = new Date(v)
    if (Number.isNaN(d.getTime())) return String(v)
    return d.toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
  } catch { return String(v) }
}
