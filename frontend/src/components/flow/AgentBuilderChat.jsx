import { useEffect, useRef, useState } from 'react'
import { Sparkles, ArrowUp, Minus, X } from 'lucide-react'
import * as store from '../../services/agents.js'

// Floating "Ask AI to build your agent" box on the flow page. Auto-opens (it
// replaces the briefcase/positions FAB here). Whatever the user types is sent
// to the AI flow builder, which returns a full flow; the parent applies it to
// the canvas via onBuilt().
export default function AgentBuilderChat({ agentId, onBuilt }) {
  const [open, setOpen] = useState(true)
  const [closed, setClosed] = useState(false)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [msgs, setMsgs] = useState([
    { role: 'ai', text: 'Ask AI to build your agent. Describe what it should watch and decide — '
      + 'e.g. "analyse gold using price, news and sentiment, then give a trade".' },
  ])
  const bodyRef = useRef(null)
  const taRef = useRef(null)

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [msgs, busy])

  const grow = (el) => { if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 140) + 'px' } }

  async function send() {
    const brief = text.trim()
    if (!brief || busy) return
    setMsgs((m) => [...m, { role: 'you', text: brief }])
    setText('')
    if (taRef.current) taRef.current.style.height = 'auto'
    setBusy(true)
    try {
      const res = await store.buildWithAI(agentId, { instruction: brief })
      onBuilt?.(res)
      const n = (res.flow?.nodes || []).length
      setMsgs((m) => [...m, { role: 'ai', text:
        `Built your agent — ${n} node${n === 1 ? '' : 's'} on the canvas${res.name ? ` ("${res.name}")` : ''}. `
        + 'Tell me what to change and I\'ll rebuild it.' }])
    } catch (e) {
      setMsgs((m) => [...m, { role: 'ai', text: 'Could not build that: ' + (e.message || 'error') }])
    } finally {
      setBusy(false)
    }
  }

  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  if (closed) {
    return (
      <button className="builder-fab" title="Ask AI to build your agent" onClick={() => { setClosed(false); setOpen(true) }}>
        <Sparkles size={20} strokeWidth={1.9} />
      </button>
    )
  }

  return (
    <div className={'builder-chat' + (open ? '' : ' builder-chat--min')}>
      <div className="builder-head" onClick={() => setOpen((o) => !o)}>
        <span className="builder-head-title"><Sparkles size={15} strokeWidth={2} /> Build with AI</span>
        <span className="builder-head-actions" onClick={(e) => e.stopPropagation()}>
          <button className="builder-icon" title={open ? 'Minimise' : 'Expand'} onClick={() => setOpen((o) => !o)}>
            <Minus size={15} strokeWidth={2} />
          </button>
          <button className="builder-icon" title="Close" onClick={() => setClosed(true)}>
            <X size={15} strokeWidth={2} />
          </button>
        </span>
      </div>

      {open && (
        <>
          <div className="builder-body" ref={bodyRef}>
            {msgs.map((m, i) => (
              <div key={i} className={'builder-msg builder-msg--' + m.role}>{m.text}</div>
            ))}
            {busy && <div className="builder-msg builder-msg--ai builder-msg--typing">Building…</div>}
          </div>
          <div className="builder-composer">
            <textarea
              ref={taRef}
              className="builder-input"
              rows={1}
              value={text}
              placeholder="Describe your agent…"
              spellCheck={false}
              disabled={busy}
              onChange={(e) => { setText(e.target.value); grow(e.target) }}
              onKeyDown={onKey}
            />
            <button className="builder-send" title="Build" onClick={send} disabled={busy || !text.trim()}>
              <ArrowUp size={16} strokeWidth={2.4} />
            </button>
          </div>
        </>
      )}
    </div>
  )
}
