import { useEffect, useRef, useState, useCallback } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { ArrowRight, Wrench, Square, ChevronDown, Check, X, AlertTriangle, Brain, Mic, Zap, Star, Wallet, Clock } from 'lucide-react'
import { Link, useParams, useNavigate } from 'react-router-dom'
import DashboardLayout from '../components/DashboardLayout.jsx'
import Dropdown from '../components/Dropdown.jsx'
import TradeChart from '../components/TradeChart.jsx'
import ToolResult from '../components/ToolResult.jsx'
import { useChats } from '../context/ChatsContext.jsx'
import { useBilling, billingChanged } from '../services/billing.js'
import { useCapabilities, useModule } from '../services/capabilities.js'
import { useActiveAccount, setActiveAccount } from '../services/activeAccount.js'
import * as api from '../services/api.js'
import BrokerLogo from '../components/BrokerLogo.jsx'

marked.setOptions({ breaks: true, gfm: true })
const md = (text) => ({ __html: DOMPurify.sanitize(marked.parse(text || '')) })

// The agent signals a question by ending its reply with "OPTIONS: a | b | c".
function extractOptions(text) {
  const m = /(?:^|\n)\s*OPTIONS:\s*(.+?)\s*$/i.exec(text || '')
  if (!m) return { body: text || '', options: [] }
  const options = m[1].split('|').map((s) => s.trim()).filter(Boolean)
  return { body: text.slice(0, m.index).trimEnd(), options }
}

// The agent emits actionable trade setups as ```trade fenced JSON blocks — pull
// them out of the message so they render as one-tap trade cards, not code.
function extractTrades(text) {
  const trades = []
  const body = (text || '').replace(/```trade\s*([\s\S]*?)```/gi, (_, json) => {
    try { const t = JSON.parse(json.trim()); if (t && t.symbol && t.side) trades.push(t) } catch { /* ignore */ }
    return ''
  })
  // fallback: if a trade didn't carry a confidence in its JSON, pick up a
  // "Confidence: N/5" the model wrote in the surrounding prose.
  const m = (text || '').match(/confidence[:\s]*([1-5])\s*\/\s*5/i)
  if (m) for (const t of trades) if (t.confidence == null) t.confidence = Number(m[1])
  return { body: body.trim(), trades }
}

// "limit" and "stop" are not decoration. A retest short is a SELL LIMIT, and
// asking for a plain "sell" fills it at market immediately — at a worse price,
// skipping the level the whole idea depended on.
function orderWord(t) {
  const kind = String(t.order_type || '').toLowerCase()
  return (kind === 'limit' || kind === 'stop') ? `${t.side} ${kind}` : String(t.side || '')
}

function buildTradeCmd(t) {
  const parts = [`Place a ${orderWord(t)} of ${t.volume || 0.01} ${t.symbol}`]
  if (t.entry != null) parts.push(`entry ${t.entry}`)
  if (t.sl != null && t.sl !== 0) parts.push(`SL ${t.sl}`)
  if (t.tp != null && t.tp !== 0) parts.push(`TP ${t.tp}`)
  const body = parts.join(' ')
  // A setup with a time on it is a different instruction, and sending "execute
  // it now" for one would place at the wrong moment — the exact mistake the
  // card exists to prevent.
  return t.at
    ? `${body}. Schedule it for ${t.at} — do not place it now, schedule it.`
    : `${body}. Execute it now.`
}

// "at 15:30", "2026-08-03T14:00Z", "in 30 minutes" — shown as the agent wrote
// it. Parsing it here would only invent a second opinion about what it meant.
function whenLabel(at) {
  const d = new Date(at)
  if (!Number.isNaN(d.getTime()) && /\d{4}-\d{2}-\d{2}/.test(String(at))) {
    return d.toLocaleString([], { month: 'short', day: 'numeric',
                                  hour: '2-digit', minute: '2-digit' })
  }
  return String(at)
}

const PROVIDER_LABEL = { anthropic: 'Claude', openai: 'OpenAI', deepseek: 'DeepSeek' }

// Whisper hallucinates these on silent/near-silent audio — never send them as a message.
const WHISPER_HALLUCINATIONS = new Set([
  'you', 'thank you', 'thanks', 'thanks for watching', 'thank you for watching',
  'bye', 'okay', 'ok', 'uh', 'um', 'so', 'the', 'please subscribe',
])

const EXAMPLES = [
  'What are my open positions and floating P/L?',
  'Buy 0.1 XAUUSD with 3000 point SL and 5000 point TP',
  'Break even all my winning gold trades',
  "Show this week's realised profit summary",
]

export default function Dashboard() {
  const hasExness = useModule('exness')
  const [models, setModels] = useState([])          // [{provider, model}]
  const [cfgLoaded, setCfgLoaded] = useState(false) // AI config fetched? (avoids no-model flash)
  const [model, setModel] = useState('')            // "provider:model"
  const [pool, setPool] = useState([])              // account objects
  const [acctsLoaded, setAcctsLoaded] = useState(false) // accounts fetched? (reserve space to avoid jitter)
  const [chatAccounts, setChatAccounts] = useState([]) // account numbers selected for chat
  const [messages, setMessages] = useState([])      // turn objects
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [voiceError, setVoiceError] = useState(null)
  const [focused, setFocused] = useState(false)      // input focus → reveal example prompts
  const [multiline, setMultiline] = useState(false)  // composer > 1 line → send button drops to bottom
  const [acctOpen, setAcctOpen] = useState(false)    // mobile: the in-input account picker popup

  const { activeId, setActiveId, refresh: refreshChats } = useChats()
  const billing = useBilling()
  const caps = useCapabilities()
  // Locked means "this instance bills, and you have not paid" — never simply
  // "you have no plan", because a Community instance has no plans at all.
  const locked = caps ? caps.chat === false : (!!billing && !billing.active)
  const sharedActive = useActiveAccount()        // linked with the live panel
  const { chatId } = useParams()         // the chat id from the URL (permanent link)
  const navigate = useNavigate()
  const abortRef = useRef(null)
  const scrollRef = useRef(null)
  const inputRef = useRef(null)          // the textarea (for auto-grow)
  const acctRef = useRef(null)           // the mobile account-picker wrapper (outside-click close)
  const recorderRef = useRef(null)       // MediaRecorder
  const chunksRef = useRef([])           // recorded audio chunks
  const streamRef = useRef(null)         // mic MediaStream (to stop tracks)
  const audioCtxRef = useRef(null)       // meter: AudioContext
  const rafRef = useRef(null)            // meter: animation frame
  const levelRef = useRef(0)             // meter: peak RMS heard while recording
  const loadedRef = useRef(undefined)   // which chat's messages are currently displayed
  const activeIdRef = useRef(activeId)  // freshest activeId for async persistence
  const messagesRef = useRef(messages)  // freshest history for building the working copy

  useEffect(() => { activeIdRef.current = activeId }, [activeId])

  // the URL is the source of truth for which chat is open — so a refresh (or a
  // shared/bookmarked link) restores that chat instead of dropping to a new one.
  useEffect(() => { setActiveId(chatId || null) }, [chatId, setActiveId])
  useEffect(() => { messagesRef.current = messages }, [messages])

  useEffect(() => {
    // load persisted selection first, then reconcile with what's available
    api.getPrefs().catch(() => ({ chat_model: null, chat_accounts: [] })).then((prefs) => {
      const saved = prefs || { chat_model: null, chat_accounts: [] }

      api.getAIConfig().then((r) => {
        // Whatever this edition offers: the branded tiers on cloud, the models
        // the user picked from their own providers on a Community box. Same
        // shape either way, so nothing here has to know which.
        const avail = (r.models || []).filter((m) => m.available)
        setModels(avail)
        const savedOk = saved.chat_model && avail.some((m) => m.key === saved.chat_model)
        setModel(savedOk ? saved.chat_model : (avail[0]?.key || ''))
      }).catch(() => {}).finally(() => setCfgLoaded(true))

      Promise.all([
        hasExness ? api.getExnessAccounts().catch(() => ({ accounts: [], selected: [] }))
                  : Promise.resolve({ accounts: [], selected: [] }),
        api.getAllAccounts().catch(() => ({ tradelocker: { connections: [] } })),
      ]).then(([ex, all]) => {
        const selected = ex.selected || []
        // Exness: only the MT5 accounts explicitly selected (never archived)
        // `selected` comes back from JSONB as STRINGS; account_number is a
        // NUMBER. ['63791908'].includes(63791908) is false, so every Exness
        // account was filtered out and the picker showed only TradeLocker —
        // while the Accounts page, reading the same data differently, showed
        // them all. The TradeLocker branch below already compares as strings.
        const exPool = (ex.accounts || []).filter(
          (a) => !a.is_archived && a.platform === 'mt5'
            && selected.map(String).includes(String(a.account_number)))
        // TradeLocker: the accounts made available to the app. `available` is
        // null when the user has never chosen, which means all of them.
        const tlAvail = all.tradelocker?.available ?? null
        const tlPool = (all.tradelocker?.connections || []).flatMap((c) =>
          (c.accounts || []).filter(
            (a) => tlAvail === null || tlAvail.map(String).includes(String(a.account_id)))
          .map((a) => ({
            account_number: Number(a.account_id) || a.account_id,
            account_type: 'TradeLocker',
            is_real: a.environment === 'live',
            is_archived: false,
            platform: 'tradelocker',
          })))
        const p = [...exPool, ...tlPool]
        setPool(p)
        const savedAccts = Array.isArray(saved.chat_accounts)
          ? saved.chat_accounts.filter((n) => p.some((a) => a.account_number === n))
          : []
        setChatAccounts(savedAccts.length ? savedAccts : p.map((a) => a.account_number))
      }).catch(() => {}).finally(() => setAcctsLoaded(true))
    })
  }, [hasExness])

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  // close the mobile account picker on an outside click
  useEffect(() => {
    if (!acctOpen) return
    const onDoc = (e) => { if (acctRef.current && !acctRef.current.contains(e.target)) setAcctOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [acctOpen])

  // load the active chat when it changes (sidebar click / New chat)
  useEffect(() => {
    if (loadedRef.current === activeId) return
    loadedRef.current = activeId
    if (!activeId) { setMessages([]); return }
    api.getChat(activeId)
      .then((c) => setMessages(c.messages || []))
      .catch(() => { navigate('/dashboard', { replace: true }) })   // gone/invalid → new chat
  }, [activeId, navigate])

  // save the finished conversation (explicit — not an effect, so no races)
  async function persist(msgs) {
    try {
      if (activeIdRef.current) {
        await api.updateChat(activeIdRef.current, { messages: msgs })
      } else {
        const first = msgs.find((m) => m.role === 'user')
        const title = (first?.text || 'New chat').slice(0, 48)
        const c = await api.createChat(title, msgs)
        loadedRef.current = c.id
        activeIdRef.current = c.id
        setActiveId(c.id)
        navigate('/dashboard/' + c.id, { replace: true })   // give the new chat a permanent link
      }
      refreshChats()
    } catch { /* ignore */ }
  }

  async function send(promptText) {
    const text = (promptText ?? input).trim()
    if (!text || busy || !model) return
    if (locked) { navigate('/billing'); return }   // no subscription → send them to Plans

    const base = messagesRef.current                 // displayed history so far
    const userMsg = { role: 'user', text }
    const assistant = { role: 'assistant', thinking: '', text: '', actions: [], memory: [], error: null, done: false }
    const render = () => setMessages([...base, userMsg, { ...assistant, actions: assistant.actions.slice() }])
    render()
    setInput('')
    setBusy(true)

    const apiMessages = [...base, userMsg].map((m) => ({
      role: m.role,
      content: m.role === 'user' ? m.text : (m.text && m.text.trim() ? m.text : '(completed actions)'),
    }))

    // mutate the single local assistant object, then re-render from it
    function onEvent(ev) {
      if (ev.type === 'thinking') assistant.thinking += ev.text
      else if (ev.type === 'text') assistant.text += ev.text
      else if (ev.type === 'tool_call') assistant.actions.push({ id: ev.id, name: ev.name, input: ev.input, result: null })
      else if (ev.type === 'tool_result') { const a = assistant.actions.find((x) => x.id === ev.id); if (a) a.result = ev.result }
      else if (ev.type === 'memory') assistant.memory = [...(assistant.memory || []), ev.note]
      else if (ev.type === 'error') assistant.error = ev.error
      render()
    }

    const controller = new AbortController()
    abortRef.current = controller
    try {
      await api.agentChat(
        { model, messages: apiMessages, accounts: chatAccounts },
        onEvent,
        controller.signal,
      )
    } catch (e) {
      if (e.name !== 'AbortError') assistant.error = e.message
    } finally {
      assistant.done = true
      const finalMsgs = [...base, userMsg, { ...assistant, actions: assistant.actions.slice() }]
      setMessages(finalMsgs)
      setBusy(false)
      abortRef.current = null
      persist(finalMsgs)
      billingChanged()   // credits were spent → refresh the meter
    }
  }

  function stop() {
    abortRef.current?.abort()
  }

  function changeModel(v) {
    setModel(v)
    api.setPrefs({ chat_model: v }).catch(() => {})
  }

  const brokerOf = (num) =>
    (pool.find((a) => a.account_number === num)?.platform === 'tradelocker' ? 'tradelocker' : 'exness')

  function toggleAccount(num) {
    const on = chatAccounts.includes(num)
    const next = on ? chatAccounts.filter((x) => x !== num) : [...chatAccounts, num]
    setChatAccounts(next)
    api.setPrefs({ chat_accounts: next }).catch(() => {})
    // Link to the live panel: the account you just picked becomes THE active one
    // (the most recent input change wins); deselecting the active one falls back to
    // another still-selected account.
    if (!on) setActiveAccount(num, { broker: brokerOf(num) })
    else if (num === sharedActive && next.length) setActiveAccount(next[next.length - 1], { broker: brokerOf(next[next.length - 1]) })
  }

  // A change made in the live panel selects that account in the composer too.
  useEffect(() => {
    if (sharedActive == null || chatAccounts.includes(sharedActive)) return
    if (!pool.some((a) => a.account_number === sharedActive)) return
    const next = [...chatAccounts, sharedActive]
    setChatAccounts(next)
    api.setPrefs({ chat_accounts: next }).catch(() => {})
  }, [sharedActive, pool, chatAccounts])

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  // grow the textarea with its content (up to the CSS max-height, then it scrolls).
  // When empty, clear the inline height so the CSS single-line height applies — an
  // inline height would otherwise win over CSS and leave the box deformed/too tall.
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    if (!input) { el.style.height = ''; setMultiline(false); return }
    el.style.height = 'auto'
    const h = el.scrollHeight
    el.style.height = Math.min(h, 200) + 'px'
    setMultiline(h > 55)   // more than one line → button drops to the bottom
  }, [input, recording])

  // ── voice input (Whisper) ───────────────────────────────────────────────
  function pickMime() {
    const cands = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus']
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported) {
      for (const c of cands) { if (MediaRecorder.isTypeSupported(c)) return c }
    }
    return ''
  }

  async function startRecording() {
    setVoiceError(null)
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setVoiceError('Voice input needs a secure (https/localhost) context and a supported browser.')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      })
      streamRef.current = stream
      startMeter(stream)                   // watch the actual mic level (detect silence)
      const mime = pickMime()
      const mr = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream)
      chunksRef.current = []
      mr.ondataavailable = (e) => { if (e.data && e.data.size) chunksRef.current.push(e.data) }
      mr.onstop = onRecordingStop
      mr.start(200)                        // flush chunks every 200ms (reliable capture)
      recorderRef.current = mr
      setRecording(true)
    } catch {
      setVoiceError('Microphone access was blocked.')
      setRecording(false)
    }
  }

  function startMeter(stream) {
    try {
      const AC = window.AudioContext || window.webkitAudioContext
      if (!AC) return
      const ac = new AC()
      audioCtxRef.current = ac
      const analyser = ac.createAnalyser()
      analyser.fftSize = 512
      ac.createMediaStreamSource(stream).connect(analyser)
      const buf = new Uint8Array(analyser.fftSize)
      levelRef.current = 0
      const tick = () => {
        analyser.getByteTimeDomainData(buf)
        let sum = 0
        for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v }
        const rms = Math.sqrt(sum / buf.length)
        if (rms > levelRef.current) levelRef.current = rms
        rafRef.current = requestAnimationFrame(tick)
      }
      tick()
    } catch { /* metering is best-effort */ }
  }

  function stopMeter() {
    if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null }
    if (audioCtxRef.current) { try { audioCtxRef.current.close() } catch { /* noop */ } audioCtxRef.current = null }
  }

  function stopTracks() {
    stopMeter()
    if (streamRef.current) { streamRef.current.getTracks().forEach((t) => t.stop()); streamRef.current = null }
  }

  function stopRecording() {              // finish → transcribe → send
    const mr = recorderRef.current
    if (mr && mr.state !== 'inactive') { try { mr.requestData() } catch { /* noop */ } mr.stop() }
    else { setRecording(false); stopTracks() }
  }

  function cancelRecording() {            // discard, don't transcribe
    const mr = recorderRef.current
    chunksRef.current = []
    if (mr && mr.state !== 'inactive') { mr.onstop = null; mr.stop() }
    setRecording(false)
    stopTracks()
  }

  async function onRecordingStop() {
    setRecording(false)
    const level = levelRef.current          // peak mic level heard (0 = silence)
    stopTracks()
    const mr = recorderRef.current
    const chunks = chunksRef.current
    const blob = chunks.length ? new Blob(chunks, { type: mr?.mimeType || chunks[0].type || 'audio/webm' }) : null
    // no real audio → don't send Whisper's silence hallucination ("you"/"thank you")
    if (!blob || blob.size < 1200 || level < 0.015) {
      setVoiceError('No speech detected — check your microphone is on and unmuted, then try again.')
      return
    }
    setTranscribing(true)
    try {
      const { text } = await api.transcribe(blob)
      const t = (text || '').trim()
      const bare = t.toLowerCase().replace(/[.!?,…]/g, '').trim()
      if (!t || (t.length <= 18 && WHISPER_HALLUCINATIONS.has(bare))) {
        setVoiceError('Didn’t catch any speech — please try again.')
      } else {
        setInput(t); send(t)                // show what was heard, then send it
      }
    } catch (e) {
      setVoiceError(e.message || 'Transcription failed.')
    } finally {
      setTranscribing(false)
    }
  }

  const noModels = models.length === 0
  // only surface the "no model" message once the config has actually loaded —
  // otherwise it flashes on every reload before getAIConfig resolves.
  const noModelsReady = cfgLoaded && noModels
  const composerDisabled = noModels || locked   // no model set OR no active subscription
  const isEmpty = messages.length === 0

  // account chips + hint ABOVE the input, then the input — reused in both the
  // centered empty state and the pinned-bottom state
  const composer = (
    <>
      {isEmpty && (
        <>
      <div className="chat-controls">
        {!acctsLoaded
          ? /* reserve the row's space with ghost chips so real chips don't shift the UI */
            [0, 1].map((i) => <span key={i} className="acct-chip--ghost live-ghost" aria-hidden="true" />)
          : pool.map((a) => {
              const on = chatAccounts.includes(a.account_number)
              return (
                <button key={a.account_number}
                        className={`acct-chip ${on ? 'acct-chip--on' : ''}`}
                        onClick={() => toggleAccount(a.account_number)}
                        title={`${a.account_type} · ${a.is_real ? 'Real' : 'Demo'}`}>
                  {on && <Check size={12} strokeWidth={2.5} />}
                  {/* The pool mixes brokers, so the number alone is ambiguous. */}
                  <BrokerLogo size={15} broker={a.platform === 'tradelocker' ? 'tradelocker' : 'exness'} />
                  {a.account_number}
                  <span className="acct-chip-tag">{a.is_real ? 'Real' : 'Demo'}</span>
                </button>
              )
            })}
      </div>
      <p className="chat-hint">Arrissa can place real trades. Review actions carefully.</p>
        </>
      )}
      <div className={'chat-composer' + (isEmpty ? ' chat-composer--empty' : '') + (recording ? ' chat-composer--rec' : '') + (multiline ? ' chat-composer--multiline' : '')}>
        {/* mobile only: accounts icon on the left opens the account picker */}
        <div className="chat-acct-wrap" ref={acctRef}>
          <button type="button" className={'chat-acct-btn' + (acctOpen ? ' chat-acct-btn--open' : '')}
                  onClick={() => setAcctOpen((o) => !o)} title="Choose accounts" aria-label="Choose accounts">
            <Wallet size={18} strokeWidth={1.9} />
            {chatAccounts.length > 0 && <span className="chat-acct-badge">{chatAccounts.length}</span>}
          </button>
          {acctOpen && (
            <div className="chat-acct-pop">
              <div className="chat-acct-pop-head">Accounts</div>
              {pool.length === 0 ? (
                <div className="chat-acct-empty">No accounts connected</div>
              ) : pool.map((a) => {
                const on = chatAccounts.includes(a.account_number)
                return (
                  <button key={a.account_number} type="button"
                          className={'chat-acct-opt' + (on ? ' chat-acct-opt--on' : '')}
                          onClick={() => toggleAccount(a.account_number)}>
                    <span className="chat-acct-check">{on && <Check size={13} strokeWidth={3} />}</span>
                    <BrokerLogo size={16} broker={a.platform === 'tradelocker' ? 'tradelocker' : 'exness'} />
                    <span className="chat-acct-num">{a.account_number}</span>
                    <span className="chat-acct-tag">{a.is_real ? 'Real' : 'Demo'}</span>
                  </button>
                )
              })}
            </div>
          )}
        </div>
        {recording ? (
          <div className="chat-recording">
            <span className="rec-wave" aria-hidden="true"><i /><i /><i /><i /><i /></span>
            <span className="rec-label">Listening… tap ✓ to send</span>
            <button className="rec-cancel" onClick={cancelRecording} title="Cancel">
              <X size={16} strokeWidth={2} />
            </button>
          </div>
        ) : (
          <textarea
            ref={inputRef}
            className="chat-input"
            placeholder={locked ? 'Subscribe to chat with Arrissa…' : noModelsReady ? 'Connect an AI provider to start…' : 'Message Arrissa…'}
            value={input}
            rows={1}
            disabled={composerDisabled}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
          />
        )}
        {busy ? (
          <button className="chat-send chat-send--stop" onClick={stop} title="Stop">
            <Square size={15} strokeWidth={2} fill="currentColor" />
          </button>
        ) : recording ? (
          <button className="chat-send chat-send--rec" onClick={stopRecording} title="Send voice">
            <Check size={19} strokeWidth={2.5} />
          </button>
        ) : transcribing ? (
          <button className="chat-send chat-send--go" disabled title="Transcribing">
            <span className="chat-spinner" />
          </button>
        ) : input.trim() ? (
          <button className="chat-send chat-send--go" onClick={() => send()} title="Send">
            <ArrowRight size={19} strokeWidth={2.25} />
          </button>
        ) : (
          <button className="chat-send chat-send--mic" onClick={startRecording}
                  disabled={composerDisabled} title="Voice input">
            <Mic size={19} strokeWidth={2} />
          </button>
        )}
      </div>
      {voiceError && <div className="chat-voice-error">{voiceError}</div>}
    </>
  )

  // The model selector lives in the topbar, right of the "Chat" title.
  const modelSelector = (
    <Dropdown
      className="topbar-model"
      value={model}
      onChange={changeModel}
      disabled={noModels}
      align="left"
      placeholder={noModelsReady ? 'No models — add in Connections' : 'Select model'}
      options={models.map((m) => ({ value: m.key, label: m.name }))}
    />
  )

  return (
    <DashboardLayout title="Chat" titleExtra={modelSelector} flush>
      <div className="chat">
        {isEmpty ? (
          <div className="chat-center">
            {locked ? (
              <div className="alert alert--warn chat-config" style={{ maxWidth: 620 }}>
                <AlertTriangle size={16} strokeWidth={1.75} />
                <span>Your account is view-only — no active subscription. <Link to="/billing">Choose a plan</Link> to start chatting and trading with Arrissa.</span>
              </div>
            ) : noModelsReady ? (
              <div className="alert alert--warn chat-config" style={{ maxWidth: 620 }}>
                <AlertTriangle size={16} strokeWidth={1.75} />
                <span>No AI model configured. Connect a provider and pick a model in <Link to="/connections">Connections</Link>.</span>
              </div>
            ) : (
              <div className="chat-welcome">
                <h2>How can I help you trade?</h2>
                <p>Ask in plain language. I can read prices, open and manage positions, and act across your selected accounts.</p>
              </div>
            )}
            <div className="chat-composer-wrap chat-composer-wrap--inline">{composer}</div>
            {!noModelsReady && (
              <div className={'chat-examples' + (focused ? '' : ' chat-examples--hidden')}>
                {EXAMPLES.map((ex) => (
                  <button key={ex} className="chat-example"
                          onMouseDown={(e) => e.preventDefault()}   /* keep input focused so the click registers */
                          onClick={() => send(ex)}>{ex}</button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <>
            <div className="chat-scroll" ref={scrollRef}>
              <div className="chat-thread">
                {messages.map((m, i) => (
                  <Message key={i} m={m} busy={busy && i === messages.length - 1} onOption={send} />
                ))}
              </div>
            </div>
            <div className="chat-composer-wrap">{composer}</div>
          </>
        )}
      </div>
    </DashboardLayout>
  )
}

function TradeCard({ trade, onPlace }) {
  const buy = String(trade.side).toLowerCase() === 'buy'
  const conf = Math.max(0, Math.min(5, Math.round(Number(trade.confidence) || 0)))
  const Level = ({ label, v }) => (v == null || v === 0 ? null : (
    <div className="trade-card-lvl"><span>{label}</span><b>{v}</b></div>
  ))
  return (
    <div className="trade-card">
      <div className="trade-card-head">
        {/* Say LIMIT / STOP on the card. "SELL" on a retest setup reads as
            "sell now", which is the one thing it must not do. */}
        <span className={'trade-card-side trade-card-side--' + (buy ? 'buy' : 'sell')}>
          {orderWord(trade).toUpperCase() || (buy ? 'BUY' : 'SELL')}
        </span>
        <span className="trade-card-sym">{trade.symbol}</span>
        {trade.volume ? <span className="trade-card-vol">{trade.volume} lots</span> : null}
        {conf > 0 && (
          <span className="trade-card-stars" title={`Confidence ${conf} / 5`}>
            {[1, 2, 3, 4, 5].map((i) => (
              <Star key={i} size={15} strokeWidth={1.75}
                    fill={i <= conf ? 'currentColor' : 'none'}
                    className={'trade-card-star' + (i <= conf ? ' trade-card-star--on' : '')} />
            ))}
          </span>
        )}
      </div>
      <div className="trade-card-levels">
        <Level label="Entry" v={trade.entry} />
        <Level label="Stop" v={trade.sl} />
        <Level label="Target" v={trade.tp} />
      </div>
      {trade.at && (
        <div className="trade-card-when">
          <Clock size={14} strokeWidth={2} />
          <span>Opens {whenLabel(trade.at)}</span>
        </div>
      )}
      {trade.rationale && <p className="trade-card-note">{trade.rationale}</p>}
      <button className="btn btn--primary trade-card-btn" onClick={() => onPlace(trade)}>
        {trade.at
          ? <><Clock size={15} strokeWidth={2} /> Schedule this trade</>
          : <><Zap size={15} strokeWidth={2} /> Place this trade</>}
      </button>
    </div>
  )
}

function Message({ m, busy, onOption }) {
  if (m.role === 'user') {
    return (
      <div className="msg msg--user">
        <div className="msg-bubble">{m.text}</div>
      </div>
    )
  }

  const { body: optBody, options } = extractOptions(m.text)
  const { body, trades } = extractTrades(optBody)
  const empty = !m.thinking && !m.actions.length && !body && !trades.length && !m.error
  // charts the agent drew this turn are shown in the message itself, not buried
  // in the collapsed tool activity
  const charts = (m.actions || [])
    .filter((a) => a.name === 'show_chart' && a.result && a.result.chart)
    .map((a) => a.result)
  return (
    <div className="msg msg--assistant">
      <Activity m={m} busy={busy} hasBody={!!body} />

      {charts.map((c, i) => <TradeChart key={`${c.symbol}-${c.timeframe}-${i}`} spec={c} />)}

      {body ? <div className="msg-md" dangerouslySetInnerHTML={md(body)} /> : null}

      {trades.map((t, i) => <TradeCard key={i} trade={t} onPlace={(tr) => onOption(buildTradeCmd(tr))} />)}

      {options.length > 0 && (
        <div className="msg-options">
          {options.map((o) => (
            <button key={o} className="msg-option" onClick={() => onOption(o)}>{o}</button>
          ))}
        </div>
      )}

      {m.error && <div className="alert alert--danger" style={{ marginTop: 8 }}>{m.error}</div>}

      {m.memory && m.memory.length > 0 && (
        <div className="msg-memory">
          <Brain size={12} strokeWidth={1.75} />
          <span>Saved to memory: {m.memory.join(' · ')}</span>
        </div>
      )}

      {empty && busy && <div className="msg-typing"><span /><span /><span /></div>}
    </div>
  )
}

// Decide the single small grey status word for the current activity phase.
function activityLabel(m, busy, hasBody) {
  if (busy) {
    if (m.actions.some((a) => a.result === null)) return 'Actioning…'
    if (m.actions.length && !hasBody) return 'Processing…'
    if (hasBody) return 'Writing…'
    return 'Thinking…'
  }
  const n = m.actions.length
  if (n) return `Used ${n} ${n === 1 ? 'tool' : 'tools'}`
  return m.thinking ? 'Thought process' : ''
}

// Cascades the whole thinking + tool-call activity into one small grey line.
// Click to expand and see the reasoning and every tool call with its data.
function Activity({ m, busy, hasBody }) {
  const [open, setOpen] = useState(false)
  const hasDetail = m.thinking || m.actions.length
  if (!hasDetail) return null
  const label = activityLabel(m, busy, hasBody)
  return (
    <div className="activity">
      <button className="activity-head" onClick={() => setOpen((o) => !o)}>
        <span className={`activity-label ${busy ? 'activity-label--live' : ''}`}>{label}</span>
        <ChevronDown size={12} strokeWidth={2} className={`activity-caret ${open ? '' : 'closed'}`} />
      </button>
      {open && (
        <div className="activity-body">
          {m.thinking && <div className="think-body">{m.thinking}</div>}
          {m.actions.map((a) => <Action key={a.id} a={a} />)}
        </div>
      )}
    </div>
  )
}

function Action({ a }) {
  const [open, setOpen] = useState(false)
  const acc = a.input?.account
  const err = a.result && a.result.error
  const pending = a.result === null
  const cached = a.result && a.result.cached
  return (
    <div className={`action ${open ? 'action--open' : ''} ${err ? 'action--err' : ''}`}>
      <button className="action-head" onClick={() => setOpen((o) => !o)}>
        <Wrench size={13} strokeWidth={1.75} />
        <span className="action-name">{a.name}</span>
        {acc != null && <span className="action-acct">acct {acc}</span>}
        {cached && <span className="action-acct" title="Served from a 5-second cache — billed at 20%">cached</span>}
        <span className={`action-status ${pending ? 'action-status--run' : err ? 'action-status--err' : 'action-status--ok'}`}>
          {pending ? 'running…' : err ? <><X size={11} strokeWidth={3} /> failed</> : <><Check size={11} strokeWidth={3} /> done</>}
        </span>
        <ChevronDown size={13} strokeWidth={2} className={`action-caret ${open ? '' : 'closed'}`} />
      </button>
      {open && (
        <>
          <ActionArgs input={a.input} />
          {a.result != null && <ToolResult result={a.result} />}
        </>
      )}
    </div>
  )
}

function ActionArgs({ input }) {
  const entries = Object.entries(input || {}).filter(([k]) => k !== 'account')
  if (!entries.length) return null
  return (
    <div className="action-args">
      {entries.map(([k, v]) => (
        <span className="action-arg" key={k}><b>{k}</b> {typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
      ))}
    </div>
  )
}
