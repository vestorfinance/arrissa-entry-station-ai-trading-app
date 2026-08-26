import { useEffect, useState } from 'react'
import { KeyRound, Puzzle } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import DashboardLayout from '../components/DashboardLayout.jsx'
import { ApiEndpoint, buildUrl } from '../components/ApiEndpoint.jsx'
import GuideSection, { GuideAction } from '../components/GuideSection.jsx'
import * as api from '../services/api.js'
import * as moduleBus from '../services/moduleBus.js'

// One component, every module's guide page.
//
// This is what lets a ZIP add a page to a bundle that was built without it: the
// page is DATA — a guide.json inside the module — and this renders it. 16 of the
// 17 hand-written guides were already the same shape (an intro, a list of
// endpoints, their parameters), so almost nothing is lost by describing them
// instead of coding them, and a self-hoster needs no build step to gain a page.
//
// A module that genuinely needs bespoke UI ships its own component instead; this
// covers the ordinary case, which is nearly all of them.
export default function ModuleGuide({ guide: given }) {
  const { id } = useParams()
  const [guide, setGuide] = useState(given || null)
  const [apiKey, setApiKey] = useState(null)
  const [loaded, setLoaded] = useState(!!given)
  const base = window.location.origin

  useEffect(() => {
    api.primaryKey().then((r) => setApiKey(r.api_key)).catch(() => setApiKey(null))
  }, [])

  // Fetched by id, so the page survives a deep link and a refresh rather than
  // working only when the sidebar happened to pass it in.
  useEffect(() => {
    if (given) { setGuide(given); setLoaded(true); return }
    const load = () => api.moduleGuides()
      .then((r) => setGuide((r.guides || []).find((g) => g.id === id) || null))
      .catch(() => setGuide(null))
      .finally(() => setLoaded(true))
    load()
    // Sitting on a guide when its module is switched off should say so, not keep
    // offering Run buttons against endpoints that now 404.
    return moduleBus.onChanged(load)
  }, [given, id])

  if (!loaded) return <DashboardLayout title="Loading…"><div className="guide" /></DashboardLayout>

  if (!guide) {
    return (
      <DashboardLayout title="Not found">
        <div className="guide">
          <div className="card"><div className="card-body">
            <h2 className="card-title">This page belongs to a module that is not installed</h2>
            <p className="card-sub">
              It was provided by a module that has since been removed or disabled.{' '}
              <Link to="/modules" style={{ textDecoration: 'underline' }}>Manage modules</Link>.
            </p>
          </div></div>
        </div>
      </DashboardLayout>
    )
  }

  const intro = Array.isArray(guide.intro) ? guide.intro : [guide.intro].filter(Boolean)

  return (
    <DashboardLayout title={guide.title || guide.heading || 'Module'}>
      <div className="guide">
        <div className="guide-intro card">
          <div className="card-body">
            <h2 className="card-title">
              <Puzzle size={16} strokeWidth={1.9} style={{ verticalAlign: '-2px', marginRight: 8 }} />
              {guide.heading || guide.title}
            </h2>
            {intro.map((p, i) => <p className="card-sub" key={i}>{p}</p>)}

            {!!(guide.actions || []).length && (
              <div className="guide-actions">
                {guide.actions.map((a) => <GuideAction key={a.label} action={a} />)}
              </div>
            )}

            {loaded && !apiKey && (
              <div className="alert alert--danger" style={{ marginTop: 12 }}>
                No active API key.{' '}
                <Link to="/settings" style={{ textDecoration: 'underline' }}>Generate one in Settings</Link>.
              </div>
            )}
            {apiKey && (
              <div className="key-inline">
                <KeyRound size={15} strokeWidth={1.75} />
                <span>Using your active key</span>
                <code className="key-inline-val">{`${apiKey.slice(0, 12)}…${apiKey.slice(-4)}`}</code>
              </div>
            )}
          </div>
        </div>

        {/* Live sections come first, for the same reason the hand-written pages
            put them there: someone opening a guide wants to see the thing work
            before they read about how to call it. */}
        {(guide.sections || []).map((s, i) => (
          <GuideSection key={s.id || s.title || i} section={s} apiKey={apiKey} base={base} />
        ))}

        {(guide.endpoints || []).map((ep) => (
          <ApiEndpoint key={ep.id || ep.path} ep={ep} url={buildUrl(base, ep, apiKey)} base={base} apiKey={apiKey} />
        ))}

        {!!(guide.notes || []).length && (
          <div className="card"><div className="card-body">
            <h3 className="card-title">Worth knowing</h3>
            <ul className="guide-list">
              {guide.notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          </div></div>
        )}
      </div>
    </DashboardLayout>
  )
}
