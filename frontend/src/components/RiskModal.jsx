import { useEffect, useMemo, useRef, useState } from 'react'
import { ShieldAlert, ShieldCheck, X, Send, Loader2, Minus, Plus } from 'lucide-react'
import * as api from '../services/api.js'
import InstrumentFlag from './InstrumentFlag.jsx'
import TradeChart from './TradeChart.jsx'

// The one stop between pressing Buy or Sell and the order going out.
//
// It always opens now: a click on the Live card is one step from real money and
// cannot be taken back. So the job is to show what the trade is worth either
// way, let those amounts be CHANGED directly, and then get out of the way.
//
// Editing MONEY rather than price is the point. Nobody decides "my stop is
// 1.34047"; they decide "I am risking a hundred dollars". Money and distance are
// linear in each other, so with the per-price-unit value the check already
// returns, the conversion happens here — instantly, with no round trip per
// keystroke, which is what would make typing an amount feel broken.
//
// Its own modal rather than something on the Live card: it is a decision about
// money, and it must survive the panel being dragged or scrolled.

const round = (v, d) => Number(Number(v).toFixed(d ?? 5))

export default function RiskModal({ ctx, onClose, onPlace }) {
  const [msg, setMsg] = useState(null)
  const [sug, setSug] = useState(ctx?.suggestion || null)
  const [thinking, setThinking] = useState(false)
  const [reply, setReply] = useState('')
  const [placing, setPlacing] = useState(false)
  const [chart, setChart] = useState(null)
  const asked = useRef(false)

  const t = ctx?.trade || {}
  const pricing = ctx?.pricing || null
  const isBuy = String(t.side).toLowerCase() !== 'sell'
  const vol = Number(t.volume) || 0

  // Prices are the truth and money is a view of them, because a price is what
  // the broker is eventually handed.
  const [levels, setLevels] = useState({
    sl: ctx?.outcome?.sl ?? ctx?.suggestion?.sl ?? null,
    tp: ctx?.outcome?.tp ?? ctx?.suggestion?.tp ?? null,
  })
  // What is being typed, so a half-finished "4" does not get rounded to 4.00
  // under the cursor. Null means "not editing — show the derived amount".
  const [typing, setTyping] = useState({ sl: null, tp: null })

  const conv = useMemo(() => {
    if (!pricing?.money_per_price_unit || !vol) return null
    const per = pricing.money_per_price_unit * vol       // money per 1.0 of price
    const digits = pricing.digits ?? 5
    return {
      per, digits, entry: pricing.entry,
      toMoney: (price) => (price == null ? null : Math.abs(price - pricing.entry) * per),
      toPrice: (amount, which) => {
        const d = Math.abs(Number(amount) || 0) / per
        // A stop sits against the trade, a target with it.
        const above = which === 'sl' ? !isBuy : isBuy
        return round(pricing.entry + (above ? d : -d), digits)
      },
    }
  }, [pricing, vol, isBuy])

  const riskMoney = conv ? conv.toMoney(levels.sl) : ctx?.outcome?.risk_money
  const rewardMoney = conv ? conv.toMoney(levels.tp) : ctx?.outcome?.reward_money
  const ccy = pricing?.currency || ctx?.outcome?.currency || ''

  // One ask, and only when there is something to say. Confirming a trade that
  // fits the rules is a look at two figures, not a conversation — a model call
  // in that path would put latency and cost on every single click.
  useEffect(() => {
    if (asked.current || !ctx) return
    asked.current = true
    const worth = (ctx.issues || []).length > 0 || (ctx.suggestion && !ctx.suggestion.advisory)
    if (!worth) return
    setThinking(true)
    api.tradeAdvise(ctx, '')
      .then((r) => {
        setMsg(r.message)
        if (r.volume || r.sl || r.tp) setSug((s) => ({ ...(s || {}), ...r }))
      })
      .catch(() => setMsg(ctx.issues?.[0]?.reason || null))
      .finally(() => setThinking(false))
  }, [ctx])

  // The chart loads beside it so the levels can be seen and dragged. Only where
  // there is room: on a phone it would push the decision below the fold.
  useEffect(() => {
    if (!t.symbol || (typeof window !== 'undefined' && window.innerWidth < 980)) return
    let dead = false
    api.chartData({ symbol: t.symbol, timeframe: 'M15', count: 120, account: t.account })
      .then((d) => { if (!dead && d?.candles?.length) setChart(d) })
      .catch(() => { /* the modal is fine without it */ })
    return () => { dead = true }
  }, [t.symbol, t.account])

  // A NEW object here would rebuild the chart on every keystroke — the spec is
  // an effect dependency downstream, and typing an amount would tear the canvas
  // down and put it back, losing wherever the user had panned to.
  const chartSpec = useMemo(
    () => (chart ? { ...chart, account: t.account } : null),
    [chart, t.account])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!ctx) return null
  const blocking = (ctx.issues || []).filter((i) => i.severity === 'block')
  const proceedLabel = blocking.length ? 'Place anyway' : 'Place'
  const alt = sug && sug.volume && Number(sug.volume) !== Number(t.volume) ? sug : null

  const setMoney = (which, amount) => {
    if (!conv) return
    setLevels((L) => ({ ...L, [which]: conv.toPrice(amount, which) }))
  }
  // A step that means something at any size: 5% of what is currently at stake,
  // so it is cents on a small trade and not a thousand clicks on a large one.
  const nudge = (which, dir) => {
    const cur = which === 'sl' ? riskMoney : rewardMoney
    const base = Math.abs(cur || 0) || 10
    const next = Math.max(0, base + dir * Math.max(0.01, base * 0.05))
    setTyping((s) => ({ ...s, [which]: null }))
    setMoney(which, next)
  }

  async function sendReply(e) {
    e?.preventDefault()
    const text = reply.trim()
    if (!text || thinking) return
    setReply(''); setThinking(true)
    try {
      const r = await api.tradeAdvise({ ...ctx, suggestion: sug }, text)
      setMsg(r.message)
      if (r.volume || r.sl || r.tp) {
        setSug((s) => ({ ...(s || {}), ...r }))
        setLevels((L) => ({ sl: r.sl ?? L.sl, tp: r.tp ?? L.tp }))
        setTyping({ sl: null, tp: null })
      }
    } catch (err) { setMsg(err.message) } finally { setThinking(false) }
  }

  async function place(volume) {
    setPlacing(true)
    try {
      await onPlace({ volume, sl: levels.sl || null, tp: levels.tp || null, override: true })
      onClose()
    } finally { setPlacing(false) }
  }

  const Amount = ({ which, label, value, price, tone }) => (
    <div className={`rk-out rk-out--${tone}`}>
      <i>{label}</i>
      <div className="rk-amt">
        <button type="button" className="rk-step" title="Less"
                onClick={() => nudge(which, -1)} disabled={!conv}>
          <Minus size={13} strokeWidth={2.4} />
        </button>
        <span className="rk-amt-val">
          <span className="rk-sign">{tone === 'loss' ? '−' : '+'}</span>
          <input
            className="rk-amt-input"
            value={typing[which] != null ? typing[which]
              : (value == null ? '' : Number(value).toFixed(2))}
            inputMode="decimal"
            disabled={!conv}
            onChange={(e) => {
              const raw = e.target.value.replace(/[^\d.]/g, '')
              setTyping((s) => ({ ...s, [which]: raw }))
              setMoney(which, raw)
            }}
            onBlur={() => setTyping((s) => ({ ...s, [which]: null }))}
            aria-label={label} />
          <span className="rk-ccy">{ccy}</span>
        </span>
        <button type="button" className="rk-step" title="More"
                onClick={() => nudge(which, +1)} disabled={!conv}>
          <Plus size={13} strokeWidth={2.4} />
        </button>
      </div>
      <em>{price ? `at ${price}` : 'no level'}</em>
    </div>
  )

  return (
    <div className="rk-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className={'rk-modal' + (chart ? ' rk-modal--wide' : '')} role="dialog" aria-modal="true">
        <div className="rk-main">
          <div className="rk-head">
            {(ctx.issues || []).length ? <ShieldAlert size={17} strokeWidth={1.9} />
                                       : <ShieldCheck size={17} strokeWidth={1.9} />}
            <div className="rk-head-main">
              <h3 className="rk-title">{blocking.length ? 'Check this trade'
                : (ctx.issues || []).length ? 'One thing to know' : 'Confirm this trade'}</h3>
              <p className="rk-sub">
                <InstrumentFlag symbol={t.symbol} size="sm" />
                <span className={'rk-side rk-side--' + t.side}>{t.side}</span>
                {t.volume} lots of {t.symbol}
              </p>
            </div>
            <button className="rk-x" onClick={onClose} title="Cancel"><X size={16} strokeWidth={2} /></button>
          </div>

          <div className="rk-body">
            {blocking.map((i) => (
              <div key={i.code} className="rk-issue"><b>{i.title}</b><span>{i.reason}</span></div>
            ))}
            {(ctx.issues || []).filter((i) => i.severity === 'warn').map((i) => (
              <div key={i.code} className="rk-issue rk-issue--warn"><b>{i.title}</b><span>{i.reason}</span></div>
            ))}

            <div className="rk-outcome">
              <Amount which="sl" label="If your stop is hit" tone="loss"
                      value={riskMoney} price={levels.sl} />
              <Amount which="tp" label="If your target is hit" tone="win"
                      value={rewardMoney} price={levels.tp} />
            </div>
            {!conv && (
              <p className="rk-hint">This symbol could not be priced, so the amounts cannot be edited here.</p>
            )}

            {thinking && !msg ? (
              <p className="rk-msg rk-msg--wait">
                <Loader2 size={13} className="rk-spin" strokeWidth={2} /> Checking against your rules…
              </p>
            ) : msg ? <p className="rk-msg">{msg}</p> : null}

            <form className="rk-reply" onSubmit={sendReply}>
              <input className="input rk-input"
                     placeholder="Or tell it what you want — “risk 50, stop below yesterday's low”"
                     value={reply} onChange={(e) => setReply(e.target.value)} disabled={placing} />
              <button className="rk-send" type="submit" disabled={!reply.trim() || thinking} title="Send">
                {thinking ? <Loader2 size={14} className="rk-spin" strokeWidth={2} />
                          : <Send size={14} strokeWidth={1.9} />}
              </button>
            </form>
          </div>

          <div className="rk-foot">
            <button className="btn btn--ghost" onClick={onClose} disabled={placing}>Cancel</button>
            <div className="rk-foot-right">
              {alt ? (
                <>
                  {/* Their rules, their call. Overriding is first-class. */}
                  <button className="btn btn--ghost" disabled={placing} onClick={() => place(t.volume)}>
                    {proceedLabel} ({t.volume})
                  </button>
                  <button className="btn btn--primary" disabled={placing}
                          onClick={() => { setLevels({ sl: alt.sl, tp: alt.tp }); place(alt.volume) }}>
                    {placing ? 'Placing…' : `Accept ${alt.volume}`}
                  </button>
                </>
              ) : (
                <button className="btn btn--primary" disabled={placing} onClick={() => place(t.volume)}>
                  {placing ? 'Placing…' : `${isBuy ? 'Buy' : 'Sell'} ${t.volume} ${t.symbol}`}
                </button>
              )}
            </div>
          </div>
        </div>

        {chart && (
          <div className="rk-chart">
            <TradeChart
              spec={chartSpec}
              proposal={{ side: t.side, entry: pricing?.entry, sl: levels.sl, tp: levels.tp }}
              onProposalChange={(next) => {
                setTyping({ sl: null, tp: null })
                setLevels((L) => ({ ...L, ...next }))
              }}
            />
            <p className="rk-chart-hint">Drag the stop or target line to move it.</p>
          </div>
        )}
      </div>
    </div>
  )
}
