import { useRef } from 'react'

// A query string, coloured the way an editor colours one.
//
// A textarea cannot hold coloured spans — its value is plain text and that is
// the whole point of it. So the colour is a <pre> sitting exactly behind a
// transparent textarea: you type into the real control, and what you see is the
// markup underneath, aligned character for character.
//
// Everything about that alignment is load-bearing. The two elements must share
// font, size, line-height, padding, wrapping and scroll position, or the
// colouring drifts a pixel per line and the caret stops sitting on the letter it
// is next to. That is why the shared properties live in one CSS class applied to
// both, rather than being written twice and kept in step by hand.
//
// Why bother: `symbol={{symbol}}&timeframe=M15` is four different kinds of thing
// in one unbroken line, and the one people get wrong — a mistyped {{name}} that
// will never resolve — is invisible in plain text and obvious in colour.

// Splitting on the variable first means a `{{name}}` is recognised wherever it
// sits, including in the middle of a value.
const VAR = /(\{\{\s*[a-zA-Z_]\w*\s*\}\}|\$[a-zA-Z_]\w*)/g
const VAR_ONE = /^(\{\{\s*[a-zA-Z_]\w*\s*\}\}|\$[a-zA-Z_]\w*)$/
// Separators, then runs of everything else. Between them these two match any
// character at all, which is the property that matters: drop one and the colour
// slides off the text from that point on. An alternation that can fail to match
// somewhere would do exactly that, silently.
const PAIR = /([&\n;=?])|([^&\n;=?]+)/g

function tokens(text) {
  const out = []
  for (const chunk of text.split(VAR)) {
    if (!chunk) continue
    if (VAR_ONE.test(chunk)) { out.push({ t: 'var', s: chunk }); continue }
    let m
    PAIR.lastIndex = 0
    while ((m = PAIR.exec(chunk)) !== null) {
      out.push(m[1] ? { t: 'sep', s: m[1] } : { t: 'text', s: m[2] })
    }
  }
  // A run of text is a KEY when an '=' follows it and a value when nothing does.
  // Deciding it here, after the whole string is split, is what makes it hold
  // across a variable boundary — `sym={{s}}` still reads `sym` as a key.
  return out.map((tk, i) => (tk.t === 'text'
    ? { ...tk, t: out[i + 1]?.s === '=' ? 'key' : 'val' }
    : tk))
}

function paint(text) {
  const out = tokens(text).map((tk, i) => (
    <span className={'pf-' + tk.t} key={i}>{tk.s}</span>
  ))
  // <pre> does not render a trailing newline unless something follows it, so
  // while you are typing on a fresh line the paint would come up one line short.
  out.push('\n')
  return out
}

export default function ParamsField({ value = '', onChange, placeholder, big = false,
                                      inputRef, onDrop, autoFocus, onKeyDown,
                                      className = '' }) {
  const ownRef = useRef(null)
  const ref = inputRef || ownRef
  const preRef = useRef(null)

  return (
    <div className={'pf' + (big ? ' pf--big' : '') + (className ? ' ' + className : '')}>
      <pre className="pf-paint" ref={preRef} aria-hidden="true">{paint(value)}</pre>
      <textarea
        ref={ref}
        className="pf-input"
        value={value}
        placeholder={placeholder}
        spellCheck={false}
        autoFocus={autoFocus}
        onKeyDown={onKeyDown}
        autoFocus={autoFocus}
        onKeyDown={onKeyDown}
        onChange={(e) => onChange(e.target.value)}
        // The painted layer does not scroll itself, so it is pushed to wherever
        // the textarea has scrolled to. Without this the colour stays put while
        // the text moves.
        onScroll={(e) => {
          if (!preRef.current) return
          preRef.current.scrollTop = e.currentTarget.scrollTop
          preRef.current.scrollLeft = e.currentTarget.scrollLeft
        }}
        onDrop={onDrop}
      />
    </div>
  )
}
