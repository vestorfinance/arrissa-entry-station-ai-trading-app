// App branding (name) — admin-configurable, fetched once and cached. useAppName()
// re-renders when it changes (e.g. the admin edits it). Sets document.title too.
import { useState, useEffect } from 'react'
import * as api from './api.js'

let _name = null
// The whole payload, fetched once. Three separate places want something out of
// it (the name, the edition, whether this is a fresh install) and each fetching
// it for itself meant the same public request three times on first paint.
let _cfg = null
let _inflight = null

export function loadAppConfig() {
  if (_cfg) return Promise.resolve(_cfg)
  if (!_inflight) {
    _inflight = api.appConfig()
      .then((c) => { _cfg = c || {}; setAppNameCache(_cfg.app_name); return _cfg })
      .catch(() => {
        // Not cached: an unreachable server now should not decide what this
        // instance is for the rest of the session. Cloud is the safe guess —
        // it shows the marketing page rather than a set-up flow.
        _inflight = null
        return { edition: 'cloud', setup: false }
      })
  }
  return _inflight
}

// null until it is known. Callers that route on it should render nothing while
// it is null rather than guess, or the page paints once and is snatched away.
export function useAppConfig() {
  const [cfg, setCfg] = useState(_cfg)
  useEffect(() => { if (!_cfg) loadAppConfig().then(setCfg) }, [])
  return cfg
}

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
    if (_name == null) loadAppConfig().catch(() => {})
    window.addEventListener('appname-change', sync)
    return () => window.removeEventListener('appname-change', sync)
  }, [])
  return n
}

// Where the legal documents live for THIS edition.
//
// On the hosted service they are pages of this app. On a Community install they
// are not: those documents are the agreement between the operator and us, they
// are maintained by us, and a self-hosted copy would be a stale fork of a legal
// text the moment either is updated. So the links go to entrystation.com, and
// the instance still serves its own copies for anyone who deep-links one.
export const STORE_ORIGIN = 'https://entrystation.com'

export function useLegalBase() {
  const cfg = useAppConfig()
  if (cfg === null) return ''                     // unknown yet: keep it internal
  return cfg.edition === 'community' ? STORE_ORIGIN : ''
}
