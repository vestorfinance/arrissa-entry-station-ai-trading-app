// The single "active account" shared across the interface — the live panel's
// selected tab and the chat composer's account picker read/write this, so a change
// in one place shows up in the other instantly, and it's remembered across reloads
// (localStorage) and becomes the backend trading default too. Mirrors devmode.js.
import { useState, useEffect } from 'react'
import * as api from './api.js'

const KEY = 'arrissa.activeAccount'

export function getActiveAccount() {
  const v = localStorage.getItem(KEY)
  return v != null && v !== '' ? Number(v) : null
}

// Set the active account and notify every listener. No-op if unchanged (this is
// what stops the two-way sync from looping). `broker` (optional) also makes it the
// backend trading default. Pass persistBackend:false to only sync the UI.
export function setActiveAccount(num, { broker, persistBackend = true } = {}) {
  if (num == null) return
  const n = Number(num)
  if (getActiveAccount() === n) return
  localStorage.setItem(KEY, String(n))
  window.dispatchEvent(new CustomEvent('active-account-change', { detail: { account: n } }))
  if (persistBackend) {
    if (broker) api.setActiveAccountUnified(broker, n).catch(() => {})
    else api.setActiveAccount(n).catch(() => {})
  }
}

// React hook — re-renders when the active account changes (this tab or another).
export function useActiveAccount() {
  const [a, setA] = useState(getActiveAccount())
  useEffect(() => {
    const sync = () => setA(getActiveAccount())
    window.addEventListener('active-account-change', sync)
    window.addEventListener('storage', sync)     // cross-tab
    return () => {
      window.removeEventListener('active-account-change', sync)
      window.removeEventListener('storage', sync)
    }
  }, [])
  return a
}
