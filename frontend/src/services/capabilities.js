// What this user may actually reach, as the SERVER sees it.
//
// The app used to work this out from the billing state — active subscription
// means chat, Elite plan means the API guides, admin means the Modules page.
// Every one of those is a question about the CLOUD. On a Community instance
// nobody has a plan and nobody is an admin, and inferring from billing left the
// owner looking at an empty app they had installed themselves.
//
// So the server decides and says so, and the frontend asks rather than guesses.
import { useEffect, useState } from 'react'
import * as api from './api.js'

// Persisted so a refresh knows the answer BEFORE the first paint. Without it
// every reload rendered the cloud defaults for a moment and then took them away
// again — a Plans & Billing entry appearing and vanishing on an instance that
// has no billing at all. localStorage is per-origin, so the two editions cannot
// see each other's answer.
const STORE_KEY = 'entrystation:capabilities'

function remembered() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)) || null } catch { return null }
}

let cache = remembered()
let confirmed = false        // has the SERVER answered yet in this page load?
let inflight = null
const subscribers = new Set()

export function cachedCapabilities() {
  return cache
}

// Nothing to ask when nobody is signed in.
//
// `/api/me` needs a session, and the app now has public pages — the homepage,
// login, a Buy link someone was sent. Every one of them mounted something that
// wanted capabilities, so every visitor's console opened on
// `GET /api/me 401 (Unauthorized)`: harmless, wasted, and the first thing a
// careful person sees when they look under the bonnet of a product about money.
//
// The empty answer is deliberately NOT remembered. `cache` is what stops a
// second request, so storing {} here would outlive the login that follows and
// leave a signed-in user's app believing they may do nothing at all.
const NOBODY = Object.freeze({})

export function fetchCapabilities(force = false) {
  if (cache && !force) return Promise.resolve(cache)
  if (!localStorage.getItem('auth_token')) return Promise.resolve(NOBODY)
  if (inflight) return inflight
  inflight = api.me()
    .then((me) => {
      cache = me.capabilities || {}
      confirmed = true
      try { localStorage.setItem(STORE_KEY, JSON.stringify(cache)) } catch { /* private mode */ }
      return cache
    })
    .catch(() => { cache = cache || {}; return cache })
    .finally(() => { inflight = null; subscribers.forEach((fn) => fn(cache)) })
  return inflight
}

/** Re-read after anything that could change what the user may do. */
export function capabilitiesChanged() {
  fetchCapabilities(true)          // keep the remembered value until the new one lands
}

/** Forget it on logout — the next person may not be the same person. */
export function forgetCapabilities() {
  cache = null
  confirmed = false
  try { localStorage.removeItem(STORE_KEY) } catch { /* ignore */ }
}

/**
 * Is this module installed and serving?
 *
 * Anything that calls a module's endpoint has to ask first. Calling
 * /api/exness/accounts when the Exness module is not installed is a 404 in
 * everyone's console on every page load, and a request the server has to refuse
 * — for an answer the frontend could already have known.
 *
 * Unknown (capabilities not loaded yet) reads as FALSE, so nothing fires
 * speculatively during the first render.
 */
export function useModule(id) {
  const caps = useCapabilities()
  return !!(caps?.active_modules || []).includes(id)
}

export function useCapabilities() {
  const [caps, setCaps] = useState(cache)
  useEffect(() => {
    subscribers.add(setCaps)
    // The remembered value paints immediately; the server confirms it ONCE per
    // page load, not once per component that asks.
    if (!confirmed) fetchCapabilities(true)
    return () => subscribers.delete(setCaps)
  }, [])
  return caps
}
