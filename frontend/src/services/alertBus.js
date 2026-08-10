// Dismissing a toast should also clear it from the bell, and the two components
// are unrelated in the tree. A window event rather than shared state because
// neither needs the other's data — only the news that something changed.
const EVENT = 'entrystation:alerts-changed'

export const alertsChanged = () => window.dispatchEvent(new Event(EVENT))

export function onAlertsChanged(fn) {
  window.addEventListener(EVENT, fn)
  return () => window.removeEventListener(EVENT, fn)
}
