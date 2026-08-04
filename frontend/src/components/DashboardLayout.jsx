import { useState, useEffect, useRef } from 'react'
import Sidebar from './Sidebar.jsx'
import Topbar from './Topbar.jsx'
import LivePanel from './LivePanel.jsx'
import UpdateRibbon from './UpdateRibbon.jsx'
import SetupModal from './SetupModal.jsx'

// App shell: fixed sidebar + topbar, scrollable main content area.
// On mobile the sidebar becomes an off-canvas drawer toggled from the topbar.
// `flush` removes the main padding/scroll so a child (e.g. the chat) can own the
// full height and manage its own scrolling.
// `hideLive` suppresses the global positions panel / briefcase FAB — used on the
// flow builder, which shows its own "Ask AI to build your agent" box instead.
const RAIL_KEY = 'es.sidebar.rail'

export default function DashboardLayout({ title, titleExtra = null, children, flush = false, hideLive = false, railHint = false }) {
  const [navOpen, setNavOpen] = useState(false)
  // Collapsed = an icon-only rail. Remembered, because it is a working
  // preference: having to re-collapse on every page load would make it a
  // gesture rather than a setting. Desktop-only in CSS — on a phone the sidebar
  // is already a drawer, so there is nothing to collapse.
  const [rail, setRail] = useState(() => {
    try { return localStorage.getItem(RAIL_KEY) === '1' } catch { return false }
  })
  // A page may collapse the rail for its own sake — the flow canvas wants every
  // pixel — and that must not become the user's setting. `auto` marks the
  // current value as the page's doing rather than theirs, so it is not saved
  // and is put back on the way out.
  const auto = useRef(false)
  useEffect(() => {
    if (auto.current) return
    try { localStorage.setItem(RAIL_KEY, rail ? '1' : '0') } catch { /* private mode */ }
  }, [rail])

  useEffect(() => {
    if (!railHint) return undefined
    let saved = false
    try { saved = localStorage.getItem(RAIL_KEY) === '1' } catch { saved = false }
    // Already working collapsed: nothing to collapse, and nothing to restore
    // later. Expanding them on the way out would be undoing a choice they made.
    if (saved) return undefined
    auto.current = true
    setRail(true)
    return () => {
      // Only if it is still the page's doing. Toggling it by hand while here
      // makes it theirs, and theirs survives leaving.
      if (auto.current) { auto.current = false; setRail(false) }
    }
  }, [railHint])

  // A deliberate toggle is a preference, whatever the page wanted.
  const toggleRail = () => { auto.current = false; setRail((r) => !r) }

  return (
    <div className="app-outer">
      {/* Outside the shell, not inside it. The topbar is absolutely positioned
          at top:0 with a transparent background and a blur, so anything placed
          before it as a sibling ends up UNDERNEATH it — which is what turned
          the whole header accent-blue. A column wrapper gives the ribbon real
          height that the sidebar and the body both start below. */}
      <UpdateRibbon />
      <SetupModal />
    <div className={'app-shell' + (navOpen ? ' app-shell--nav-open' : '') + (rail ? ' app-shell--rail' : '')}>
      <Sidebar onNavigate={() => setNavOpen(false)} collapsed={rail} onToggleCollapse={toggleRail} />
      {navOpen && <div className="nav-backdrop" onClick={() => setNavOpen(false)} />}
      <div className="app-body">
        <Topbar title={title} titleExtra={titleExtra} onMenu={() => setNavOpen((o) => !o)} />
        <main className={flush ? 'app-main app-main--flush' : 'app-main'}>{children}</main>
      </div>
      {!hideLive && <LivePanel />}
      {/* The Exness gate used to sit here, blocking the whole app until a
          broker was connected. A broker is a connection like any other now, made
          from Settings > Connections when the user wants one — an app that
          refuses to open until you hand over a broker password is a worse first
          impression than an app with nothing connected yet. */}
    </div>
    </div>
  )
}
