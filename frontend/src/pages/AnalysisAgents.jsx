import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bot, Plus, Trash2, Workflow, AlertTriangle, Download, Upload, Globe, Lock } from 'lucide-react'
import DashboardLayout from '../components/DashboardLayout.jsx'
import CopyId from '../components/CopyId.jsx'
import AnalysisModelPicker from '../components/AnalysisModelPicker.jsx'
import * as store from '../services/agents.js'
import * as api from '../services/api.js'
import { backdrop } from '../services/backdrop.js'

const STATUS_TONE = { draft: 'pill--muted', active: 'pill--ok', paused: 'pill--warn' }

export default function AnalysisAgents() {
  const navigate = useNavigate()
  const [agents, setAgents] = useState([])
  const [admin, setAdmin] = useState(false)
  const [open, setOpen] = useState(false)
  const [confirm, setConfirm] = useState(null)   // agent pending delete confirmation
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [notice, setNotice] = useState(null)     // {type, text}
  const fileRef = useRef(null)

  const load = () => store.listAgents().then(setAgents).catch(() => setAgents([]))
  useEffect(() => { load() }, [])
  useEffect(() => { api.me().then((p) => setAdmin(!!p?.admin)).catch(() => {}) }, [])

  // admin only: publishing lets every user RUN it; editing stays with the owner
  async function togglePublic(e, a) {
    e.stopPropagation()
    try {
      await store.setPublic(a.id, !a.public)
      setNotice({ type: 'ok', text: a.public ? `“${a.name}” is private again.`
        : `“${a.name}” is now public — every user can run it.` })
      load()
    } catch (err) {
      setNotice({ type: 'danger', text: err.message })
    }
  }

  async function exportAgent(e, a) {
    e.stopPropagation()
    try {
      const payload = await store.exportAgent(a.id)
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${(a.name || 'agent').replace(/[^a-z0-9]+/gi, '-').toLowerCase()}.json`
      link.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setNotice({ type: 'danger', text: `Export failed: ${err.message}` })
    }
  }

  async function onImportFile(e) {
    const file = e.target.files?.[0]
    e.target.value = ''            // allow re-importing the same file
    if (!file) return
    try {
      const payload = JSON.parse(await file.text())
      const agent = await store.importAgent({
        name: payload.name, description: payload.description, flow: payload.flow,
      })
      setNotice({ type: 'ok', text: `Imported “${agent.name}”.` })
      navigate(`/analysis-agents/${agent.id}`)
    } catch (err) {
      setNotice({ type: 'danger', text: `Import failed: ${err.message}` })
    }
  }

  async function create(e) {
    e.preventDefault()
    if (!name.trim()) return
    try {
      const agent = await store.createAgent({ name, description })
      setOpen(false)
      setName(''); setDescription('')
      navigate(`/analysis-agents/${agent.id}`)
    } catch { /* surfaced by disabled state; keep modal open */ }
  }

  async function remove() {
    try { await store.deleteAgent(confirm.id) } catch { /* ignore */ }
    load()
    setConfirm(null)
  }

  return (
    <DashboardLayout title="Agents Builder">
      <div className="settings-stack settings-stack--wide">
        <section className="card">
          <div className="card-head">
            <Bot size={18} strokeWidth={1.75} />
            <div>
              <h2 className="card-title">Analysis agents</h2>
              <p className="card-sub">
                Agents that watch a market and reason about it on a schedule. Create one, then open it
                to build its flow on the canvas.
              </p>
            </div>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
              <input ref={fileRef} type="file" accept="application/json,.json"
                     style={{ display: 'none' }} onChange={onImportFile} />
              <button className="btn btn--ghost" onClick={() => fileRef.current?.click()}>
                <Upload size={16} strokeWidth={2} />
                Import
              </button>
              <button className="btn btn--primary" onClick={() => setOpen(true)}>
                <Plus size={16} strokeWidth={2} />
                New agent
              </button>
            </div>
          </div>

          <div className="card-body">
            {/* The same setting as Settings → Model for analysis agents. Here
                because this is where you are when it occurs to you to change it. */}
            <AnalysisModelPicker compact />
            {notice && (
              <div className={`alert alert--${notice.type === 'ok' ? 'ok' : 'danger'}`} style={{ marginBottom: 12 }}>
                {notice.text}
              </div>
            )}
            {agents.length === 0 ? (
              <div className="agents-empty">
                <Workflow size={26} strokeWidth={1.5} />
                <div className="agents-empty-title">No analysis agents yet</div>
                <p className="agents-empty-sub">Create your first agent to start building a flow.</p>
                <button className="btn btn--primary" onClick={() => setOpen(true)}>
                  <Plus size={16} strokeWidth={2} />
                  New agent
                </button>
              </div>
            ) : (
              <div className="agent-grid">
                {agents.map((a) => (
                  <button key={a.id} className="agent-card" onClick={() => navigate(`/analysis-agents/${a.id}`)}>
                    <div className="agent-card-top">
                      <span className="agent-avatar"><Bot size={18} strokeWidth={1.75} /></span>
                      <span className={`pill ${STATUS_TONE[a.status] || ''}`}>{a.status}</span>
                      <div className="agent-card-actions">
                        <span className="agent-del" title="Export as JSON"
                              onClick={(e) => exportAgent(e, a)}>
                          <Download size={15} strokeWidth={1.75} />
                        </span>
                        {a.mine !== false && (
                          <span className="agent-del" title="Delete"
                                onClick={(e) => { e.stopPropagation(); setConfirm(a) }}>
                            <Trash2 size={15} strokeWidth={1.75} />
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="agent-name">{a.name}</div>
                    <p className="agent-desc">{a.description || 'No description'}</p>
                    <div className="agent-meta">
                      <span className="pill pill--muted">{a.nodes ?? a.flow?.nodes?.length ?? 0} nodes</span>
                      {admin && a.mine !== false ? (
                        <span role="button" tabIndex={-1}
                              className={'pill pill--switch ' + (a.public ? 'pill--ok' : 'pill--muted')}
                              style={{ cursor: 'pointer' }}
                              title={a.public
                                ? 'Public — every user can run this agent. Click to make it private.'
                                : 'Private — only you can run it. Click to make it public.'}
                              onClick={(e) => togglePublic(e, a)}>
                          {a.public ? <Globe size={12} strokeWidth={2} /> : <Lock size={12} strokeWidth={2} />}
                          {a.public ? 'Public' : 'Private'}
                        </span>
                      ) : a.public ? (
                        <span className="pill pill--ok pill--switch"
                              title="Public — you can run it, only its owner edits it">
                          <Globe size={12} strokeWidth={2} /> Public
                        </span>
                      ) : null}
                      {/* the id the Analysis API takes as analysis_agent_id */}
                      <CopyId value={a.id} label="ID" title="Copy agent ID" />
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>

      {open && (
        <div className="modal-overlay" {...backdrop(() => setOpen(false))}>
          <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={create}>
            <div className="modal-head">
              <Bot size={18} strokeWidth={1.75} />
              <span className="modal-title">New analysis agent</span>
            </div>

            <div className="card-body" style={{ padding: 0, marginBottom: 20 }}>
              <label className="field">
                <span className="field-label">Name</span>
                <input className="input" value={name} autoFocus placeholder="Gold momentum watcher"
                       onChange={(e) => setName(e.target.value)} />
              </label>

              <label className="field">
                <span className="field-label">Description</span>
                <input className="input" value={description} placeholder="What should this agent look for?"
                       onChange={(e) => setDescription(e.target.value)} />
              </label>
            </div>

            <div className="modal-actions">
              <button type="button" className="btn btn--ghost" onClick={() => setOpen(false)}>Cancel</button>
              <button type="submit" className="btn btn--primary" disabled={!name.trim()}>Create agent</button>
            </div>
          </form>
        </div>
      )}

      {confirm && (
        <div className="modal-overlay" {...backdrop(() => setConfirm(null))}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <AlertTriangle className="modal-warn-icon" size={18} strokeWidth={1.75} />
              <span className="modal-title">Delete agent?</span>
            </div>
            <p className="modal-body">
              <strong>{confirm.name}</strong> and its flow will be permanently removed.
              This can’t be undone.
            </p>
            <div className="modal-actions">
              <button className="btn btn--ghost" onClick={() => setConfirm(null)}>Cancel</button>
              <button className="btn btn--danger" onClick={remove}>Delete agent</button>
            </div>
          </div>
        </div>
      )}
    </DashboardLayout>
  )
}
