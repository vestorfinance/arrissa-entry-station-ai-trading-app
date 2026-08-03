import { useEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'
import * as api from '../services/api.js'

// Which model analysis agents run on.
//
// One component, two homes: Settings, where you go to configure things, and the
// Agents Builder, where you are when the question actually occurs to you. Two
// copies of this would drift, and the one you were not looking at would be the
// one that was wrong.
//
// `compact` is the builder's inline form; without it you get the full Settings
// card with its explanation.
export default function AnalysisModelPicker({ compact = false }) {
  const [models, setModels] = useState([])
  const [chatModel, setChatModel] = useState('')
  const [value, setValue] = useState('')
  const [saved, setSaved] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)

  useEffect(() => {
    api.getAIConfig().then((r) => setModels((r.models || []).filter((m) => m.available)))
      .catch(() => {})
    api.getPrefs().then((p) => {
      setValue(p.analysis_model || ''); setSaved(p.analysis_model || '')
      setChatModel(p.chat_model || '')
    }).catch(() => {})
  }, [])

  async function save() {
    setBusy(true); setMsg(null)
    try {
      await api.setPrefs({ analysis_model: value })
      setSaved(value)
      setMsg(value ? 'Agents will run on that model.' : 'Agents follow your chat model again.')
    } catch (e) { setMsg(e.message) } finally { setBusy(false) }
  }

  // Name the model the default actually RESOLVES to. "Follow my chat model" is
  // an abstraction; the thing a user needs to know is which model will run, and
  // an unset preference falls through chat model → first model they connected.
  const nameOf = (key) => models.find((m) => m.key === key)?.name || key
  const effective = chatModel || models[0]?.key || ''
  const select = (
    <select className="input" value={value} onChange={(e) => setValue(e.target.value)}>
      <option value="">
        {effective ? `Default — ${nameOf(effective)}` : 'Default (no model available yet)'}
      </option>
      {models.map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}
    </select>
  )

  if (compact) {
    return (
      <div className="admin-field-row" style={{ marginBottom: 14 }}>
        <label className="field">
          <span className="field-label">Agents run on</span>
          {select}
        </label>
        <button className="btn btn--primary" disabled={busy || value === saved} onClick={save}>
          {busy ? 'Saving…' : 'Save'}
        </button>
        {msg && <span className="card-sub" style={{ alignSelf: 'center' }}>{msg}</span>}
      </div>
    )
  }

  return (
    <section className="card">
      <div className="card-head">
        <Sparkles size={18} strokeWidth={1.75} />
        <div>
          <h2 className="card-title">Model for analysis agents</h2>
          <p className="card-sub">
            Which model your analysis agents run on — the ones you build, the ones on a schedule,
            and the ones an EA polls through the API. Separate from the model you pick in chat,
            because a model chosen for a conversation should not quietly become the one your
            trading signals depend on.
          </p>
        </div>
      </div>
      <div className="card-body">
        <label className="field">
          <span className="field-label">Agents run on</span>
          {select}
        </label>
        <div className="modal-actions">
          <button className="btn btn--primary" disabled={busy || value === saved} onClick={save}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
        {msg && <p className="card-sub" style={{ marginTop: 8 }}>{msg}</p>}
        <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.6, marginTop: 10 }}>
          Pick something dependable here. A model that is rate-limited or experimental does not
          fail loudly — an agent that cannot reach its model falls back to defaults and returns a
          no-trade verdict, which reads exactly like a quiet market.
        </p>
      </div>
    </section>
  )
}
