import { useState } from 'react'
import { Copy, Check, Play, ExternalLink } from 'lucide-react'

// Shared renderer for a documented, runnable API endpoint.
//
// Most endpoints take an api_key in the query. A few — the account-level ones a
// module may expose, like connecting a broker — take the session bearer token
// instead, and say so with `auth: "session"`. Putting a key on those would build
// a URL that cannot work and a Run button that always 401s.
//
// An endpoint may also carry `examples`: extra, fully runnable URLs for the
// things a parameter table describes but cannot demonstrate. A point-in-time
// replay is the case this was added for — the parameters could be listed, but
// only actually running one shows what it does to the answer. They are kept
// SEPARATE from the primary URL on purpose: putting them in the main example
// would mean everyone who copied it got historical data by accident.
export function buildUrl(base, ep, apiKey, extra) {
  const q = new URLSearchParams()
  if (ep.auth !== 'session') q.set('api_key', apiKey || 'YOUR_API_KEY')
  ;(ep.params || []).forEach((p) => {
    if (p.example) q.set(p.name, p.example)
  })
  // An empty value REMOVES the parameter rather than sending it blank, so an
  // example can drop a filter it supersedes — `range=last-6-hours` alongside
  // the default `hours=6` would be two conflicting windows in one URL.
  Object.entries(extra || {}).forEach(([k, v]) => {
    if (v === null || v === undefined || v === '') q.delete(k)
    else q.set(k, v)
  })
  const qs = q.toString()
  return `${base}${ep.path}${qs ? '?' + decodeURIComponent(qs) : ''}`
}

export function ApiEndpoint({ ep, url, base, apiKey }) {
  const [copied, setCopied] = useState('')
  const [runningUrl, setRunningUrl] = useState('')
  const [results, setResults] = useState({})
  const session = ep.auth === 'session'

  const asCurl = (u) =>
    session ? `curl -H "Authorization: Bearer YOUR_TOKEN" "${u}"` : `curl "${u}"`

  function copy(text, which) {
    navigator.clipboard.writeText(text)
    setCopied(which)
    setTimeout(() => setCopied(''), 1500)
  }

  async function run(u) {
    setRunningUrl(u)
    setResults((r) => ({ ...r, [u]: null }))
    try {
      const token = session ? localStorage.getItem('auth_token') : null
      const res = await fetch(u, token ? { headers: { Authorization: `Bearer ${token}` } } : undefined)
      const text = await res.text()
      let body
      try {
        body = JSON.stringify(JSON.parse(text), null, 2)
      } catch {
        body = text
      }
      setResults((r) => ({ ...r, [u]: { status: res.status, body } }))
    } catch (err) {
      setResults((r) => ({ ...r, [u]: { status: 'ERR', body: String(err) } }))
    } finally {
      setRunningUrl('')
    }
  }

  // One runnable URL: copy, open, run, and its own response. Used for the
  // primary example and for every extra, so an extra is not a second-class
  // citizen you can read but not try.
  const Runnable = ({ u, id, label, hint }) => {
    const result = results[id]
    return (
      <div className="endpoint-example">
        <div className="field-label">{label}</div>
        {hint ? <p className="card-sub endpoint-example-hint">{hint}</p> : null}
        <div className="code-block">
          <code className="code-text">{u}</code>
          <div className="code-actions">
            <button className="btn btn--ghost btn--icon" title="Copy URL"
                    onClick={() => copy(u, `url:${id}`)}>
              {copied === `url:${id}` ? <Check size={16} /> : <Copy size={16} />}
            </button>
            <a className="btn btn--ghost btn--icon" title="Open in browser"
               href={u} target="_blank" rel="noreferrer">
              <ExternalLink size={16} />
            </a>
          </div>
        </div>
        <div className="code-block">
          <code className="code-text">{asCurl(u)}</code>
          <div className="code-actions">
            <button className="btn btn--ghost btn--icon" title="Copy curl"
                    onClick={() => copy(asCurl(u), `curl:${id}`)}>
              {copied === `curl:${id}` ? <Check size={16} /> : <Copy size={16} />}
            </button>
          </div>
        </div>
        <div className="endpoint-run">
          <button className="btn btn--primary" onClick={() => run(u)} disabled={!!runningUrl}>
            <Play size={16} strokeWidth={2} />
            {runningUrl === u ? 'Running…' : 'Run'}
          </button>
        </div>
        {result && (
          <div className="response">
            <div className="response-head">
              <span className={`pill ${result.status === 200 ? 'pill--ok' : 'pill--warn'}`}>
                {result.status === 200 ? '200 OK' : `Status ${result.status}`}
              </span>
            </div>
            <pre className="response-body">{result.body}</pre>
          </div>
        )}
      </div>
    )
  }

  // Extras need `base` to build their own URL. A guide that has not passed it
  // simply shows the primary example rather than rendering something broken.
  const extras = (base && ep.examples) ? ep.examples : []

  return (
    <section className="card endpoint">
      <div className="card-body">
        <div className="endpoint-head">
          <span className="method">GET</span>
          <code className="endpoint-path">{ep.path}</code>
        </div>
        <h3 className="endpoint-title">{ep.title}</h3>
        <p className="card-sub">{ep.desc}</p>

        <table className="params">
          <thead>
            <tr><th>Parameter</th><th>Example</th><th>Description</th></tr>
          </thead>
          <tbody>
            <tr>
              <td><code>api_key</code> <span className="req">required</span></td>
              <td><code>ak_live_…</code></td>
              <td>Your API key (auto-filled).</td>
            </tr>
            {ep.params.map((p) => (
              <tr key={p.name}>
                <td>
                  <code>{p.name}</code>{' '}
                  <span className={p.level === 'required' ? 'req' : 'opt'}>{p.level}</span>
                </td>
                <td>{p.example ? <code>{p.example}</code> : <span className="muted">—</span>}</td>
                <td>{p.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <Runnable u={url} id="main" label="Runnable URL" />

        {extras.map((ex, i) => (
          <Runnable
            key={ex.label || i}
            u={buildUrl(base, ep, apiKey, ex.params)}
            id={`ex${i}`}
            label={ex.label || 'Example'}
            hint={ex.hint}
          />
        ))}
      </div>
    </section>
  )
}
