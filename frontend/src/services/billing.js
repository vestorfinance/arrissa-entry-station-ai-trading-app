// Billing state (current plan + credit balance) with a tiny shared cache and a
// 'billing-change' event, mirroring the devmode service. Call billingChanged()
// after any subscribe / top-up / cancel so the credit meter and gates everywhere
// re-fetch. useBilling() is the React hook.
import { useState, useEffect } from 'react'
import * as api from './api.js'
import { cachedCapabilities, capabilitiesChanged } from './capabilities.js'

let _cache = null

// A deployment that bills nobody has no /api/billing at all — the routes 404 by
// design. Asking anyway is a 404 in the console on every page load, for an
// answer we already hold. This is what an unmetered instance would say if it
// had a billing endpoint: nothing is locked, because nothing is sold.
const UNMETERED = { active: true, plan: null, plan_name: null, credits: null,
                    developer: true, unmetered: true }

const billsAnyone = () => cachedCapabilities()?.billing !== false

export function cachedBilling() {
  return _cache
}

export async function fetchBilling(force = false) {
  if (_cache && !force) return _cache
  if (!billsAnyone()) { _cache = UNMETERED; return _cache }
  _cache = await api.getBilling()
  return _cache
}

// Notify every meter/gate to re-fetch (this tab). Subscribing or cancelling
// also changes what the user MAY DO, so the capability cache goes with it.
export function billingChanged() {
  _cache = null
  capabilitiesChanged()          // subscribing changes what the user MAY DO too
  window.dispatchEvent(new Event('billing-change'))
}

export function useBilling() {
  const [b, setB] = useState(_cache)
  useEffect(() => {
    let alive = true
    const load = () =>
      api.getBilling().then((x) => { _cache = x; if (alive) setB(x) }).catch(() => {})
    load()
    window.addEventListener('billing-change', load)
    return () => { alive = false; window.removeEventListener('billing-change', load) }
  }, [])
  return b
}
