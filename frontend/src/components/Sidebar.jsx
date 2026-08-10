import { useState, useEffect, useRef } from 'react'
import InstrumentFlag from './InstrumentFlag.jsx'
import { detectSymbol } from '../data/flags.js'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import {
  Settings, BookOpen, Boxes, CalendarClock, SlidersHorizontal, Wallet,
  ChevronRight, MoreHorizontal, Plus, Brain, Trash2, Timer, Bot, Megaphone, Landmark, Gauge,
  Newspaper, CandlestickChart, Calculator, LogOut, Percent, ShieldCheck, ShieldAlert, CreditCard, Shield,
  Radar, Puzzle, Plug,
  PanelLeft, PanelLeftClose,
} from 'lucide-react'
import { useChats } from '../context/ChatsContext.jsx'
import { useAuth } from '../context/AuthContext.jsx'
import { useAppName } from '../services/appConfig.js'
import * as api from '../services/api.js'
import * as moduleBus from '../services/moduleBus.js'
import { cachedCapabilities, forgetCapabilities } from '../services/capabilities.js'

// Guides nested under the "Trade API Guide" parent.
const TRADE_GUIDES = [
  { label: 'Orders', to: '/orders-guide', Icon: BookOpen },
  { label: 'Order Management', to: '/order-management-guide', Icon: SlidersHorizontal },
  { label: 'Account Info', to: '/account-guide', Icon: Wallet },
  { label: 'Instruments', to: '/instruments-guide', Icon: Boxes },
  { label: 'Scheduled Orders', to: '/scheduled-guide', Icon: CalendarClock },
  { label: 'Scheduled Actions', to: '/scheduled-actions-guide', Icon: Timer },
]

// Guides nested under the "Analysis API Guide" parent.
//
// CORE guides only. Everything a module provides — bond yields, sentiment,
// news, fedwatch, truth social, hmr, the calendar — arrives from
// /api/modules/guides and is merged into these same groups by `nav.group`. It
// used to be listed here too, which meant an uninstalled module still offered
// its page, and an installed one offered it twice.
const ANALYSIS_GUIDES = [
  { label: 'Analysis Agents API', to: '/analysis-api-guide', Icon: Bot },
  { label: 'Artificial Sentiment', to: '/artificial-sentiment-guide', Icon: Radar },
  { label: 'Market Data', to: '/market-data-guide', Icon: CandlestickChart },
  { label: 'SL/TP Calculator', to: '/sltp-calculator', Icon: Calculator },
]

// Icons a module may name in its guide.json. A module cannot ship a React
// component, so it names one of these; anything unrecognised falls back to the
// generic module mark rather than rendering nothing.
const MODULE_ICONS = {
  CalendarClock, Landmark, Gauge, Newspaper, CandlestickChart, Percent,
  Megaphone, ShieldAlert, Calculator, Bot, Timer, Brain, Radar, Puzzle,
}

export default function Sidebar({ onNavigate, collapsed = false, onToggleCollapse }) {
  const location = useLocation()
  const navigate = useNavigate()
  const go = () => onNavigate?.()    // close the mobile drawer on navigation
  const { chats, activeId, setActiveId, refresh, newChat } = useChats()
  const { user, logout } = useAuth()
  const appName = useAppName()
  const [profile, setProfile] = useState(null)
  useEffect(() => { api.me().then(setProfile).catch(() => {}) }, [])
  // What this user may reach, decided by the SERVER. Working it out here from
  // the billing state was a cloud assumption: on a Community instance nobody
  // has a plan, and the menu came up empty on software the user installed
  // themselves.
  // Always an object — never null. A nullable `caps` needs `?.` at every single
  // use, and the one place that missed it took the whole sidebar down.
  const caps = profile?.capabilities || cachedCapabilities() || {}
  const isElite = !!caps.guides
  const canModules = !!caps.modules
  // Asked once when the sidebar mounts, and again on the hour — module releases
  // are not a thing that happens every few seconds.
  const [updates, setUpdates] = useState(0)
  const [updatesBlocked, setUpdatesBlocked] = useState(0)
  useEffect(() => {
    if (!canModules) return
    let live = true
    const ask = () => api.moduleUpdates()
      .then((r) => { if (live) { setUpdates(r.count || 0); setUpdatesBlocked(r.blocked || 0) } })
      .catch(() => {})
    ask()
    const t = setInterval(ask, 60 * 60 * 1000)
    return () => { live = false; clearInterval(t) }
  }, [canModules])
  // Unknown means HIDDEN, not shown. A menu entry that appears a moment late is
  // survivable; one that appears and is taken away looks broken, and on a
  // Community instance Plans & Billing was doing exactly that on every refresh.
  const showBilling = caps.billing === true
  const first = (profile?.first_name || '').trim()
  const last = (profile?.last_name || '').trim()
  const displayName = [first, last].filter(Boolean).join(' ')
    || (profile?.email || user?.email || '').split('@')[0]
  const initials = ((first[0] || '') + (last[0] || '')).toUpperCase()
    || (displayName[0] || '?').toUpperCase()
  function onLogout() {
    forgetCapabilities()          // the next person may not be the same person
    logout(); navigate('/login', { replace: true })
  }

  // "More…" flyout: holds the guides + secondary pages so the sidebar stays lean.
  const [moreOpen, setMoreOpen] = useState(false)
  const moreRef = useRef(null)
  const moreActive = [...TRADE_GUIDES, ...ANALYSIS_GUIDES].some((g) => location.pathname === g.to)
    || ['/scheduled-actions', '/analysis-agents', '/memory'].includes(location.pathname)
  useEffect(() => {
    if (!moreOpen) return
    const onDoc = (e) => { if (moreRef.current && !moreRef.current.contains(e.target)) setMoreOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [moreOpen])
  const goClose = () => { setMoreOpen(false); go() }   // navigate + close popup (and drawer)

  // Collapsed, the label is gone, so the tooltip is the only thing left that
  // says what a row is. Expanded, the word is right there and a tooltip
  // repeating it is just noise.
  const tip = (label) => (collapsed ? label : undefined)

  // Pages installed modules contribute. Fetched rather than compiled in, so a
  // module installed from a ZIP appears here without the bundle knowing it exists.
  const [moduleGuides, setModuleGuides] = useState([])
  useEffect(() => {
    const load = () => api.moduleGuides()
      .then((r) => setModuleGuides(r.guides || []))
      .catch(() => setModuleGuides([]))
    load()
    // A module switched off elsewhere in the app must not leave its page listed
    // here until the next reload — the link would open a guide for something
    // that has gone.
    return moduleBus.onChanged(load)
  }, [])

  // A module's guide joins the group it belongs in — bond yields sits with the
  // other analysis guides, not in a bucket labelled "Modules". `nav.order` gives
  // an author a say in where; ties fall back to the label.
  const guidesIn = (group) => moduleGuides
    .filter((g) => (g.nav?.group || 'modules') === group)
    .sort((a, b) => (a.nav?.order ?? 100) - (b.nav?.order ?? 100) ||
                    String(a.nav?.label || a.id).localeCompare(String(b.nav?.label || b.id)))
    .map((g) => ({ label: g.nav?.label || g.title || g.id,
                   to: `/module/${g.id}`,
                   Icon: MODULE_ICONS[g.nav?.icon] || Puzzle }))

  const GUIDE_GROUPS = [
    { key: 'trade',    label: 'Trade API Guide',    items: TRADE_GUIDES },
    { key: 'analysis', label: 'Analysis API Guide', items: ANALYSIS_GUIDES },
    { key: 'modules',  label: 'Modules',            items: [] },
  ]

  function openChat(id) {
    setActiveId(id)
    navigate('/dashboard/' + id)   // permanent per-chat link (survives refresh)
    go()
  }

  function startNewChat() {
    newChat()
    navigate('/dashboard')
    go()
  }

  async function removeChat(e, id) {
    e.stopPropagation()
    try {
      await api.deleteChat(id)
      if (id === activeId) { newChat(); navigate('/dashboard') }   // don't leave a dead link in the URL
      refresh()
    } catch { /* ignore */ }
  }

  return (
    <aside className="sidebar">
      <div className="brand">
        {/* The mark is the way home. Every app puts it there, so a user who is
            lost clicks it before they look for a menu item. */}
        <Link className="brand-home" to="/dashboard" onClick={go} title={appName}>
          <img src="/entry-station-mark.png" alt={appName} width={31} height={31} className="brand-mark" />
          <span className="brand-name">{appName}</span>
        </Link>
        {/* Collapse to an icon-only rail. Desktop only — on a phone the sidebar
            is already a drawer that closes, so a second way to hide it would
            just be a button that does nothing you can see. */}
        <button
          type="button"
          className="sidebar-toggle"
          onClick={onToggleCollapse}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-expanded={!collapsed}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <PanelLeft size={20} strokeWidth={1.75} /> : <PanelLeftClose size={20} strokeWidth={1.75} />}
        </button>
      </div>

      <button type="button" className="new-chat" onClick={startNewChat} title={tip('New chat')}>
        <Plus size={18} strokeWidth={2} />
        <span>New chat</span>
      </button>

      <div className="chat-history">
        <div className="history-label">Recents</div>
        {chats.length === 0 ? (
          <p className="history-empty">No conversations yet</p>
        ) : (
          chats.map((c) => (
            <button
              key={c.id}
              className={'history-item' + (c.id === activeId ? ' history-item--active' : '')}
              onClick={() => openChat(c.id)}
              title={c.title}
            >
              <InstrumentFlag symbol={detectSymbol(c.title)} size="sm" />
              <span className="history-title">{c.title}</span>
              <span className="history-del" onClick={(e) => removeChat(e, c.id)} title="Delete">
                <Trash2 size={14} strokeWidth={1.75} />
              </span>
            </button>
          ))
        )}
      </div>

      <nav className="nav nav--foot">
        <div className="more-wrap" ref={moreRef}>
          <button
            type="button"
            className={'nav-item nav-more-btn' + (moreActive ? ' nav-item--active' : '') + (moreOpen ? ' nav-more-btn--open' : '')}
            onClick={() => setMoreOpen((o) => !o)}
            title={tip('More…')}
          >
            <MoreHorizontal className="nav-icon" size={18} strokeWidth={1.75} />
            <span>More…</span>
            <ChevronRight className={'nav-chevron-r' + (moreOpen ? ' open' : '')} size={16} strokeWidth={2} />
          </button>

          {moreOpen && (
            <div className="more-popup">
              {/* API guides are the programmatic/developer surface → Developer mode only.
                  Scheduled Actions, Analysis Agents and Memory are core features → all plans. */}
              {isElite && (<>
              {GUIDE_GROUPS.map(({ key, label, items }) => {
                const entries = items.concat(guidesIn(key))
                if (!entries.length) return null
                return (
                  <div key={key}>
                    <div className="more-group-label">{label}</div>
                    {entries.map(({ label: l, to, Icon }) => (
                      <NavLink key={to} to={to} onClick={goClose}
                        className={({ isActive }) => 'nav-item nav-child' + (isActive ? ' nav-item--active' : '')}>
                        <Icon className="nav-icon" size={16} strokeWidth={1.75} />
                        <span>{l}</span>
                      </NavLink>
                    ))}
                  </div>
                )
              })}

              <div className="more-sep" />
              </>)}
              <NavLink to="/scheduled-actions" onClick={goClose}
                className={({ isActive }) => 'nav-item' + (isActive ? ' nav-item--active' : '')}>
                <Timer className="nav-icon" size={18} strokeWidth={1.75} />
                <span>Scheduled Actions</span>
              </NavLink>
              <NavLink to="/analysis-agents" onClick={goClose}
                className={({ isActive }) => 'nav-item' + (isActive ? ' nav-item--active' : '')}>
                <Bot className="nav-icon" size={18} strokeWidth={1.75} />
                <span>Agents Builder</span>
              </NavLink>
              {canModules && (
                <NavLink to="/modules" onClick={goClose}
                  className={({ isActive }) => 'nav-item' + (isActive ? ' nav-item--active' : '')}>
                  <Puzzle className="nav-icon" size={18} strokeWidth={1.75} />
                  <span>Module Store</span>
                  {/* Nobody visits a store to check whether it has news for them.
                      The count is dulled when every update is out of reach, so a
                      lapsed subscription nags once rather than forever. */}
                  {updates > 0 && (
                    <span className={'nav-badge' + (updatesBlocked >= updates ? ' nav-badge--muted' : '')}
                          title={updatesBlocked >= updates
                            ? `${updates} update(s) available — needs a live subscription`
                            : `${updates} update(s) available`}>
                      {updates}
                    </span>
                  )}
                </NavLink>
              )}
              <NavLink to="/agent-prompt" onClick={goClose}
                className={({ isActive }) => 'nav-item' + (isActive ? ' nav-item--active' : '')}>
                <Bot className="nav-icon" size={18} strokeWidth={1.75} />
                <span>Chat Agent Prompt</span>
              </NavLink>
              <NavLink to="/memory" onClick={goClose}
                className={({ isActive }) => 'nav-item' + (isActive ? ' nav-item--active' : '')}>
                <Brain className="nav-icon" size={18} strokeWidth={1.75} />
                <span>Memory</span>
              </NavLink>
            </div>
          )}
        </div>

        <NavLink to="/accounts" onClick={go} title={tip('Accounts')}
          className={({ isActive }) => 'nav-item' + (isActive ? ' nav-item--active' : '')}>
          <Wallet className="nav-icon" size={18} strokeWidth={1.75} />
          <span>Accounts</span>
        </NavLink>

        <NavLink to="/connections" onClick={go} title={tip('Connections')}
          className={({ isActive }) => 'nav-item' + (isActive ? ' nav-item--active' : '')}>
          <Plug className="nav-icon" size={18} strokeWidth={1.75} />
          <span>Connections</span>
        </NavLink>

        <NavLink to="/risk-settings" onClick={go} title={tip('Risk Settings')}
          className={({ isActive }) => 'nav-item' + (isActive ? ' nav-item--active' : '')}>
          <ShieldCheck className="nav-icon" size={18} strokeWidth={1.75} />
          <span>Risk Settings</span>
        </NavLink>

        {/* Nobody is billed on a Community instance — there is no plan to buy
            and no credits to spend, so the page would only ever say so. */}
        {showBilling && (
          <NavLink to="/billing" onClick={go} title={tip('Plans & Billing')}
            className={({ isActive }) => 'nav-item' + (isActive ? ' nav-item--active' : '')}>
            <CreditCard className="nav-icon" size={18} strokeWidth={1.75} />
            <span>Plans &amp; Billing</span>
          </NavLink>
        )}

        {/* The admin console manages OTHER PEOPLE. A single-user instance has
            nobody else on it, so there is nothing to administer — the settings
            from it that still apply live in Settings instead. */}
        {caps.admin && (
          <NavLink to="/admin" onClick={go} title={tip('Admin')}
            className={({ isActive }) => 'nav-item' + (isActive ? ' nav-item--active' : '')}>
            <Shield className="nav-icon" size={18} strokeWidth={1.75} />
            <span>Admin</span>
          </NavLink>
        )}

        <NavLink to="/settings" onClick={go} title={tip('Settings')}
          className={({ isActive }) => 'nav-item' + (isActive ? ' nav-item--active' : '')}>
          <Settings className="nav-icon" size={18} strokeWidth={1.75} />
          <span>Settings</span>
        </NavLink>
      </nav>

      {/* Account + logout — shown only in the mobile drawer (hidden on desktop,
          where the topbar carries them). */}
      <div className="sidebar-user">
        <span className="sidebar-user-av" aria-hidden="true">{initials}</span>
        <span className="sidebar-user-name">{displayName}</span>
        <button type="button" className="sidebar-user-logout" onClick={onLogout} title="Logout">
          <LogOut size={17} strokeWidth={1.75} />
        </button>
      </div>
    </aside>
  )
}
