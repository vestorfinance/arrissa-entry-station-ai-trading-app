import { useState } from 'react'
import { Copy, Check, Play, ExternalLink } from 'lucide-react'

// Shared renderer for a documented, runnable API endpoint.
//
// Most endpoints take an api_key in the query. A few — the account-level ones a
// module may expose, like connecting a broker — take the session bearer token
// instead, and say so with `auth: "session"`. Putting a key on those would build
// a URL that cannot work and a Run button that always 401s.
export function buildUrl(base, ep, apiKey) {
  const q = new URLSearchParams()
  if (ep.auth !== 'session') q.set('api_key', apiKey || 'YOUR_API_KEY')
  ;(ep.params || []).forEach((p) => {
    if (p.example) q.set(p.name, p.example)
  })
  const qs = q.toString()
  return `${base}${ep.path}${qs ? '?' + decodeURIComponent(qs) : ''}`
}

export function ApiEndpoint({ ep, url }) {
  const [copied, setCopied] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const session = ep.auth === 'session'
  const curl = session
    ? `curl -H "Authorization: Bearer YOUR_TOKEN" "${url}"`
    : `curl "${url}"`

  function copy(text, which) {
    navigator.clipboard.writeText(text)
    setCopied(which)
    setTimeout(() => setCopied(''), 1500)
  }

  async function run() {
    setRunning(true)
    setResult(null)
    try {
      const token = session ? localStorage.getItem('auth_token') : null
      const res = await fetch(url, token ? { headers: { Authorization: `Bearer ${token}` } } : undefined)
      const text = await res.text()
      let body
      try {
        body = JSON.stringify(JSON.parse(text), null, 2)
      } catch {
        body = text
      }
      setResult({ status: res.status, body })
    } catch (err) {
      setResult({ status: 'ERR', body: String(err) })
    } finally {
      setRunning(false)
    }
  }

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

        <div className="field-label">Runnable URL</div>
        <div className="code-block">
          <code className="code-text">{url}</code>
          <div className="code-actions">
            <button className="btn btn--ghost btn--icon" title="Copy URL" onClick={() => copy(url, 'url')}>
              {copied === 'url' ? <Check size={16} /> : <Copy size={16} />}
            </button>
            <a className="btn btn--ghost btn--icon" title="Open in browser" href={url} target="_blank" rel="noreferrer">
              <ExternalLink size={16} />
            </a>
          </div>
        </div>

        <div className="field-label">curl</div>
        <div className="code-block">
          <code className="code-text">{curl}</code>
          <div className="code-actions">
            <button className="btn btn--ghost btn--icon" title="Copy curl" onClick={() => copy(curl, 'curl')}>
              {copied === 'curl' ? <Check size={16} /> : <Copy size={16} />}
            </button>
          </div>
        </div>

        <div className="endpoint-run">
          <button className="btn btn--primary" onClick={run} disabled={running}>
            <Play size={16} strokeWidth={2} />
            {running ? 'Running…' : 'Run'}
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
    </section>
  )
}
