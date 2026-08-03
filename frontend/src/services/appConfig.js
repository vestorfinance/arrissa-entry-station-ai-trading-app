// App branding (name) — admin-configurable, fetched once and cached. useAppName()
// re-renders when it changes (e.g. the admin edits it). Sets document.title too.
import { useState, useEffect } from 'react'
import * as api from './api.js'

let _name = null

export function getAppName() { return _name || 'EntryStation' }

export function setAppNameCache(name) {
  _name = name || 'EntryStation'
  document.title = _name
  window.dispatchEvent(new Event('appname-change'))
}

export function useAppName() {
  const [n, setN] = useState(getAppName())
  useEffect(() => {
    const sync = () => setN(getAppName())
    if (_name == null) api.appConfig().then((c) => setAppNameCache(c.app_name)).catch(() => {})
    window.addEventListener('appname-change', sync)
    return () => window.removeEventListener('appname-change', sync)
  }, [])
  return n
}
