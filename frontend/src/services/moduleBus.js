// Modules go on and off while the app is running, so anything built FROM them
// can go stale without a navigation to trigger a refetch — the More… menu, the
// flow-canvas palette, an open guide page for a module that just left.
//
// The Modules page fires `changed()` after every successful install, removal,
// enable or disable; everything that renders module-provided things subscribes.
// A window event rather than a context because the subscribers are scattered
// and none of them need the payload — only the news that it moved.
const EVENT = 'entrystation:modules-changed'

export const changed = () => window.dispatchEvent(new Event(EVENT))

export function onChanged(fn) {
  window.addEventListener(EVENT, fn)
  return () => window.removeEventListener(EVENT, fn)
}
