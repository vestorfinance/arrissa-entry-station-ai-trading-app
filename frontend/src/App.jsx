import { useState, useEffect } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import Login from './pages/Login.jsx'
import Signup from './pages/Signup.jsx'
import InviteOnly from './pages/InviteOnly.jsx'
import Buy from './pages/Buy.jsx'
import Home from './pages/Home.jsx'
import Legal, { Licence } from './pages/Legal.jsx'
import Install from './pages/Install.jsx'
import * as api from './services/api.js'
import { useAppConfig } from './services/appConfig.js'
import { useAuth } from './context/AuthContext.jsx'

// /signup shows the form only when the SERVER says registration is reachable —
// open, or the URL carries a valid private invite (/signup?invite=CODE).
// Otherwise the invite-only wall.
//
// The server is asked every time, with or without a code, because whether
// signup is reachable is a fact only it holds. A Community instance is
// single-user: the first account may be created, since somebody has to be able
// to get in, and from the moment that user exists registration is shut for
// good. This page used to decide with a hardcoded constant and only called the
// server when a code was present, which got BOTH ends of that wrong — a fresh
// Community install could not create its own first account without an invite
// code it had no way to know, and the page's idea of "open" could disagree with
// the server's for as long as the constant went unedited.
//
// The wall is cosmetic either way: /api/signup/start applies the same rule and
// refuses regardless of what the page decided to render.
function SignupGate() {
  const invite = new URLSearchParams(useLocation().search).get('invite') || ''
  const [state, setState] = useState('checking')
  useEffect(() => {
    api.checkInvite(invite)
      .then((r) => setState(r.valid ? 'allow' : 'block'))
      .catch(() => setState('block'))   // unreachable server ⇒ never open by accident
  }, [invite])
  if (state === 'checking') return null
  return state === 'allow' ? <Signup invite={invite} /> : <InviteOnly />
}
import Dashboard from './pages/Dashboard.jsx'
import Settings from './pages/Settings.jsx'
import Billing from './pages/Billing.jsx'
import AgentPrompt from './pages/AgentPrompt.jsx'
import Connections from './pages/Connections.jsx'
import { useCapabilities } from './services/capabilities.js'

// Who gets the front door.
//
// A member opening the app every morning wants their dashboard, not the pitch —
// so anyone holding a token goes straight through, exactly as `/` did before
// there was a homepage. And a Community instance has one user who already owns
// the software: marketing to them would be advertising a decision they have
// made, so their `/` stays the way in rather than a shop window.
//
// It waits for the answer instead of guessing, because guessing means the wrong
// page paints first and is then snatched away — which is what the capabilities
// cache exists to stop elsewhere in this file.
function HomeOrApp() {
  const { isAuthed } = useAuth()
  const cfg = useAppConfig()
  if (isAuthed) return <Navigate to="/dashboard" replace />
  if (cfg === null) return null
  // Nobody has an account on this box yet, so the front door is the sign-up.
  if (cfg.setup) return <Navigate to="/signup" replace />
  return cfg.edition === 'community' ? <Navigate to="/login" replace /> : <Home />
}

// Plans and credits exist on the hosted service. On a Community instance nobody
// is billed, so the page has nothing to say — and a link that lands on "this
// does not apply to you" is worse than no link. Existing bookmarks and the
// "Choose a plan" links go to Settings instead of a dead end.
function ConsoleOnly({ children }) {
  const caps = useCapabilities()
  if (caps && caps.admin === false) return <Navigate to="/settings" replace />
  return children
}

function BillingOnlyWhereItApplies() {
  const caps = useCapabilities()
  if (caps && caps.billing === false) return <Navigate to="/settings" replace />
  return <Billing />
}
import Accounts from './pages/Accounts.jsx'
import RiskSettings from './pages/RiskSettings.jsx'
import HmrNotifier from './components/HmrNotifier.jsx'
import Memory from './pages/Memory.jsx'
import OrdersGuide from './pages/OrdersGuide.jsx'
import OrderManagementGuide from './pages/OrderManagementGuide.jsx'
import AccountGuide from './pages/AccountGuide.jsx'
import InstrumentsGuide from './pages/InstrumentsGuide.jsx'
import ScheduledGuide from './pages/ScheduledGuide.jsx'
import ScheduledActionsGuide from './pages/ScheduledActionsGuide.jsx'
import ScheduledActions from './pages/ScheduledActions.jsx'
import AnalysisApiGuide from './pages/AnalysisApiGuide.jsx'
import ArtificialSentimentGuide from './pages/ArtificialSentimentGuide.jsx'
import Modules from './pages/Modules.jsx'
import ModuleGuide from './pages/ModuleGuide.jsx'
import MarketDataGuide from './pages/MarketDataGuide.jsx'
import SltpCalculatorGuide from './pages/SltpCalculatorGuide.jsx'
import MobileSimulator from './pages/MobileSimulator.jsx'
import AnalysisAgents from './pages/AnalysisAgents.jsx'
import AnalysisAgentFlow from './pages/AnalysisAgentFlow.jsx'
import AdminOverview from './pages/admin/AdminOverview.jsx'
import AdminUsers from './pages/admin/AdminUsers.jsx'
import AdminSettings from './pages/admin/AdminSettings.jsx'
import AdminAudit from './pages/admin/AdminAudit.jsx'
import AdminTransactions from './pages/admin/AdminTransactions.jsx'
import ProtectedRoute from './components/ProtectedRoute.jsx'

// Real client-side routes. Add new protected pages here as the app grows.
export default function App() {
  return (
    <>
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<SignupGate />} />
      {/* Public, and reachable without an account: somebody deciding whether to
          create one has to be able to read what they would be agreeing to. */}
      <Route path="/terms" element={<Legal />} />
      <Route path="/privacy" element={<Legal />} />
      <Route path="/licence" element={<Licence />} />
      <Route path="/license" element={<Navigate to="/licence" replace />} />
      {/* Public: the buyer has no account here and is not being asked to make one. */}
      {/* The Community install guide: public, like everything else somebody
          reads before they have an account. */}
      <Route path="/install" element={<Install />} />
      <Route path="/modules/:id" element={<Buy />} />
      <Route path="/buy/:id" element={<Buy />} />
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute>
            <Dashboard />
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/:chatId"
        element={
          <ProtectedRoute>
            <Dashboard />
          </ProtectedRoute>
        }
      />
      <Route
        path="/orders-guide"
        element={
          <ProtectedRoute>
            <OrdersGuide />
          </ProtectedRoute>
        }
      />
      <Route
        path="/instruments-guide"
        element={
          <ProtectedRoute>
            <InstrumentsGuide />
          </ProtectedRoute>
        }
      />
      <Route
        path="/scheduled-guide"
        element={
          <ProtectedRoute>
            <ScheduledGuide />
          </ProtectedRoute>
        }
      />
      <Route
        path="/scheduled-actions-guide"
        element={
          <ProtectedRoute>
            <ScheduledActionsGuide />
          </ProtectedRoute>
        }
      />
      <Route
        path="/scheduled-actions"
        element={
          <ProtectedRoute>
            <ScheduledActions />
          </ProtectedRoute>
        }
      />
      <Route
        path="/analysis-api-guide"
        element={
          <ProtectedRoute>
            <AnalysisApiGuide />
          </ProtectedRoute>
        }
      />
      <Route
        path="/artificial-sentiment-guide"
        element={
          <ProtectedRoute>
            <ArtificialSentimentGuide />
          </ProtectedRoute>
        }
      />
      <Route
        path="/market-data-guide"
        element={
          <ProtectedRoute>
            <MarketDataGuide />
          </ProtectedRoute>
        }
      />
      <Route
        path="/sltp-calculator"
        element={
          <ProtectedRoute>
            <SltpCalculatorGuide />
          </ProtectedRoute>
        }
      />
      <Route
        path="/analysis-agents"
        element={
          <ProtectedRoute>
            <AnalysisAgents />
          </ProtectedRoute>
        }
      />
      <Route
        path="/analysis-agents/:id"
        element={
          <ProtectedRoute>
            <AnalysisAgentFlow />
          </ProtectedRoute>
        }
      />
      <Route
        path="/order-management-guide"
        element={
          <ProtectedRoute>
            <OrderManagementGuide />
          </ProtectedRoute>
        }
      />
      <Route
        path="/account-guide"
        element={
          <ProtectedRoute>
            <AccountGuide />
          </ProtectedRoute>
        }
      />
      <Route
        path="/accounts"
        element={
          <ProtectedRoute>
            <Accounts />
          </ProtectedRoute>
        }
      />
      <Route
        path="/risk-settings"
        element={
          <ProtectedRoute>
            <RiskSettings />
          </ProtectedRoute>
        }
      />
      <Route
        path="/settings"
        element={
          <ProtectedRoute>
            <Settings />
          </ProtectedRoute>
        }
      />
      <Route
        path="/billing"
        element={
          <ProtectedRoute>
            <BillingOnlyWhereItApplies />
          </ProtectedRoute>
        }
      />
      <Route
        path="/memory"
        element={
          <ProtectedRoute>
            <Memory />
          </ProtectedRoute>
        }
      />
      {/* These guides became modules. The paths stay so old links, bookmarks and
          anything the assistant once linked to still land somewhere real. */}
      <Route path="/analysis-guide" element={<Navigate to="/module/truth-social" replace />} />
      <Route path="/fedwatch-guide" element={<Navigate to="/module/fedwatch" replace />} />
      <Route path="/sentiment-guide" element={<Navigate to="/module/sentiment" replace />} />
      <Route path="/bonds-guide" element={<Navigate to="/module/bond-yields" replace />} />
      <Route path="/news-guide" element={<Navigate to="/module/news" replace />} />
      <Route path="/hmr-guide" element={<Navigate to="/module/hmr" replace />} />
      <Route path="/connections" element={<ProtectedRoute><Connections /></ProtectedRoute>} />
      <Route path="/agent-prompt" element={<ProtectedRoute><AgentPrompt /></ProtectedRoute>} />
      <Route path="/modules" element={<ProtectedRoute><Modules /></ProtectedRoute>} />
      {/* Every page an installed module contributes, behind ONE static route.
          Router needs its routes at build time, so the module's identity travels
          in the URL and the page itself is fetched at runtime — which is how a
          ZIP adds a page to a bundle that was built without it. */}
      <Route path="/module/:id" element={<ProtectedRoute><ModuleGuide /></ProtectedRoute>} />
      {/* The console manages other people. Where there is nobody else it does
          not exist server-side either — every /api/admin route 404s — so a page
          reached by URL would only mount to fire failing requests at it. */}
      <Route path="/admin" element={<ProtectedRoute><ConsoleOnly><AdminOverview /></ConsoleOnly></ProtectedRoute>} />
      <Route path="/admin/users" element={<ProtectedRoute><ConsoleOnly><AdminUsers /></ConsoleOnly></ProtectedRoute>} />
      <Route path="/admin/settings" element={<ProtectedRoute><ConsoleOnly><AdminSettings /></ConsoleOnly></ProtectedRoute>} />
      <Route path="/admin/transactions" element={<ProtectedRoute><ConsoleOnly><AdminTransactions /></ConsoleOnly></ProtectedRoute>} />
      <Route path="/admin/audit" element={<ProtectedRoute><ConsoleOnly><AdminAudit /></ConsoleOnly></ProtectedRoute>} />
      {/* Dev tool: renders the app inside a phone-sized frame on any screen.
          Deliberately unprotected so /login and /signup can be previewed too —
          the page inside the frame still enforces its own auth. */}
      <Route path="/mobile-simulator" element={<MobileSimulator />} />
      {/* The front door, and only for people standing outside it. A member who
          opens the app every morning wants their dashboard, not the pitch; and a
          Community box has one user who already owns it, so selling to them
          there would be advertising a decision they have made. Both go straight
          through, exactly as before. */}
      <Route path="/" element={<HomeOrApp />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
    <HmrNotifier />
    </>
  )
}
