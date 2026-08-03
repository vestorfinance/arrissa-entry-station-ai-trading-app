// Theme control: System (default) / Light / Dark.
// The user's choice is stored in localStorage; "system" follows the OS.
// The resolved theme is stamped on <html data-theme="..."> so the CSS
// (which keys off [data-theme="light"]) can react. Dark is the base
// stylesheet, so we only ever need to add/remove the light attribute.

const KEY = 'arrissa.theme'
const mql = () => window.matchMedia('(prefers-color-scheme: dark)')

export function getThemePref() {
  const v = localStorage.getItem(KEY)
  return v === 'light' || v === 'dark' ? v : 'system'
}

function resolve(pref) {
  if (pref === 'light') return 'light'
  if (pref === 'dark') return 'dark'
  return mql().matches ? 'dark' : 'light'
}

export function applyTheme(pref = getThemePref()) {
  const effective = resolve(pref)
  document.documentElement.setAttribute('data-theme', effective)
  const meta = document.querySelector('meta[name="color-scheme"]')
  if (meta) meta.setAttribute('content', effective)
  // favicon follows the theme's sidebar emblem (white mark on dark, black on light)
  const icon = (effective === 'light' ? '/favicon-light.png' : '/favicon-dark.png') + '?v=3'
  document.querySelectorAll('link[rel="icon"], link[rel="apple-touch-icon"]')
    .forEach((l) => l.setAttribute('href', icon))
}

export function setThemePref(pref) {
  if (pref === 'system') localStorage.removeItem(KEY)
  else localStorage.setItem(KEY, pref)
  applyTheme(pref)
}

// Follow OS changes only while the user is on "system".
let bound = false
export function initTheme() {
  applyTheme()
  if (bound) return
  bound = true
  const onChange = () => { if (getThemePref() === 'system') applyTheme('system') }
  const m = mql()
  if (m.addEventListener) m.addEventListener('change', onChange)
  else m.addListener(onChange)
}
