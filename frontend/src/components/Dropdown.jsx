import { useState, useRef, useEffect } from 'react'
import { ChevronDown, Check } from 'lucide-react'

// A custom, fully-styled dropdown that looks identical on every device/browser
// (native <select> menus are rendered by the OS and vary). Drop-in replacement:
//   <Dropdown value={v} onChange={setV} options={[{value,label}] | [string]} />
// onChange receives the selected VALUE (not an event).
export default function Dropdown({
  value, onChange, options = [], disabled = false,
  className = '', placeholder = 'Select…', align = 'left', title,
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey) }
  }, [open])

  const opts = options.map((o) => (o && typeof o === 'object' ? o : { value: o, label: String(o) }))
  const sel = opts.find((o) => String(o.value) === String(value))

  return (
    <div className={'dd ' + className + (disabled ? ' dd--disabled' : '')} ref={ref}>
      <button type="button" className="dd-btn" disabled={disabled} title={title}
              onClick={() => !disabled && setOpen((o) => !o)}>
        <span className="dd-label">{sel ? sel.label : placeholder}</span>
        <ChevronDown size={14} strokeWidth={2} className={'dd-caret' + (open ? ' dd-caret--open' : '')} />
      </button>
      {open && (
        <div className={'dd-menu dd-menu--' + align} role="listbox">
          {opts.map((o) => {
            const on = String(o.value) === String(value)
            return (
              <button type="button" key={String(o.value)} role="option" aria-selected={on}
                      className={'dd-opt' + (on ? ' dd-opt--on' : '')}
                      onClick={() => { onChange(o.value); setOpen(false) }}>
                <span className="dd-opt-label">{o.label}</span>
                {on && <Check size={14} strokeWidth={2.4} className="dd-opt-check" />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
