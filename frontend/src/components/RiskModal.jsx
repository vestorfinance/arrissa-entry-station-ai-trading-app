import { useEffect, useRef, useState } from 'react'
import { ShieldAlert, ShieldCheck, X, Send, Loader2 } from 'lucide-react'
import * as api from '../services/api.js'
import InstrumentFlag from './InstrumentFlag.jsx'

// The one interruption between pressing BUY and the order going out.
//
// It only ever opens when the deterministic check already found something, so
// its whole job is: say what is wrong, offer one concrete alternative, and let
// the trader take it, ignore it, or argue. Three ways out and no dead ends —
// their rules are theirs, so "Place anyway" is always available.
//
// Its own modal rather than something hanging off the Live card: it is a
// decision about money, it must survive the panel being dragged or scrolled,
// and a popover pinned to a floating card can end up half off-screen.
const money = (n) =>
  n == null ? '—' : Number(n).toLocaleString(undefined,
    { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export default function RiskModal({ ctx, onClose, onPlace }) {
  const [msg, setMsg] = useState(null)         // the agent's reply
  const [sug, setSug] = useState(ctx?.suggestion || null)
  const [thinking, setThinking] = useState(false)
  const [reply, setReply] = useState('')
  const [placing, setPlacing] = useState(false)
  const asked = useRef(false)

  // One ask, on open. The agent is not consulted again unless the trader says
  // something — a second unprompted opinion is the "too much conversation" the
  // whole design is trying to avoid.
  useEffect(() => {
    if (asked.current || !ctx) return
    asked.current = true
    // A model is asked only when there is something to say. Confirming a trade
    // that fits the rules is a look at the numbers, not a conversation — putting
    // a call in that path would put latency and cost on every single click.
    const worthAsking = (ctx.issues || []).length > 0
      || (ctx.suggestion && !ctx.suggestion.advisory)
    if (!worthAsking) return
    setThinking(true)
    api.tradeAdvise(ctx, '')
      .then((r) => {
        setMsg(r.message)
        if (r.volume || r.sl || r.tp) setSug((s) => ({ ...(s || {}), ...r }))
      })
      .catch(() => setMsg(ctx.issues?.[0]?.reason || null))
      .finally(() => setThinking(false))
  }, [ctx])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!ctx) return null
  const t = ctx.trade || {}
  const blocking = (ctx.issues || []).filter((i) => i.severity === 'block')
  // With nothing blocking, proceeding is not an override — it is just placing
  // the trade, and the button should not imply they are breaking their own rule.
  const proceedLabel = blocking.length ? 'Place anyway' : 'Place'
  // A suggestion only becomes a CHOICE when it differs from what they typed. An
  // advisory one carries their own volume, so offering "Accept 0.1" beside
  // "Place 0.1" would be two buttons that do the same thing.
  const alt = sug && sug.volume && Number(sug.volume) !== Number(t.volume) ? sug : null

  async function sendReply(e) {
    e?.preventDefault()
    const text = reply.trim()
    if (!text || thinking) return
    setReply('')
    setThinking(true)
    try {
      const r = await api.tradeAdvise({ ...ctx, suggestion: sug }, text)
      setMsg(r.message)
      if (r.volume || r.sl || r.tp) setSug((s) => ({ ...(s || {}), ...r }))
    } catch (err) {
      setMsg(err.message)
    } finally {
      setThinking(false)
    }
  }

  async function place(vol, sl, tp) {
    setPlacing(true)
    try {
      await onPlace({ volume: vol, sl: sl || null, tp: tp || null, override: true })
      onClose()
    } finally {
      setPlacing(false)
    }
  }

  return (
    <div className="rk-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="rk-modal" role="dialog" aria-modal="true">
        <div className="rk-head">
          {(ctx.issues || []).length ? <ShieldAlert size={17} strokeWidth={1.9} />
                                     : <ShieldCheck size={17} strokeWidth={1.9} />}
          <div className="rk-head-main">
            <h3 className="rk-title">{blocking.length ? 'Check this trade'
              : (ctx.issues || []).length ? 'One thing to know'
              : 'Confirm this trade'}</h3>
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
            <div key={i.code} className="rk-issue">
              <b>{i.title}</b>
              <span>{i.reason}</span>
            </div>
          ))}
          {(ctx.issues || []).filter((i) => i.severity === 'warn').map((i) => (
            <div key={i.code} className="rk-issue rk-issue--warn">
              <b>{i.title}</b>
              <span>{i.reason}</span>
            </div>
          ))}

          {thinking && !msg ? (
            <p className="rk-msg rk-msg--wait">
              <Loader2 size={13} className="rk-spin" strokeWidth={2} /> Checking against your rules…
            </p>
          ) : msg ? (
            <p className="rk-msg">{msg}</p>
          ) : null}

          {/* The question anybody actually has before pressing the button. */}
          {ctx.outcome && (ctx.outcome.risk_money != null || ctx.outcome.reward_money != null) ? (
            <div className="rk-outcome">
              <div className="rk-out rk-out--loss">
                <i>If your stop is hit</i>
                <b>−{money(ctx.outcome.risk_money)} {ctx.outcome.currency}</b>
                {ctx.outcome.sl ? <em>at {ctx.outcome.sl}</em> : null}
              </div>
              <div className="rk-out rk-out--win">
                <i>If your target is hit</i>
                <b>+{money(ctx.outcome.reward_money)} {ctx.outcome.currency}</b>
                {ctx.outcome.tp ? <em>at {ctx.outcome.tp}</em> : null}
              </div>
            </div>
          ) : null}

          {sug && (sug.volume || sug.sl || sug.tp) ? (
            <div className="rk-sug">
              {sug.volume ? <span><i>Size</i> {sug.volume} lots</span> : null}
              {sug.sl ? <span><i>Stop</i> {sug.sl}</span> : null}
              {sug.tp ? <span><i>Target</i> {sug.tp}</span> : null}
              {sug.risk_money != null
                ? <span><i>Risks</i> {money(sug.risk_money)} {ctx.outcome?.currency}</span> : null}
              {sug.reward_money != null
                ? <span><i>Makes</i> {money(sug.reward_money)} {ctx.outcome?.currency}</span> : null}
            </div>
          ) : null}

          <form className="rk-reply" onSubmit={sendReply}>
            <input
              className="input rk-input"
              placeholder="Or tell it what you want — “0.5 lots, stop below yesterday's low”"
              value={reply}
              onChange={(e) => setReply(e.target.value)}
              disabled={placing} />
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
                {/* Their rules, their call. Overriding is a first-class option,
                    not something to be talked out of. */}
                <button className="btn btn--ghost" disabled={placing}
                        onClick={() => place(t.volume, t.sl, t.tp)}>
                  {proceedLabel} ({t.volume})
                </button>
                <button className="btn btn--primary" disabled={placing}
                        onClick={() => place(alt.volume, alt.sl, alt.tp)}>
                  {placing ? 'Placing…' : `Accept ${alt.volume}`}
                </button>
              </>
            ) : (
              /* Nothing to argue with: one button, and it carries the stop and
                 target the engine sized to their rule. */
              <button className="btn btn--primary" disabled={placing}
                      onClick={() => place(t.volume, sug?.sl ?? t.sl, sug?.tp ?? t.tp)}>
                {placing ? 'Placing…'
                  : `${t.side === 'sell' ? 'Sell' : 'Buy'} ${t.volume} ${t.symbol}`}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
