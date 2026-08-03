// Developer mode: when ON, the chat shows raw JSON in tool activity; when OFF
// (the default for everyone) tool results are rendered as formatted, readable
// data — charts for market data, tables for lists, labeled rows for objects.
import { useState, useEffect } from 'react'

const KEY = 'arrissa.devmode'

export function getDevMode() {
  return localStorage.getItem(KEY) === '1'
}

export function setDevMode(on) {
  localStorage.setItem(KEY, on ? '1' : '0')
  window.dispatchEvent(new Event('devmode-change'))   // notify listeners in this tab
}

// React hook — re-renders when the toggle flips (this tab or another).
export function useDevMode() {
  const [on, setOn] = useState(getDevMode())
  useEffect(() => {
    const sync = () => setOn(getDevMode())
    window.addEventListener('devmode-change', sync)
    window.addEventListener('storage', sync)
    return () => {
      window.removeEventListener('devmode-change', sync)
      window.removeEventListener('storage', sync)
    }
  }, [])
  return on
}
