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

// Work out what a native drop inserted, and whether it needs an `&` welding it
// to what was already there.
//
// Dropping `impact=high` at the end of `symbol=XAUUSD` gives you
// `symbol=XAUUSDimpact=high` — one broken key — because the browser inserts
// exactly the string it was given and knows nothing about query strings. The
// separator cannot be baked into the dragged text either: at the START of the
// field it would need to trail rather than lead, and the drag has no idea where
// it will land.
//
// So it is fixed afterwards, from the only evidence available once the browser
// has already done it: the difference between the old value and the new one.
// The common prefix and suffix bracket the insertion, and the characters on
// either side of it say which separators are missing.
export function stitch(prev, next) {
  let a = 0
  while (a < prev.length && a < next.length && prev[a] === next[a]) a += 1
  let b = 0
  while (b < prev.length - a && b < next.length - a
         && prev[prev.length - 1 - b] === next[next.length - 1 - b]) b += 1
  const ins = next.slice(a, next.length - b)
  // A bare variable goes INSIDE a value — `symbol={{symbol}}` — so it must not
  // be separated from what it lands next to. Only a whole pair needs welding.
  if (!ins || !ins.includes('=')) return next
  const before = next.slice(0, a)
  const after = next.slice(next.length - b)
  // A separator is missing only when NEITHER side supplies one. The dropped
  // text can carry its own — dragging out of the middle of a call brings the
  // `&` with it — and adding a second gives `a=1&&b=2`, which is an empty pair.
  const sepEnd = (t) => /[&?;\n]$/.test(t)
  const sepStart = (t) => /^[&?;\n]/.test(t)
  const lead = (!before || sepEnd(before) || sepStart(ins)) ? '' : '&'
  const trail = (!after || sepStart(after) || after[0] === '=' || sepEnd(ins)) ? '' : '&'
  return before + lead + ins + trail + after
}

export default function ParamsField({ value = '', onChange, placeholder, big = false,
                                      inputRef, autoFocus, onKeyDown,
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
        /* The browser places a dropped string exactly where it was dropped —
           it is the only thing that knows the character offset under the
           pointer, so the drop itself is left alone. Two things still have to
           happen afterwards: a controlled textarea must be told its DOM value
           changed or the next keystroke reverts it, and a dropped key=value
           pair may need an `&` welding it to its neighbour. */
        onDrop={(e) => {
          const el = e.currentTarget
          const prev = value
          requestAnimationFrame(() => {
            if (!el || el.value === prev) return
            onChange(stitch(prev, el.value))
          })
        }}
      />
    </div>
  )
}
