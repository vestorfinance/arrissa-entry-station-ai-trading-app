import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ExternalLink, Check, ArrowRight, X } from 'lucide-react'
import * as api from '../services/api.js'
import { backdrop } from '../services/backdrop.js'

// The first thing a new Community owner sees, and the last time they should
// have to think about any of it.
//
// Sign-up used to ask for an Exness email and password on its last step, which
// was wrong twice over: it asked somebody who may have no Exness account for
// credentials to one, and it asked for the single thing this app is careful
// never to keep. Meanwhile the two things the app genuinely cannot run without
// — somewhere to trade and a model to think with — were left to be discovered.
//
// So sign-up is a sign-up, and this is the setup: both in one modal, both
// skippable, and with the way to OPEN an account for somebody who has none.
// Skipping is deliberate. A wall between a person and the app they just
// installed is a wall they will resent, and the bell keeps asking anyway.

const AI_LABEL = {
  openai: 'OpenAI', anthropic: 'Anthropic', deepseek: 'DeepSeek',
  groq: 'Groq', openrouter: 'OpenRouter', gemini: 'Gemini', grok: 'Grok',
}
const SEEN = 'entrystation:setup-seen'

export default function SetupModal() {
  const [types, setTypes] = useState(null)
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [done, setDone] = useState({})          // kind -> true
  const navigate = useNavigate()

  // AI
  const [provider, setProvider] = useState('')
  const [aiKey, setAiKey] = useState('')
  // TradeLocker
  const [tl, setTl] = useState({ email: '', password: '', server: '', environment: 'demo' })

  useEffect(() => {
    if (localStorage.getItem(SEEN)) return
    Promise.all([api.appConfig(), api.listConnections()])
      .then(([cfg, data]) => {
        // Hosted accounts arrive configured; this is the self-hosted first run.
        if (cfg.edition !== 'community') return
        const have = new Set((data?.connections || []).map((c) => c.kind))
        const kinds = data?.types || []
        const ai = kinds.filter((t) => t.group === 'ai')
        const hasAI = ai.some((t) => have.has(t.kind))
        const tlType = kinds.find((t) => t.kind === 'tradelocker')
        // Nothing to ask about: everything it would offer is already done.
        if (hasAI && (!tlType || have.has('tradelocker'))) return
        setTypes({ ai, tl: tlType, hasAI, hasTL: have.has('tradelocker') })
        setProvider(ai[0]?.kind || '')
        setShow(true)
      })
      .catch(() => {})
  }, [])

  if (!show || !types) return null

  function close() {
    try { localStorage.setItem(SEEN, '1') } catch { /* private mode */ }
    setShow(false)
  }

  async function saveAI(e) {
    e.preventDefault()
    if (!provider || !aiKey.trim()) return
    setBusy('ai'); setErr('')
    try {
      await api.createConnection({
        kind: provider, name: AI_LABEL[provider] || provider,
        config: { api_key: aiKey.trim() },
      })
      setDone((d) => ({ ...d, ai: true }))
      setAiKey('')
    } catch (e2) { setErr(e2.message) } finally { setBusy('') }
  }

  async function saveTL(e) {
    e.preventDefault()
    setBusy('tl'); setErr('')
    try {
      await api.post(types.tl.managed_by?.connect || '/api/tradelocker/connect', tl)
      setDone((d) => ({ ...d, tl: true }))
    } catch (e2) { setErr(e2.message) } finally { setBusy('') }
  }

  const aiDone = types.hasAI || done.ai
  const tlDone = types.hasTL || done.tl

  return (
    <div className="modal-overlay" {...backdrop(close)}>
      <div className="modal setup-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-title">Finish setting up</span>
          <button className="node-text-grow" style={{ position: 'static', marginLeft: 'auto', opacity: 1 }}
                  onClick={close} title="Later">
            <X size={14} strokeWidth={2} />
          </button>
        </div>

        <p className="setup-intro">
          Two things the app cannot run without. Both can wait, and the bell will
          keep asking until they are done.
        </p>

        {err && <div className="alert alert--danger">{err}</div>}

        {/* A model first: without one there is no assistant and no agent, which
            is most of what somebody just installed this for. */}
        <section className="setup-step">
          <span className="setup-step-head">
            <em className={aiDone ? 'setup-num setup-num--done' : 'setup-num'}>
              {aiDone ? <Check size={12} strokeWidth={3} /> : '1'}
            </em>
            An AI provider
          </span>
          {aiDone ? (
            <p className="setup-note">Connected. Arrissa and your agents can think.</p>
          ) : (
            <form className="setup-body" onSubmit={saveAI}>
              <p className="setup-note">
                Arrissa and every analysis agent run on this. Your key stays on this
                machine.
              </p>
              <div className="setup-providers">
                {types.ai.map((t) => (
                  <button type="button" key={t.kind}
                          className={'pill setup-provider' + (provider === t.kind ? ' setup-provider--on' : '')}
                          onClick={() => setProvider(t.kind)}>
                    {t.logo && <img src={t.logo} alt="" />}
                    {t.name || AI_LABEL[t.kind] || t.kind}
                  </button>
                ))}
              </div>
              <div className="setup-row">
                <input className="input" type="password" value={aiKey} placeholder="Paste the API key"
                       onChange={(e) => setAiKey(e.target.value)} />
                <button className="btn btn--primary" disabled={busy === 'ai' || !aiKey.trim()}>
                  {busy === 'ai' ? 'Saving…' : 'Save'}
                </button>
              </div>
            </form>
          )}
        </section>

        {types.tl && (
          <section className="setup-step">
            <span className="setup-step-head">
              <em className={tlDone ? 'setup-num setup-num--done' : 'setup-num'}>
                {tlDone ? <Check size={12} strokeWidth={3} /> : '2'}
              </em>
              A trading account
            </span>
            {tlDone ? (
              <p className="setup-note">Connected. Your accounts are available to trade.</p>
            ) : (
              <form className="setup-body" onSubmit={saveTL}>
                <p className="setup-note">
                  {types.tl.blurb}
                </p>
                {/* For somebody who has no account yet. TradeLocker is a platform,
                    so the account is opened with a broker who runs on it. */}
                {types.tl.signup_options?.length > 0 && (
                  <div className="setup-brokers">
                    <span>No account yet? Open one with</span>
                    {types.tl.signup_options.map((o) => (
                      <a key={o.name} className="btn btn--ghost btn--sm" href={o.url}
                         target="_blank" rel="noreferrer">
                        {o.name} <ExternalLink size={12} />
                      </a>
                    ))}
                  </div>
                )}
                <div className="setup-grid">
                  <input className="input" placeholder="TradeLocker email" value={tl.email}
                         onChange={(e) => setTl({ ...tl, email: e.target.value })} />
                  <input className="input" type="password" placeholder="Password" value={tl.password}
                         onChange={(e) => setTl({ ...tl, password: e.target.value })} />
                  <input className="input" placeholder="Server" value={tl.server}
                         onChange={(e) => setTl({ ...tl, server: e.target.value })} />
                  <select className="input" value={tl.environment}
                          onChange={(e) => setTl({ ...tl, environment: e.target.value })}>
                    <option value="demo">Demo</option>
                    <option value="live">Live</option>
                  </select>
                </div>
                <button className="btn btn--primary" disabled={busy === 'tl' || !tl.email || !tl.password || !tl.server}>
                  {busy === 'tl' ? 'Connecting…' : 'Connect'}
                </button>
              </form>
            )}
          </section>
        )}

        <div className="modal-actions setup-actions">
          <button className="btn" onClick={() => { close(); navigate('/connections') }}>
            More connections <ArrowRight size={13} strokeWidth={2.2} />
          </button>
          <button className="btn btn--primary" onClick={close}>
            {aiDone && tlDone ? 'Done' : 'Later'}
          </button>
        </div>
      </div>
    </div>
  )
}
