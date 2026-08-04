import { useCallback, useEffect, useState } from 'react'
import { X, Trash2, Sparkles, Check, Plus, Maximize2 } from 'lucide-react'
import { backdrop } from '../../services/backdrop.js'
import { paletteItem } from './palette.js'
import { UNITS, floorNote } from './schedule.js'
import Dropdown from '../Dropdown.jsx'
import * as store from '../../services/agents.js'
import { billingChanged } from '../../services/billing.js'

const PROVIDER_LABEL = { anthropic: 'Claude', openai: 'OpenAI', deepseek: 'DeepSeek',
                         gemini: 'Gemini', grok: 'Grok', groq: 'Groq',
                         openrouter: 'OpenRouter' }

// ── args whose options a MODULE supplies ─────────────────────────────────────
// A module cannot ship React, so a picker it needs is declared as data: the arg
// says which endpoint holds the options and which fields are the value and the
// label. Core fetches and renders it, and knows nothing about what it is
// choosing — the same arrangement as guide pages and the palette itself.
function useArgOptions(arg) {
  const [opts, setOpts] = useState(null)      // null = still asking
  const load = useCallback(() => {
    if (!arg?.source) { setOpts([]); return }
    const token = localStorage.getItem('auth_token')
    fetch(arg.source, token ? { headers: { Authorization: `Bearer ${token}` } } : undefined)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((body) => {
        const rows = (arg.rows ? body?.[arg.rows] : body) || []
        setOpts(Array.isArray(rows) ? rows.map((r) => ({
          value: String(r[arg.value ?? 'value'] ?? ''),
          label: String(r[arg.text ?? 'label'] ?? r[arg.value ?? 'value'] ?? ''),
        })).filter((o) => o.value) : [])
      })
      .catch(() => setOpts([]))
  }, [arg?.source, arg?.rows, arg?.value, arg?.text])
  useEffect(load, [load])
  return [opts, load]
}

function SelectArg({ arg, value, onChange }) {
  const [opts] = useArgOptions(arg)
  if (opts === null) return <p className="node-settings-note">Loading…</p>
  if (!opts.length) return <p className="node-settings-note">{arg.none || 'Nothing to choose yet.'}</p>
  return (
    <Dropdown
      className="node-settings-dd"
      value={value || ''}
      onChange={onChange}
      placeholder={arg.empty || 'Default'}
      options={[{ value: '', label: arg.empty || 'Default' }, ...opts]}
    />
  )
}

function MultiSelectArg({ arg, value, onChange }) {
  const [opts] = useArgOptions(arg)
  const [typed, setTyped] = useState('')
  const chosen = Array.isArray(value) ? value : (value ? String(value).split(',') : [])
  const toggle = (v) => onChange(chosen.includes(v) ? chosen.filter((x) => x !== v) : [...chosen, v])

  // Anything chosen but not in the list still gets a pill — a chat typed by hand,
  // or one the bot has since been removed from. Dropping it silently would make
  // the node send somewhere the settings no longer admit to.
  const known = new Set(opts?.map((o) => o.value) || [])
  const extras = chosen.filter((c) => !known.has(c)).map((c) => ({ value: c, label: c }))
  const all = [...(opts || []), ...extras]

  return (
    <>
      {opts === null && <p className="node-settings-note">Loading…</p>}
      {opts !== null && !all.length && (
        <p className="node-settings-note">{arg.none || 'Nothing to choose yet.'}</p>
      )}
      {!!all.length && (
        <div className="ai-models" style={{ marginTop: 2 }}>
          {all.map((o) => (
            <button key={o.value} type="button"
                    className={'pill pill-opt' + (chosen.includes(o.value) ? ' pill-opt--on' : '')}
                    onClick={() => toggle(o.value)}>
              {chosen.includes(o.value) && <Check size={12} strokeWidth={2.5} />}
              {o.label}
            </button>
          ))}
        </div>
      )}
      {arg.free && (
        <div className="node-arg-add">
          <input className="input" value={typed} placeholder="Add another…"
                 onChange={(e) => setTyped(e.target.value)}
                 onKeyDown={(e) => {
                   if (e.key !== 'Enter') return
                   e.preventDefault()
                   const v = typed.trim()
                   if (v && !chosen.includes(v)) onChange([...chosen, v])
                   setTyped('')
                 }} />
          <button type="button" className="btn btn--ghost btn--sm" disabled={!typed.trim()}
                  onClick={() => {
                    const v = typed.trim()
                    if (v && !chosen.includes(v)) onChange([...chosen, v])
                    setTyped('')
                  }}>
            <Plus size={13} strokeWidth={2} /> Add
          </button>
        </div>
      )}
      {!chosen.length && <p className="node-settings-note">{arg.empty || 'Nothing selected.'}</p>}
    </>
  )
}

// Data-gathering nodes that can form their own opinion on what they fetched.
const OPINION_KINDS = new Set([
  'truth-social', 'news', 'fed-watch', 'economic-calendar', 'sentiment-volume', 'bond-yields',
  'market-data', 'artificial-sentiment',
])

// What each node does, and what it assumes when its text is left empty.
const NODE_HELP = {
  'trigger-agent-call': 'Entry point. Describe the input this agent expects — it becomes part of the tool description the chat agent sees when deciding to call it.',
  'truth-social': 'Fetches Truth Social posts and reads them. No text ⇒ Trump, last 24 hours.',
  news: 'Fetches market news, impact-scored. No text ⇒ last 12 hours, all impacts.',
  'fed-watch': 'Reads CME FedWatch rate-cut probabilities. No text ⇒ the latest snapshot.',
  'economic-calendar': "Reads scheduled economic releases. No text ⇒ today's events.",
  'sentiment-volume': 'Reads retail long/short sentiment. No text ⇒ all tracked symbols.',
  'artificial-sentiment': 'Reconstructs who CONTROLS the market from its own candles — swings, liquidity sweeps, volume and wick absorption. Works on any instrument and any timeframe, unlike the Myfxbook node. Name the symbol and optionally the timeframe (e.g. “gold positioning on H1”). Returns bulls/bears %, each side’s average entry and how much of each side is trapped underwater; it also fetches the real retail reading so the flow can see where the crowd disagrees with the price footprint.',
  'market-data': 'Reads live price or candles — name a symbol in the text (e.g. “gold M15 candles”).',
  hmr: 'Exness High Margin Requirements. Name the instrument (e.g. "HMR for gold") — returns whether its leverage is capped now, the cap, when it lifts and the next window. Omit the symbol for all active/upcoming windows.',
  'risk-management': 'Smart SL/TP + lot sizing. Name the symbol, the side (or say “infer from the bias”), the style (scalp/intraday/swing/position) and the risk (e.g. “size a swing long on gold risking 2%”). It reads live structure + ATR to place the stop, sets the target by reward:risk, and sizes the lot — risk defaults to your saved setting.',
  'time-session': 'Returns the current UTC time and which forex sessions (Sydney/Tokyo/London/New York) are open, plus overlaps. No text needed.',
  if: 'Branches the flow: an AI reads your condition against the data gathered so far and follows the true / false handle.',
  respond: 'Composes the final answer back to the calling agent from everything gathered. Terminal node.',
  versatile: 'Freely consults any analysis source to satisfy your description, then answers.',
  'call-agent': 'Runs another of your agents and waits for it, then feeds its response into this flow. Pick the agent to call; the optional text is an instruction to send it. With chain of thought on, the reasoning so far is passed in too, and the called agent’s response flows to the next node.',
  'trigger-interval': 'Entry point on a clock: the agent runs itself, without waiting to be called. Set a plain interval, or a cron expression for something calendar-shaped like weekday mornings. Only ACTIVE agents run — a draft or paused one keeps its schedule but stays put — and every run costs credits, like any other.',
}

// The schedule editor. Two ways to say when, and they are alternatives rather
// than a form with optional halves: you pick the one that fits, and only that one
// is read at run time.
function ScheduleSettings({ values, set }) {
  const mode = values.mode || 'every'
  const [brief, setBrief] = useState(values.cron_brief || '')
  const [busy, setBusy] = useState(false)
  const [said, setSaid] = useState(null)     // {explanation, credits} | {error}

  async function writeCron() {
    const b = brief.trim()
    if (!b || busy) return
    setBusy(true)
    setSaid(null)
    try {
      const res = await store.suggestCron(b)
      set({ ...values, mode: 'cron', cron: res.cron, cron_brief: b })
      setSaid({ explanation: res.explanation || res.reads_as, credits: res.credits_charged })
      billingChanged()                       // it cost credits — refresh the meter
    } catch (e) {
      setSaid({ error: e.message || 'Could not write that schedule.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <label className="field">
        <span className="field-label">How often</span>
        <div className="pill-row">
          <button type="button" className={'pill-opt' + (mode !== 'cron' ? ' pill-opt--on' : '')}
                  onClick={() => set({ ...values, mode: 'every' })}>Every…</button>
          <button type="button" className={'pill-opt' + (mode === 'cron' ? ' pill-opt--on' : '')}
                  onClick={() => set({ ...values, mode: 'cron' })}>Cron expression</button>
        </div>
      </label>

      {mode !== 'cron' ? (
        <label className="field">
          <span className="field-label">Run every</span>
          <div className="sched-every">
            <input
              className="input sched-num"
              type="number"
              min="1"
              step="1"
              value={values.every ?? ''}
              placeholder="15"
              onChange={(e) => set({ ...values, every: e.target.value })}
            />
            <Dropdown
              className="sched-unit"
              value={values.unit || 'minutes'}
              onChange={(v) => set({ ...values, unit: v })}
              options={UNITS.map((u) => ({ value: u, label: u }))}
            />
          </div>
          {floorNote(values) && <p className="node-settings-note">{floorNote(values)}</p>}
        </label>
      ) : (
        <>
          <label className="field">
            <span className="field-label">Cron expression <span className="muted">· UTC</span></span>
            <input
              className="input sched-cron"
              value={values.cron || ''}
              placeholder="0 7 * * 1-5"
              spellCheck={false}
              onChange={(e) => set({ ...values, cron: e.target.value })}
            />
            <p className="node-settings-note">
              Five fields: minute hour day-of-month month day-of-week. <code>0 7 * * 1-5</code> is
              07:00 UTC on weekdays; <code>*/30 * * * *</code> is every half hour.
            </p>
          </label>

          <label className="field">
            <span className="field-label">…or describe it and let AI write it</span>
            <textarea
              className="node-settings-text"
              value={brief}
              placeholder="every weekday half an hour before London opens"
              spellCheck={false}
              onChange={(e) => setBrief(e.target.value)}
            />
            <button type="button" className="btn btn--ghost sched-ai" disabled={busy || !brief.trim()}
                    onClick={writeCron}>
              <Sparkles size={14} strokeWidth={2} />
              {busy ? 'Writing…' : 'Write the cron'}
            </button>
            {/* An AI call, so it is charged like one — said out loud, before it is
                pressed and again after, rather than discovered on the meter. */}
            <p className="node-settings-note">Uses your AI model and costs credits, like a chat message.</p>
            {said?.error && <p className="node-settings-note node-settings-note--bad">{said.error}</p>}
            {said?.explanation && (
              <p className="node-settings-note node-settings-note--good">
                {said.explanation}
                {said.credits ? ` · ${said.credits} credit${said.credits === 1 ? '' : 's'}` : ''}
              </p>
            )}
          </label>
        </>
      )}

      <label className="field">
        <span className="field-label">What to analyse when it fires</span>
        <textarea
          className="node-settings-text"
          value={values.text || ''}
          placeholder="Analyse XAUUSD for an intraday trade"
          spellCheck={false}
          onChange={(e) => set({ ...values, text: e.target.value })}
        />
        <p className="node-settings-note">
          Nobody is there to ask, so this stands in for the request a caller would have made.
          Empty ⇒ the agent's own description is used.
        </p>
      </label>
    </>
  )
}

// Right-hand settings drawer for the selected node. Configurable args get an
// editor; runtime args (e.g. the trigger's requirement) are shown read-only.
export default function NodeSettings({ node, models = [], agents = [], defaultModel = '',
                                       onChange, onDelete, onClose }) {
  const [big, setBig] = useState(null)     // which text arg is open in the big window
  const item = paletteItem(node.data.kind)
  const configurable = item?.configurable
  const values = node.data.values || {}

  const set = (name, value) => onChange(node.id, { ...values, [name]: value })
  // Picking an agent stores both its id (used at runtime) and name (shown on the canvas).
  const setAgent = (id) => onChange(node.id, {
    ...values, agent_id: id,
    agent_name: (agents.find((a) => String(a.id) === String(id)) || {}).name || '',
  })

  // `m.key` is the one identifier both editions agree on: a branded tier on
  // cloud ("arrissa-pro"), a provider:model on a community box. It is also what
  // the engine resolves, so what is stored here is what actually runs — the old
  // `m.provider`/`m.model` fields are not in this payload at all.
  // Say WHICH model the default is. "Use the agent default" tells you there is
  // one; it does not tell you whether this node is about to run on the fast
  // model or the expensive one.
  const defaultName = models.find((m) => m.key === defaultModel)?.name || defaultModel
  const inheritLabel = !models.length ? 'No models — add one in Settings'
    : defaultName ? `Agent default — ${defaultName}`
    : 'Agent default'
  const modelOpts = (firstLabel) => [
    { value: '', label: firstLabel },
    ...models.map((m) => {
      const [prov] = String(m.key || '').split(':')
      return {
        value: m.key,
        label: m.key.includes(':')
          ? `${PROVIDER_LABEL[prov] || prov} · ${m.name}`
          : m.name,
      }
    }),
  ]

  return (
    <aside className="node-settings">
      <div className="node-settings-head">
        <div className="node-settings-main">
          <div className="node-settings-title">{node.data.label}</div>
          <div className="node-settings-sub">{node.data.sub}</div>
        </div>
        <button className="btn btn--ghost btn--icon" title="Close" onClick={onClose}>
          <X size={16} strokeWidth={1.75} />
        </button>
      </div>

      <div className="node-settings-body">
        {NODE_HELP[node.data.kind] && (
          <p className="node-settings-note" style={{ marginTop: 0 }}>{NODE_HELP[node.data.kind]}</p>
        )}

        {node.data.kind === 'trigger-interval' && (
          <ScheduleSettings values={values} set={(v) => onChange(node.id, v)} />
        )}

        {(node.data.args || []).map((a) => (
          <label className="field" key={a.name}>
            <span className="field-label">
              {a.label || a.name}
              {!a.label && <span className="muted"> · {a.type}</span>}
              {a.required && <span className="muted"> · required</span>}
            </span>

            {!configurable ? (
              <p className="node-settings-note">
                Provided by the calling agent at runtime.
              </p>
            ) : a.type === 'agent' ? (
              agents.length ? (
                <Dropdown
                  className="node-settings-dd"
                  value={values.agent_id || ''}
                  onChange={setAgent}
                  placeholder="Choose an agent to call…"
                  options={[
                    { value: '', label: 'Choose an agent to call…' },
                    ...agents.map((ag) => ({
                      value: String(ag.id),
                      label: ag.name + (ag.status && ag.status !== 'active' ? ` · ${ag.status}` : ''),
                    })),
                  ]}
                />
              ) : (
                <p className="node-settings-note">
                  You have no other agents to call yet — create another agent first.
                </p>
              )
            ) : a.type === 'select' ? (
              <SelectArg arg={a} value={values[a.name]} onChange={(v) => set(a.name, v)} />
            ) : a.type === 'multiselect' ? (
              <MultiSelectArg arg={a} value={values[a.name]} onChange={(v) => set(a.name, v)} />
            ) : a.type === 'text' ? (
              <div className="node-text-wrap">
                {/* The instruction is prose and wants room; a query string is one
                    short line and does not. Giving both the same 160px box made
                    the parameters field look like somewhere to write an essay. */}
                <textarea
                  className={'node-settings-text'
                    + (a.name === 'api_params' ? ' node-settings-text--short' : '')}
                  value={values[a.name] || ''}
                  placeholder={a.placeholder || 'What should this node send?'}
                  onChange={(e) => set(a.name, e.target.value)}
                  spellCheck={false}
                />
                {/* The drawer is narrow by design, and a node's instruction is
                    prose. This opens the same value in a window big enough to
                    read it back. */}
                <button type="button" className="node-text-grow" title="Write this in a bigger window"
                        onClick={() => setBig(a.name)}>
                  <Maximize2 size={13} strokeWidth={2} />
                </button>
              </div>
            ) : (
              <input
                className="input"
                value={values[a.name] || ''}
                onChange={(e) => set(a.name, e.target.value)}
              />
            )}
          </label>
        ))}

        {item?.model && (
          <label className="field">
            <span className="field-label">AI model (this node)</span>
            <Dropdown
              className="node-settings-dd"
              value={values.model || ''}
              onChange={(v) => set('model', v)}
              placeholder={inheritLabel}
              options={modelOpts(inheritLabel)}
            />
          </label>
        )}

        {OPINION_KINDS.has(node.data.kind) && (
          <>
            <label className="field">
              <span className="field-label">Require opinion</span>
              <p className="node-settings-note" style={{ marginTop: 0 }}>
                After fetching, this node forms its own short analysis of the data and attaches it as an output.
              </p>
              <div className="pill-row">
                <button type="button"
                        className={'pill-opt' + (values.opinion ? ' pill-opt--on' : '')}
                        onClick={() => set('opinion', true)}>Yes</button>
                <button type="button"
                        className={'pill-opt' + (!values.opinion ? ' pill-opt--on' : '')}
                        onClick={() => set('opinion', false)}>No</button>
              </div>
            </label>

            {values.opinion && (
              <label className="field">
                <span className="field-label">Opinion AI model</span>
                <Dropdown
                  className="node-settings-dd"
                  value={values.opinion_model || ''}
                  onChange={(v) => set('opinion_model', v)}
                  placeholder="Use this node's model"
                  options={modelOpts("Use this node's model")}
                />
              </label>
            )}
          </>
        )}
      </div>

      {/* What this node's API will actually read. At the bottom, because it is
          reference rather than a control — you consult it while filling the
          field above, and it should not push that field down the panel.
          The list comes from each node's own handler, so it cannot promise a key
          the node does not accept. */}
      {(node.data.apiKeys || node.data.api_keys) && (
        <div className="node-api-help">
          <span className="node-api-help-title">Parameters this node accepts</span>
          <p className="node-api-help-keys">{node.data.apiKeys || node.data.api_keys}</p>
          {(node.data.apiExample || node.data.api_example) && (
            <button type="button" className="node-api-help-ex"
                    title="Use this example"
                    onClick={() => set('api_params', node.data.apiExample || node.data.api_example)}>
              <code>{node.data.apiExample || node.data.api_example}</code>
            </button>
          )}
        </div>
      )}

      <div className="node-settings-foot">
        <button className="btn btn--danger btn--block" onClick={() => onDelete(node.id)}>
          <Trash2 size={15} strokeWidth={1.75} />
          Delete node
        </button>
      </div>

      {/* Same value, more room. It edits `values` directly, so what is typed
          here is already saved — closing is not a commit, and there is nothing
          to discard. */}
      {big && (
        <div className="modal-overlay" {...backdrop(() => setBig(null))}>
          <div className="modal node-text-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <span className="modal-title">{big}</span>
              <button className="node-text-grow" style={{ position: 'static', marginLeft: 'auto', opacity: 1 }}
                      onClick={() => setBig(null)} title="Close">
                <X size={14} strokeWidth={2} />
              </button>
            </div>
            <textarea
              className="node-text-modal-area"
              autoFocus
              value={values[big] || ''}
              placeholder="What should this node do?"
              spellCheck={false}
              onChange={(e) => set(big, e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Escape') setBig(null) }}
            />
            <div className="modal-actions">
              <button className="btn btn--primary" onClick={() => setBig(null)}>Done</button>
            </div>
          </div>
        </div>
      )}
    </aside>
  )
}
