import { useEffect, useState } from 'react'
import { Brain, Save } from 'lucide-react'
import DashboardLayout from '../components/DashboardLayout.jsx'
import * as api from '../services/api.js'

export default function Memory() {
  const [content, setContent] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [updatedAt, setUpdatedAt] = useState(null)
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.getMemory().then((r) => {
      setContent(r.content || '')
      setUpdatedAt(r.updated_at)
      setLoaded(true)
    }).catch(() => setLoaded(true))
  }, [])

  async function save() {
    setBusy(true); setMsg(null)
    try {
      await api.setMemory(content)
      setMsg({ type: 'ok', text: 'Memory saved' })
    } catch (err) {
      setMsg({ type: 'danger', text: err.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <DashboardLayout title="Memory">
      <div className="settings-stack">
        <section className="card">
          <div className="card-head">
            <Brain size={18} strokeWidth={1.75} />
            <div>
              <h2 className="card-title">User memory · MEMORY.md</h2>
              <p className="card-sub">
                Durable notes the agent keeps about you — preferences, risk appetite, account nicknames,
                goals. This is <strong>not</strong> your chat transcript; it persists across every
                conversation. The agent updates it as it learns, and you can edit it freely.
              </p>
            </div>
          </div>
          <div className="card-body">
            {msg && <div className={`alert alert--${msg.type === 'ok' ? 'ok' : 'danger'}`}>{msg.text}</div>}
            <textarea
              className="memory-editor"
              value={content}
              disabled={!loaded}
              placeholder={loaded ? '# What Arrissa knows about you\n\n- (nothing yet — start chatting)' : 'Loading…'}
              onChange={(e) => setContent(e.target.value)}
              spellCheck={false}
            />
            <div className="memory-foot">
              <span className="muted">
                {updatedAt ? `Last updated ${new Date(updatedAt).toLocaleString()}` : 'Markdown supported'}
              </span>
              <button className="btn btn--primary" onClick={save} disabled={busy || !loaded}>
                <Save size={16} strokeWidth={2} />
                {busy ? 'Saving…' : 'Save memory'}
              </button>
            </div>
          </div>
        </section>
      </div>
    </DashboardLayout>
  )
}
