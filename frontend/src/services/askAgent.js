// Hand a question to the chat agent from anywhere in the app.
//
// The asker is usually not on the dashboard — a toast in the corner, a row in
// the notification bell — so this cannot be a direct call. It is also not enough
// to fire an event: navigating to the chat MOUNTS it, which happens after the
// event would have been dispatched, and the question would land in an empty
// room. So the question is PARKED, and the dashboard drains it when it is ready
// to send one.
let pending = null
const EVENT = 'entrystation:ask-agent'

export function ask(text) {
  const t = (text || '').trim()
  if (!t) return
  pending = t
  window.dispatchEvent(new Event(EVENT))
}

/** The parked question, if any. Reading it clears it — it is asked once. */
export function takePending() {
  const t = pending
  pending = null
  return t
}

export function onAsk(fn) {
  window.addEventListener(EVENT, fn)
  return () => window.removeEventListener(EVENT, fn)
}

// The one place an alert becomes a question, so the toast and the bell cannot
// drift apart. The headline and the detail both go in: the title alone is often
// too thin for the model to act on, and it has no other way to see the story.
export function promptForAlert(a) {
  if (!a) return ''
  const kind = a.kind === 'truth' ? 'this Truth Social post'
    : a.kind === 'news' ? 'this market news'
    : a.kind === 'calendar_soon' ? 'this upcoming economic release'
    : a.kind === 'calendar_out' ? 'this economic release'
    : 'this'
  const bits = [`Analyse ${kind} and tell me what opportunities I can get into RIGHT NOW because of it.`, '']
  bits.push(`Headline: ${a.title}`)
  if (a.body) bits.push(`Detail: ${a.body}`)
  if (a.symbols?.length) bits.push(`Instruments named: ${a.symbols.join(', ')}`)
  if (a.country) bits.push(`Country: ${a.country.toUpperCase()}`)
  if (a.at) bits.push(`When: ${a.at}`)
  bits.push('')
  bits.push('Be specific: which instruments, which direction, and where the trade '
    + 'would be invalidated. Check live prices before you answer — this may '
    + 'already be priced in, and if it is, say so instead of inventing a trade.')
  return bits.join('\n')
}
