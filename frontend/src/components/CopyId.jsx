import { useState } from 'react'
import { Copy, Check } from 'lucide-react'

// Copyable identifier pill (agent IDs). Rendered as SPANs, never a <button>, so it
// can live inside the agent cards — which are themselves buttons.
export default function CopyId({ value, label = 'ID', short = 8, title = 'Copy ID' }) {
  const [done, setDone] = useState(false)
  const id = String(value || '')
  const text = short && id.length > short * 2 ? `${id.slice(0, short)}…${id.slice(-4)}` : id

  function copy(e) {
    e.stopPropagation()      // don't trigger the card's own click (opens the agent)
    e.preventDefault()
    navigator.clipboard.writeText(id)
    setDone(true)
    setTimeout(() => setDone(false), 1500)
  }

  return (
    <span className="copy-id" onClick={copy} role="button" tabIndex={-1} title={`${title}: ${id}`}>
      {label && <span className="copy-id-label">{label}</span>}
      <code className="copy-id-val">{text}</code>
      {done ? <Check size={13} strokeWidth={2} /> : <Copy size={13} strokeWidth={1.75} />}
    </span>
  )
}
