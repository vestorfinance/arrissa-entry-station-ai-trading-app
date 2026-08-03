import { useEffect, useState } from 'react'
import { Bot, Check, RotateCcw, ChevronDown, ChevronRight } from 'lucide-react'
import DashboardLayout from '../components/DashboardLayout.jsx'
import { useAppName } from '../services/appConfig.js'
import * as api from '../services/api.js'

// What the assistant is told, and what you can add to it.
//
// Instructions are APPENDED, never a replacement. Wholesale replacement was
// built and then removed: the built-in prompt is what teaches the assistant when
// to reach for each tool and the exact format the app renders as a trade card,
// and an editor that can silently switch all of that off is a footgun wearing a
// warning label. Adding to it does everything anyone actually wanted.
export default function AgentPrompt() {
  const appName = useAppName()
  const [d, setD] = useState(null)
  const [instructions, setInstructions] = useState('')
  const [showBase, setShowBase] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [err, setErr] = useState(null)

  const load = () => api.getAgentPrompt().then((r) => {
    setD(r); setInstructions(r.instructions || '')
  }).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])

  async function save(patch, note) {
    setBusy(true); setErr(null); setMsg(null)
    try { setD(await api.saveAgentPrompt(patch)); setMsg(note) }
    catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const dirty = d && instructions !== (d.instructions || '')

  return (
    <DashboardLayout title="Chat Agent Prompt">
      <div className="settings-stack">
        <section className="card">
          <div className="card-head">
            <Bot size={18} strokeWidth={1.75} />
            <div>
              <h2 className="card-title">Your instructions</h2>
              <p className="card-sub">
                Standing instructions for the assistant, on every conversation. Write how you want
                it to work — the markets you care about, how cautious to be, how to format an
                answer. These go last, so where they disagree with a built-in preference, yours win.
              </p>
            </div>
          </div>
          <div className="card-body">
            {err && <div className="alert alert--danger">{err}</div>}
            {msg && <div className="alert alert--ok"><Check size={14} strokeWidth={2} /> {msg}</div>}

            <textarea
              className="input prompt-box"
              rows={10}
              value={instructions}
              placeholder={'e.g. I trade gold and the majors only — do not suggest crypto.\n'
                + 'Always give me the invalidation level before the target.\n'
                + 'Keep answers short; I will ask if I want the reasoning.'}
              onChange={(e) => setInstructions(e.target.value)} />

            <div className="guide-actions">
              <button className="btn btn--primary" disabled={busy || !dirty}
                      onClick={() => save({ instructions }, 'Instructions saved.')}>
                {busy ? 'Saving…' : 'Save'}
              </button>
              {!!(d?.instructions) && (
                <button className="btn btn--ghost" disabled={busy}
                        onClick={() => { setInstructions(''); save({ instructions: '' }, 'Instructions cleared.') }}>
                  <RotateCcw size={14} strokeWidth={2} /> Clear
                </button>
              )}
            </div>
          </div>
        </section>

        <section className="card">
          <div className="card-body">
            <button className="prompt-disclose" onClick={() => setShowBase((v) => !v)}>
              {showBase ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
              What {appName} already tells it
              <span className="muted">{d ? ` · ${d.built_in_chars.toLocaleString()} characters` : ''}</span>
            </button>
            {showBase && (
              <>
                <p className="card-sub">
                  Read-only. Worth a look before you write an instruction — this is what yours is
                  added to, and an instruction that contradicts it will fight rather than win.
                </p>
                <pre className="prompt-base">{d?.built_in}</pre>
              </>
            )}
          </div>
        </section>

      </div>
    </DashboardLayout>
  )
}
