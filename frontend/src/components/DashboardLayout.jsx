import { useState, useEffect } from 'react'
import Sidebar from './Sidebar.jsx'
import Topbar from './Topbar.jsx'
import LivePanel from './LivePanel.jsx'
import UpdateRibbon from './UpdateRibbon.jsx'

// App shell: fixed sidebar + topbar, scrollable main content area.
// On mobile the sidebar becomes an off-canvas drawer toggled from the topbar.
// `flush` removes the main padding/scroll so a child (e.g. the chat) can own the
// full height and manage its own scrolling.
// `hideLive` suppresses the global positions panel / briefcase FAB — used on the
// flow builder, which shows its own "Ask AI to build your agent" box instead.
const RAIL_KEY = 'es.sidebar.rail'

export default function DashboardLayout({ title, titleExtra = null, children, flush = false, hideLive = false }) {
  const [navOpen, setNavOpen] = useState(false)
  // Collapsed = an icon-only rail. Remembered, because it is a working
  // preference: having to re-collapse on every page load would make it a
  // gesture rather than a setting. Desktop-only in CSS — on a phone the sidebar
  // is already a drawer, so there is nothing to collapse.
  const [rail, setRail] = useState(() => {
    try { return localStorage.getItem(RAIL_KEY) === '1' } catch { return false }
  })
  useEffect(() => {
    try { localStorage.setItem(RAIL_KEY, rail ? '1' : '0') } catch { /* private mode */ }
  }, [rail])

  return (
    <div className={'app-shell' + (navOpen ? ' app-shell--nav-open' : '') + (rail ? ' app-shell--rail' : '')}>
      <Sidebar onNavigate={() => setNavOpen(false)} collapsed={rail} onToggleCollapse={() => setRail((r) => !r)} />
      {navOpen && <div className="nav-backdrop" onClick={() => setNavOpen(false)} />}
      <div className="app-body">
        {/* Above the topbar, so it is the first thing on the page rather than a
            notice competing with the page's own title. */}
        <UpdateRibbon />
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
  )
}
