import { useEffect, useRef, useState } from 'react'
import InstrumentFlag from './InstrumentFlag.jsx'
import RiskModal from './RiskModal.jsx'
import { Briefcase, X, Trash2, Scale, XCircle, AlertTriangle, CandlestickChart } from 'lucide-react'
import * as api from '../services/api.js'
import { useModule } from '../services/capabilities.js'
import BrokerLogo from './BrokerLogo.jsx'
import { backdrop } from '../services/backdrop.js'
import ChartWindow from './ChartWindow.jsx'
import { getActiveAccount, setActiveAccount, useActiveAccount } from '../services/activeAccount.js'

const NO_CONFIRM_KEY = 'arrissa.live.closeNoConfirm'
const readJSON = (k, fb) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : fb } catch { return fb } }
const CUR_SYMBOL = { USD: '$', ZAR: 'R', EUR: '€', GBP: '£', JPY: '¥', AUD: 'A$', NZD: 'NZ$', CAD: 'C$', CHF: 'CHF ', NGN: '₦', INR: '₹' }
const curSymbol = (code) => CUR_SYMBOL[(code || 'USD').toUpperCase()] || ((code ? code + ' ' : '$'))

// Floating live-positions panel. Real-time via WebSocket (tick-driven), no polling.
// Whether it (and any chart windows) is open — plus its dragged position and active
// account — is remembered across refreshes, so it stays floating until closed.
export default function LivePanel() {
  const hasExness = useModule('exness')
  const [open, setOpen] = useState(() => localStorage.getItem('arrissa.live.open') === '1')
  const [accounts, setAccounts] = useState([])
  const [acctsReady, setAcctsReady] = useState(false)   // reserve tab space until loaded
  const [active, setActive] = useState(() => getActiveAccount())   // shared across the app
  const sharedActive = useActiveAccount()
  const [snap, setSnap] = useState(null)
  const [status, setStatus] = useState('connecting')
  const [errMsg, setErrMsg] = useState(null)   // what the broker actually said
  const [pos, setPos] = useState(() => readJSON('arrissa.live.pos', null))  // {left, top} once dragged
  const [expanded, setExpanded] = useState(null)   // position_id whose actions are shown
  // the quick ticket: SELL | lots | BUY, and the risk check it may raise
  const [tradeSymbol, setTradeSymbol] = useState('')
  const [lots, setLots] = useState('0.01')
  const [ticketBusy, setTicketBusy] = useState(false)
  const [gate, setGate] = useState(null)      // ctx for RiskModal
  const [ticketMsg, setTicketMsg] = useState(null)
  const [busy, setBusy] = useState({})             // {position_id|'all': true} while acting
  const [err, setErr] = useState(null)
  const [confirm, setConfirm] = useState(null)     // {position_id, label} pending close
  const [confirmAgain, setConfirmAgain] = useState(false)  // "don't ask again" checkbox
  const [noConfirm, setNoConfirm] = useState(() => localStorage.getItem(NO_CONFIRM_KEY) === '1')
  const [charts, setCharts] = useState(() => readJSON('arrissa.live.charts', []))  // open chart windows
  const [mobile, setMobile] = useState(() => typeof window !== 'undefined'
    && window.matchMedia('(max-width: 860px)').matches)
  const wsRef = useRef(null)
  const modalRef = useRef(null)
  const dragRef = useRef(null)

  // persist the floating state so a refresh restores it
  useEffect(() => { localStorage.setItem('arrissa.live.open', open ? '1' : '0') }, [open])
  // a change made elsewhere (the chat composer) selects that account here too
  useEffect(() => {
    if (sharedActive != null && accounts.some((a) => a.account_number === sharedActive) && sharedActive !== active) {
      setActive(sharedActive)
    }
  }, [sharedActive, accounts, active])
  // seed the shared active from the panel's pick the first time nothing is set
  useEffect(() => {
    if (active != null && getActiveAccount() == null) {
      const a = accounts.find((x) => x.account_number === active)
      setActiveAccount(active, { broker: a?.platform === 'tradelocker' ? 'tradelocker' : 'exness' })
    }
  }, [active, accounts])
  useEffect(() => { if (pos) localStorage.setItem('arrissa.live.pos', JSON.stringify(pos)) }, [pos])
  useEffect(() => { localStorage.setItem('arrissa.live.charts', JSON.stringify(charts)) }, [charts])

  // on small screens the panel is a full-screen sheet, not a floating window
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 860px)')
    const onChange = () => setMobile(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  // drag the floating window by its header
  function onDragStart(e) {
    if (mobile) return                       // no dragging the full-screen sheet
    if (e.target.closest('button')) return   // let tab/close clicks through
    const rect = modalRef.current.getBoundingClientRect()
    dragRef.current = { dx: e.clientX - rect.left, dy: e.clientY - rect.top }
    window.addEventListener('mousemove', onDragMove)
    window.addEventListener('mouseup', onDragEnd)
    e.preventDefault()
  }
  function onDragMove(e) {
    const { dx, dy } = dragRef.current || {}
    const el = modalRef.current
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

  // load the active/selected accounts (both brokers) when the panel opens
  useEffect(() => {
    if (!open) return
    Promise.all([
      hasExness ? api.getExnessAccounts().catch(() => ({ accounts: [], selected: [], active_account: null }))
                : Promise.resolve({ accounts: [], selected: [], active_account: null }),
      api.getAllAccounts().catch(() => ({ tradelocker: { connections: [] }, active: {} })),
    ]).then(([ex, all]) => {
      const selected = ex.selected || []
      // Exness: only the MT5 accounts activated in Settings (never archived)
      const exPool = (ex.accounts || []).filter(
        // Same string-vs-number trap as the chat picker: `selected` comes from
        // JSONB as strings, `account_number` is a number.
        (a) => !a.is_archived && a.platform === 'mt5'
          && selected.map(String).includes(String(a.account_number)))
      // TradeLocker: the connected accounts the user has made available to the
      // app. `available === null` means they have never chosen, which is all.
      const tlAvail = all.tradelocker?.available ?? null
      const tlPool = (all.tradelocker?.connections || []).flatMap((c) =>
        (c.accounts || []).filter(
          (a) => tlAvail === null || tlAvail.map(String).includes(String(a.account_id)))
        .map((a) => ({
          account_number: Number(a.account_id) || a.account_id,
          account_type: 'TradeLocker', currency: a.currency,
          is_real: a.environment === 'live', is_archived: false, platform: 'tradelocker',
        })))
      const pool = [...exPool, ...tlPool]
      setAccounts(pool)
      const inPool = (n) => pool.some((a) => a.account_number === n)
      const appActive = all.active?.account != null
        ? (Number(all.active.account) || all.active.account) : null
      const shared = getActiveAccount()
      setActive((cur) => {
        if (shared != null && inPool(shared)) return shared    // the remembered shared active (composer/live)
        if (cur && inPool(cur)) return cur                     // keep a valid current pick
        if (appActive != null && inPool(appActive)) return appActive   // the app's active account (any broker)
        if (ex.active_account && inPool(ex.active_account)) return ex.active_account
        return pool[0]?.account_number || null                 // else the first account
      })
    }).catch(() => {}).finally(() => setAcctsReady(true))
  }, [open, hasExness])

  // Persistent live WebSocket for the open panel's active tab. It auto-reconnects
  // on any drop (network blip, proxy idle-close, backend restart) and pings to stay
  // warm — so it never silently dies until a refresh.
  useEffect(() => {
    if (!open || !active) return
    setSnap(null)
    setStatus('connecting')
    setExpanded(null)
    let closedByUs = false
    let reconnectTimer = null
    let pingTimer = null

    const connect = () => {
      const token = localStorage.getItem('auth_token')
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/ws/positions/${active}?token=${token}`)
      wsRef.current = ws
      ws.onopen = () => {
        // keep traffic flowing so Cloudflare/Caddy (~100s idle) don't close the socket
        clearInterval(pingTimer)
        pingTimer = setInterval(() => { try { if (ws.readyState === 1) ws.send('ping') } catch { /* ignore */ } }, 25000)
      }
      ws.onmessage = (e) => {
        try {
          const d = JSON.parse(e.data)
          // The server says WHY when it can — "Exness refused account 436807609"
          // is worth reading; "Connection error" sends you looking at your wifi.
          if (d.error) { setErrMsg(d.error); setStatus('error') }
          else { setErrMsg(null); setSnap(d); setStatus('live') }
        } catch { /* ignore */ }
      }
      ws.onerror = () => { /* the close handler below drives reconnect */ }
      ws.onclose = (e) => {
        clearInterval(pingTimer)
        if (closedByUs) return
        if (e.code === 4401) { setStatus('error'); return }   // auth failed — don't loop, refresh needed
        setStatus('connecting')                     // reconnecting — not "disconnected"
        reconnectTimer = setTimeout(connect, 2000)  // retry until it's back
      }
    }
    connect()

    return () => {
      closedByUs = true
      clearInterval(pingTimer)
      clearTimeout(reconnectTimer)
      try { wsRef.current?.close() } catch { /* ignore */ }
    }
  }, [open, active])

  // currency follows the active account (e.g. a ZAR account shows R, not $).
  // Until the account's REAL currency is known we return null so the value shows
  // a ghost placeholder — never a wrong "$" that then flashes to "R".
  const acct = accounts.find((a) => a.account_number === active)
  const curReady = !!acct?.currency
  const cur = curSymbol(acct?.currency)
  const money = (n) => (!curReady || n == null ? null
    : (n < 0 ? '-' : '') + cur + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
  const pnl = (n) => (n > 0 ? 'pnl-up' : n < 0 ? 'pnl-down' : '')

  // ── actions ─────────────────────────────────────────────
  function mark(key, on) {
    setBusy((b) => { const n = { ...b }; if (on) n[key] = true; else delete n[key]; return n })
  }
  // surface per-position failures the endpoint returns (it responds 200 with a
  // results array even when a broker close/break-even actually failed).
  function failMsg(res) {
    const fails = (res?.result || []).filter((r) => r && r.ok === false)
    return fails.length ? (fails[0].error || `${fails.length} action(s) failed`) : null
  }
  async function doBreakEven(positionId = null) {
    const key = positionId || 'all'
    mark(key, true); setErr(null)
    try { const m = failMsg(await api.breakEvenPositions(active, positionId)); if (m) setErr(m) }
    catch (e) { setErr(e.message) }
    finally { mark(key, false) }
  }
  async function doClose(positionId = null) {
    const key = positionId || 'all'
    mark(key, true); setErr(null)
    try {
      const m = failMsg(await api.closePositions(active, positionId))
      if (m) setErr(m)
      else setSnap((s) => (s ? { ...s, positions: positionId == null ? [] : s.positions.filter((p) => String(p.position_id) !== String(positionId)) } : s))
    } catch (e) { setErr(e.message) }
    finally { mark(key, false) }
  }
  function requestClose(target) {          // target = {position_id, label}
    if (noConfirm) doClose(target.position_id)
    else { setConfirmAgain(false); setConfirm(target) }
  }
  function confirmClose() {
    if (confirmAgain) { setNoConfirm(true); localStorage.setItem(NO_CONFIRM_KEY, '1') }
    doClose(confirm.position_id)
    setConfirm(null)
  }
  // A trade from the panel goes: check → (usually) straight out. The modal only
  // appears when the check found something, which is the whole point of doing
  // the arithmetic server-side before any model is involved.
  async function submit(side) {
    const symbol = tradeSymbol.trim() || snap?.positions?.[0]?.symbol
    const volume = Number(lots)
    if (!symbol || !volume || volume <= 0 || ticketBusy) return
    setTicketBusy(true)
    setTicketMsg(null)
    try {
      const ctx = await api.tradePrecheck({ symbol, side, volume, account: active })
      const advisory = ctx.suggestion?.advisory ? ctx.suggestion : null
      // Straight through only when there is NOTHING to say. A warning still
      // earns the one interruption — being outside your own trading hours, or a
      // trade the engine could not check, is not "all well" just because it is
      // not fatal. `ok` stays about BLOCKING, which is what the server enforces.
      const quiet = ctx.ok && !ctx.issues?.length && !(ctx.suggestion && !ctx.suggestion.advisory)
      if (quiet) {
        // Straight through — but WITH the stop and target the engine sized to
        // their own rule. "Through the risk settings" should mean the trade
        // carries them, not merely that it was measured against them.
        await place({ symbol, side, volume, override: false,
                      sl: advisory?.sl, tp: advisory?.tp })
      } else {
        setGate(ctx)
      }
    } catch (e) {
      setTicketMsg(e.message)
    } finally {
      setTicketBusy(false)
    }
  }

  async function place({ symbol, side, volume, sl, tp, override }) {
    const sym = symbol || gate?.trade?.symbol
    const sd = side || gate?.trade?.side
    const r = await api.tradeExecute({
      symbol: sym, side: sd, volume, sl, tp, account: active, override: !!override,
    })
    setTicketMsg(`${sd} ${volume} ${sym} placed.`)
    setTimeout(() => setTicketMsg(null), 6000)
    return r
  }


  function openChart(symbol) {
    const id = `${active}:${symbol}`
    setCharts((cs) => (cs.some((c) => c.id === id) ? cs : cs.concat({ id, symbol, account: active })))
  }
  function closeChart(id) {
    setCharts((cs) => cs.filter((c) => c.id !== id))
    try { localStorage.removeItem(`arrissa.chart.${id}`) } catch { /* ignore */ }
  }

  return (
    <>
      <button className="live-fab" onClick={() => setOpen((o) => !o)} title="Live positions">
        <Briefcase size={22} strokeWidth={2} />
      </button>

      {/* the remembered drag position is DESKTOP-only — applying it on a phone would
          override the full-screen-sheet CSS and park the sheet off-screen */}
      {open && (
        <div className={'live-modal' + (mobile ? ' live-modal--mobile' : '')} ref={modalRef}
             style={!mobile && pos ? { left: pos.left, top: pos.top, right: 'auto', bottom: 'auto' } : undefined}>
          <div className="live-head" onMouseDown={onDragStart}>
            <div className="live-tabs">
              {!acctsReady
                ? [0, 1].map((i) => <span key={i} className="live-tab-ghost live-ghost" aria-hidden="true" />)
                : accounts.map((a) => (
                    <button key={a.account_number}
                            className={'live-tab' + (a.account_number === active ? ' live-tab--active' : '')}
                            onClick={() => { setActive(a.account_number); setActiveAccount(a.account_number, { broker: a.platform === 'tradelocker' ? 'tradelocker' : 'exness' }) }}>
                      {/* An account number alone does not say whose it is, and
                          these tabs mix brokers. The mark does. */}
                      <BrokerLogo size={16}
                                  broker={a.platform === 'tradelocker' ? 'tradelocker' : 'exness'} />
                      {a.account_number}
                      <span className="live-tab-tag">{a.is_real ? 'Real' : 'Demo'}</span>
                    </button>
                  ))}
            </div>
            <button className="live-close" onClick={() => setOpen(false)} title="Close"><X size={18} /></button>
          </div>

          <div className="live-stats">
            <Stat label="Balance" value={money(snap?.balance)} />
            <Stat label="Equity" value={money(snap?.equity)} />
            <Stat label="Floating P/L" value={money(snap?.floating_profit)} cls={pnl(snap?.floating_profit)} />
          </div>

          <div className="live-status">
            <span className={'live-dot' + (status === 'live' ? ' live-dot--on' : '')} />
            {status === 'live' ? 'Live' : status === 'connecting' ? 'Connecting…'
              : status === 'error' ? (errMsg || 'Connection error') : 'Disconnected'}
          </div>

          <div className="live-positions">
            <div className="live-row live-row--head">
              <span>Symbol</span><span>Side</span><span>Lots</span><span className="live-right">P/L</span><span />
            </div>
            {!snap ? (
              <p className="live-empty">Loading…</p>
            ) : snap.positions.length === 0 ? (
              <p className="live-empty">No open trades</p>
            ) : (
              snap.positions.map((p) => {
                const isOpen = expanded === p.position_id
                const label = `${p.symbol} ${p.side} ${p.volume}`
                return (
                  <div key={p.position_id}>
                    <div className={'live-row live-row--trade' + (isOpen ? ' live-row--open' : '')}
                         onClick={() => setExpanded(isOpen ? null : p.position_id)}>
                      <span className="live-sym">
                        <InstrumentFlag symbol={p.symbol} size="sm" />
                        {p.symbol}
                      </span>
                      <span className={'live-side live-side--' + p.side}>{p.side}</span>
                      <span>{p.volume}</span>
                      <span className={'live-right ' + pnl(p.profit)}>{money(p.profit)}</span>
                      <span className="live-row-btns">
                        <button className="live-rowbtn" title="Show chart"
                                onClick={(e) => { e.stopPropagation(); openChart(p.symbol) }}>
                          <CandlestickChart size={14} strokeWidth={1.75} />
                        </button>
                        <button className="live-rowbtn live-rowbtn--danger" title="Close this trade"
                                onClick={(e) => { e.stopPropagation(); requestClose({ position_id: p.position_id, label }) }}>
                          <Trash2 size={14} strokeWidth={1.75} />
                        </button>
                      </span>
                    </div>
                    {isOpen && (
                      <div className="live-row-actions">
                        <button className="live-mini-btn" disabled={busy[p.position_id]}
                                onClick={() => doBreakEven(p.position_id)}>
                          <Scale size={13} strokeWidth={1.9} /> Break even
                        </button>
                        <button className="live-mini-btn live-mini-btn--danger" disabled={busy[p.position_id]}
                                onClick={() => requestClose({ position_id: p.position_id, label })}>
                          <XCircle size={13} strokeWidth={1.9} /> Close
                        </button>
                      </div>
                    )}
                  </div>
                )
              })
            )}
          </div>

          {err && <div className="live-status" style={{ color: 'var(--danger)' }}>{err}</div>}

          {/* Quick ticket: SELL left, size middle, BUY right. The symbol sits
              above rather than in that row, so the three controls stay the shape
              they were asked for — and a ticket needs to know what it is buying. */}
          <div className="live-ticket-wrap">
          <div className="live-ticket-sym">
            <InstrumentFlag symbol={tradeSymbol || snap?.positions?.[0]?.symbol} size="sm" />
            <input className="tk-sym" placeholder={snap?.positions?.[0]?.symbol || 'Symbol'}
                   value={tradeSymbol}
                   onChange={(e) => setTradeSymbol(e.target.value.toUpperCase())}
                   aria-label="Symbol" />
          </div>
          <div className="live-ticket">
            <button className="tk-btn tk-btn--sell" disabled={ticketBusy}
                    onClick={() => submit('sell')}>Sell</button>
            <input className="tk-lots" value={lots} inputMode="decimal"
                   onChange={(e) => setLots(e.target.value.replace(/[^\d.]/g, ''))}
                   aria-label="Lot size" />
            <button className="tk-btn tk-btn--buy" disabled={ticketBusy}
                    onClick={() => submit('buy')}>Buy</button>
          </div>
          {ticketMsg && <p className="live-ticket-msg">{ticketMsg}</p>}
          </div>

          {snap && snap.positions.length > 0 && (
            <div className="live-foot">
              <button className="live-mini-btn" disabled={busy.all} onClick={() => doBreakEven(null)}>
                <Scale size={14} strokeWidth={1.9} /> Break even all
              </button>
              <button className="live-mini-btn live-mini-btn--danger" disabled={busy.all}
                      onClick={() => requestClose({ position_id: null, label: `all ${snap.positions.length} trades` })}>
                <XCircle size={14} strokeWidth={1.9} /> Close all
              </button>
            </div>
          )}
        </div>
      )}

      {/* Its own modal, deliberately not hung off the Live card: it is a decision
          about money, and it must survive the panel being dragged or scrolled. */}
      {gate && (
        <RiskModal
          ctx={gate}
          onClose={() => setGate(null)}
          onPlace={({ volume, sl, tp, override }) =>
            place({ symbol: gate.trade.symbol, side: gate.trade.side, volume, sl, tp, override })}
        />
      )}

      {confirm && (
        <div className="modal-overlay" style={{ zIndex: 80 }} {...backdrop(() => setConfirm(null))}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 400 }}>
            <div className="modal-head">
              <AlertTriangle className="modal-warn-icon" size={18} strokeWidth={1.75} />
              <span className="modal-title">Close this trade?</span>
            </div>
            <p className="modal-body">
              {confirm.position_id ? <>Close <strong>{confirm.label}</strong>? This exits the position at market.</>
                : <>Close <strong>{confirm.label}</strong>? This exits every open position at market.</>}
            </p>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, margin: '4px 0 16px', cursor: 'pointer' }}>
              <input type="checkbox" checked={confirmAgain} onChange={(e) => setConfirmAgain(e.target.checked)} />
              Don’t ask again
            </label>
            <div className="modal-actions">
              <button className="btn btn--ghost" onClick={() => setConfirm(null)}>Cancel</button>
              <button className="btn btn--danger" onClick={confirmClose}>Close trade</button>
            </div>
          </div>
        </div>
      )}

      {/* detachable chart windows — stay open even when the panel is closed */}
      {charts.map((c, i) => (
        <ChartWindow key={c.id} id={c.id} symbol={c.symbol} account={c.account} index={i}
                     onClose={() => closeChart(c.id)} />
      ))}
    </>
  )
}

function Stat({ label, value, cls }) {
  return (
    <div className="live-stat">
      <span className="live-stat-label">{label}</span>
      {value == null
        ? <span className="live-stat-value live-ghost" aria-hidden="true">&nbsp;</span>
        : <span className={'live-stat-value ' + (cls || '')}>{value}</span>}
    </div>
  )
}
