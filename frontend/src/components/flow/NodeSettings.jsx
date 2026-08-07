import { useCallback, useEffect, useRef, useState } from 'react'
import { X, Trash2, Sparkles, Check, Plus, Maximize2, Play, AlertTriangle} from 'lucide-react'
import { backdrop } from '../../services/backdrop.js'
import { paletteItem } from './palette.js'
import { UNITS, floorNote } from './schedule.js'
import Dropdown from '../Dropdown.jsx'
import ParamsField from './ParamsField.jsx'
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
  'trade-actions': 'The node that DOES things. It has the instrument, order and position tools and calls them in a loop \u2014 resolve the symbol, read what is open, size it properly, place it, confirm \u2014 deciding each step from what the last one returned, up to 10 rounds. Write the instruction in plain words. It stops as soon as the job is done, and says so plainly if it runs out of rounds rather than reporting success. Sizing always goes through the risk engine rather than being worked out by hand. State a fixed call in API parameters instead and no model is used at all.',
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

// What has to happen for the agent to wake up.
//
// A list of conditions and how they combine. Each row's shape depends on what
// it watches — a symbol list for news about instruments, an amount and a unit
// for anything timed against a release — so this is its own control rather than
// a generic field: a textarea cannot ask "how long before".
//
// Conditions whose module is not installed are shown, disabled, with the reason.
// Hiding them would leave somebody wondering why the guide mentions something
// they cannot find; offering them silently would let a flow be built that never
// fires.
// Its own list: schedule.js exports UNITS as bare strings for the clock
// trigger, and this control needs {value,label} pairs. Named apart rather than
// reshaped in place, so neither one changes under the other.
const DT_UNITS = [
  { value: 'seconds', label: 'seconds' },
  { value: 'minutes', label: 'minutes' },
  { value: 'hours', label: 'hours' },
]
// Each source's own words. The calendar says "moderate" where the news says
// "medium", so one shared list would have offered a calendar condition a value
// no event ever has — filtering everything out and looking like a quiet week.
// The server sends what each kind accepts; this is only the fallback.
const IMPACT_LABEL = {
  any: 'Any impact', high: 'High only', medium: 'Medium only',
  moderate: 'Moderate only', low: 'Low only',
}

function DataTriggerSettings({ values, set }) {
  const [kinds, setKinds] = useState([])
  const conditions = values.conditions || []
  const combine = values.combine || 'or'

  useEffect(() => {
    store.triggerSources().then((r) => setKinds(r.kinds || [])).catch(() => {})
  }, [])

  const setC = (i, patch) => set('conditions',
    conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)))
  const add = () => set('conditions', [...conditions, { kind: 'truth', impact: 'any' }])
  const del = (i) => set('conditions', conditions.filter((_, j) => j !== i))

  const timed = (k) => k === 'before_event' || k === 'after_event'
  const info = (k) => kinds.find((x) => x.kind === k)

  return (
    <div className="field dt">
      <span className="field-label">Run when</span>

      {conditions.length === 0 && (
        <p className="node-settings-note">
          Nothing yet, so this agent will not run on its own. Add a condition.
        </p>
      )}

      {conditions.map((c, i) => (
        <div className="dt-row" key={i}>
          <div className="dt-head">
            <span className="dt-n">{i + 1}</span>
            <Dropdown
              value={c.kind}
              onChange={(v) => setC(i, { kind: v })}
              /* The retired kind stays selectable only where a flow already
                 uses it: removing it from the list would silently rewrite a
                 saved condition to whatever sorted first. */
              options={kinds.filter((k) => !k.legacy || k.kind === c.kind).map((k) => ({
                value: k.kind,
                label: k.available ? k.label : `${k.label} — needs its module`,
                disabled: !k.available,
              }))}
            />
            <button type="button" className="var-del" title="Remove" onClick={() => del(i)}>
              <X size={13} />
            </button>
          </div>

          {info(c.kind) && !info(c.kind).available && (
            <p className="node-settings-note dt-warn">
              The module this reads is not installed, so this condition can never fire.
            </p>
          )}

          {/* Offered by whichever conditions declare they take instruments,
              rather than by name. Empty means every story; a list means any one
              of them, which is what somebody naming three instruments wants. */}
          {info(c.kind)?.symbols && (
            <>
              <input className="input" value={c.symbols || ''}
                     placeholder="XAUUSD, GBPUSD, US30  —  leave empty for all news"
                     onChange={(e) => setC(i, { symbols: e.target.value })} />
              <p className="node-settings-note dt-hint">
                {(c.symbols || '').trim()
                  ? 'Fires on a story about ANY of these.'
                  : 'Fires on every story. Name instruments to narrow it.'}
              </p>
            </>
          )}

          {timed(c.kind) && (
            <div className="dt-when">
              <input className="input dt-amount" type="number" min="0"
                     value={c.amount ?? 15}
                     onChange={(e) => setC(i, { amount: e.target.value })} />
              <Dropdown value={c.unit || 'minutes'} onChange={(v) => setC(i, { unit: v })}
                        options={DT_UNITS} />
              <span className="dt-when-tail">
                {c.kind === 'before_event' ? 'before it lands' : 'after it prints'}
              </span>
            </div>
          )}

          {(
            <Dropdown value={c.impact || 'any'} onChange={(v) => setC(i, { impact: v })}
                      options={(info(c.kind)?.impacts || ['any', 'high', 'low'])
                        .map((v) => ({ value: v, label: IMPACT_LABEL[v] || v }))} />
          )}
        </div>
      ))}

      <button type="button" className="btn btn--sm var-add" onClick={add}>
        <Plus size={13} strokeWidth={2} /> Add a condition
      </button>

      {conditions.length > 1 && (
        <div className="dt-combine">
          <span className="field-label">Combine them with</span>
          <Dropdown value={combine} onChange={(v) => set('combine', v)}
                    options={[
                      { value: 'or', label: 'OR — any one of them is enough' },
                      { value: 'and', label: 'AND — all of them, together' },
                    ]} />
          <p className="node-settings-note">
            {combine === 'and'
              ? 'All of them have to happen within the same half-minute. Use it for "a post AND a story", not for things that arrive hours apart.'
              : 'Whichever happens first runs the agent.'}
          </p>
        </div>
      )}
    </div>
  )
}

// Right-hand settings drawer for the selected node. Configurable args get an
// editor; runtime args (e.g. the trigger's requirement) are shown read-only.
export default function NodeSettings({ node, variables = [], models = [], agents = [], defaultModel = '',
                                       onChange, onDelete, onClose }) {
  const paramsRef = useRef(null)
  const [rulesOpen, setRulesOpen] = useState(false)
  // What the call actually returns. Guessing from a parameter list is how a
  // flow gets built on a filter that was never biting.
  const [tryOut, setTryOut] = useState(null)     // {busy, ms, count, result, error}
  const [big, setBig] = useState(null)     // which text arg is open in the big window

  // What this node's API accepts. Core nodes spell it apiDoc; module nodes send
  // api_doc through the palette. Same thing, two naming conventions meeting.
  //
  // Read from the PALETTE first, and from the saved node only as a fallback.
  // The documentation belongs to the installed module, not to a flow somebody
  // drew last month — a node saved before its module published a parameter list
  // carries no list, and copying that stale blank into the drawer is how this
  // window came up empty for a node whose module documents nine fields. There
  // is an effect that patches saved nodes when the palette loads; this does not
  // depend on it having run.
  const live = paletteItem(node.data.kind)
  const apiDoc = live?.apiDoc || live?.api_doc || node.data.apiDoc || node.data.api_doc || []

  // Clicking a value writes `key=value` into the field, REPLACING that key if it
  // is already there — clicking `high` then `low` should change impact, not send
  // both and leave the API to pick.
  // Put `{{name}}` where the caret is, not at the end. Somebody adding a
  // variable is usually mid-line — `symbol=` with the cursor after the equals —
  // and appending would put it in the wrong place every time.
  // text/plain is what the textarea inserts, and what a drop anywhere else
  // pastes. Nothing else is needed: the field works out for itself whether what
  // arrived needs an `&` in front of it.
  function drag(e, text) {
    e.dataTransfer.setData('text/plain', text)
    e.dataTransfer.effectAllowed = 'copy'
  }

  function insertVar(name) {
    const token = `{{${name}}}`
    const el = paramsRef.current
    const cur = values.api_params || ''
    if (!el) { set('api_params', cur + token); return }
    const a = el.selectionStart ?? cur.length
    const b = el.selectionEnd ?? cur.length
    set('api_params', cur.slice(0, a) + token + cur.slice(b))
    // Put the caret after what was just inserted, so two chips in a row do not
    // land on top of each other.
    requestAnimationFrame(() => {
      el.focus()
      el.setSelectionRange(a + token.length, a + token.length)
    })
  }

  function addParam(key, value) {
    const cur = (values.api_params || '').trim()
    const kept = cur
      .replace(/\n/g, '&').split('&')
      .map((s2) => s2.trim())
      .filter((s2) => s2 && s2.split('=')[0].trim() !== key)
    set('api_params', [...kept, `${key}=${value}`].join('&'))
  }
  const item = paletteItem(node.data.kind)
  const configurable = item?.configurable
  const values = node.data.values || {}

  const set = (name, value) => onChange(node.id, { ...values, [name]: value })

  // The variables a trigger declares. Everything downstream can then use them —
  // `symbol={{symbol}}` in a parameter, `trade_type=scalper` as a condition — so
  // the instrument stops being something each node re-derives from the prose.
  const vars = values.vars || []
  const setVars = (v) => set('vars', v)
  const isTrigger = ['trigger-agent-call', 'trigger', 'trigger-interval', 'triggerInterval',
    'trigger-data'].includes(node.data.kind)

  // The conditional calls on a data node. Ordered, first match wins, and a rule
  // with no condition is the default — which is why order is editable.
  const rules = values.api_rules || []
  const setRules = (r) => set('api_rules', r)

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

        {node.data.kind === 'trigger-data' && (
          <DataTriggerSettings values={values} set={set} />
        )}

        {isTrigger && (
          <div className="field">
            <span className="field-label">
              Variables this agent needs
              <span className="muted"> · used downstream as {'{{name}}'}</span>
            </span>
            {vars.map((v, i) => (
              <div className="var-row" key={i}>
                <input
                  className="input var-key"
                  value={v.key || ''}
                  placeholder="symbol"
                  onChange={(e) => setVars(vars.map((x, j) =>
                    (j === i ? { ...x, key: e.target.value.trim() } : x)))}
                />
                {/* Required means the run REFUSES without it, rather than
                    proceeding on a guess and presenting the result as asked-for. */}
                <button type="button"
                        className={'var-req' + (v.required ? ' var-req--on' : '')}
                        title={v.required ? 'Required' : 'Optional'}
                        onClick={() => setVars(vars.map((x, j) =>
                          (j === i ? { ...x, required: !x.required } : x)))}>
                  <Check size={13} strokeWidth={2.5} /> required
                </button>
                <button type="button" className="var-del" title="Remove"
                        onClick={() => setVars(vars.filter((_, j) => j !== i))}>
                  <X size={13} />
                </button>
              </div>
            ))}
            <button type="button" className="btn btn--sm var-add"
                    onClick={() => setVars([...vars, { key: '', required: true }])}>
              <Plus size={13} strokeWidth={2} /> Add a variable
            </button>
          </div>
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
                {a.name === 'api_params' ? (
                  <ParamsField
                    className="node-settings-text node-settings-text--short"
                    value={values[a.name] || ''}
                    /* This node's own example, not a generic one. A source
                       whose filter can be switched off shows that in its
                       example, which is the only place somebody looking at an
                       empty field will read it. */
                    placeholder={live?.apiExample || live?.api_example
                                 || a.placeholder || 'symbol=XAUUSD&count=15'}
                    onChange={(v) => set(a.name, v)}
                  />
                ) : (
                  <textarea
                    className="node-settings-text"
                    value={values[a.name] || ''}
                    placeholder={a.placeholder || 'What should this node send?'}
                    onChange={(e) => set(a.name, e.target.value)}
                    spellCheck={false}
                  />
                )}
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
      {(live?.apiKeys || live?.api_keys || node.data.apiKeys || node.data.api_keys) && (
        <div className="node-api-help">
          <span className="node-api-help-title">Parameters this node accepts</span>
          <p className="node-api-help-keys">{live?.apiKeys || live?.api_keys || node.data.apiKeys || node.data.api_keys}</p>
          {(live?.apiExample || live?.api_example || node.data.apiExample || node.data.api_example) && (
            <button type="button" className="node-api-help-ex"
                    title="Use this example"
                    onClick={() => set('api_params', live?.apiExample || live?.api_example || node.data.apiExample || node.data.api_example)}>
              <code>{live?.apiExample || live?.api_example || node.data.apiExample || node.data.api_example}</code>
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
          <div className={'modal node-text-modal'
                          + (tryOut && (tryOut.result || tryOut.error) ? ' node-text-modal--split' : '')}
               onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <span className="modal-title">{big}</span>
              <button className="node-text-grow" style={{ position: 'static', marginLeft: 'auto', opacity: 1 }}
                      onClick={() => setBig(null)} title="Close">
                <X size={14} strokeWidth={2} />
              </button>
            </div>

            {/* Two columns once there is an answer, not before. The window grew
                past the screen when a result was appended underneath what
                produced it — and the two are read together, so stacking them
                puts the call out of sight exactly when you want to compare it
                with what came back. Each column scrolls on its own. */}
            <div className="ptm-cols">
              <div className="ptm-left">
            {big === 'api_params' ? (
              <ParamsField
                big
                className="node-text-modal-area node-text-modal-area--short"
                inputRef={paramsRef}
                autoFocus
                value={values[big] || ''}
                placeholder={live?.apiExample || live?.api_example
                             || 'symbol=XAUUSD&count=15&timeframe=M15'}
                onChange={(v) => set(big, v)}
                onKeyDown={(e) => { if (e.key === 'Escape') setBig(null) }}
              />
            ) : (
              <textarea
                className="node-text-modal-area"
                autoFocus
                value={values[big] || ''}
                placeholder="What should this node do?"
                spellCheck={false}
                onChange={(e) => set(big, e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Escape') setBig(null) }}
              />
            )}

            {/* Every key this node's API reads, with the values each one takes.
                Not one example: an example shows that a thing is possible and
                leaves you guessing at the rest, which is how somebody ends up
                reading the module's source to find out that News takes
                `min_score`. Clicking a value writes `key=value` into the field,
                so the documentation is also the way to fill it in. */}
            {big === 'api_params' && (
              <div className="try-bar">
                <button type="button" className="btn btn--primary btn--sm"
                        disabled={tryOut?.busy || !(values.api_params || '').trim()}
                        onClick={async () => {
                          setTryOut({ busy: true })
                          try {
                            const vars = Object.fromEntries(
                              (variables || []).map((v) => [v.key, v.example || v.key]))
                            const r = await store.previewParams(
                              node.data.kind, values.api_params, vars)
                            setTryOut({ ...r, busy: false })
                          } catch (e) { setTryOut({ busy: false, error: e.message }) }
                        }}>
                  <Play size={13} strokeWidth={2.4} />
                  {tryOut?.busy ? 'Running…' : 'Test this call'}
                </button>
                {tryOut && !tryOut.busy && !tryOut.error && (
                  <span className="try-meta">
                    {tryOut.count != null ? `${tryOut.count} row${tryOut.count === 1 ? '' : 's'} · ` : ''}
                    {tryOut.ms} ms
                  </span>
                )}
                {tryOut && <button type="button" className="var-del" title="Clear"
                                   onClick={() => setTryOut(null)}><X size={13} /></button>}
              </div>
            )}

            {/* The variables this agent was given. Drag one into the field, or
                click it — dragging is the gesture people reach for and clicking
                is the one that always works, so it does both. */}
            {big === 'api_params' && variables.length > 0 && (
              <div className="var-bar">
                <span className="var-bar-label">Variables</span>
                {variables.map((v) => (
                  <button type="button" className="pill var-chip" key={v.key}
                          draggable
                          onDragStart={(e) => drag(e, `{{${v.key}}}`)}
                          title={`Drag into the field, or click to insert {{${v.key}}}`}
                          onClick={() => insertVar(v.key)}>
                    {'{{'}{v.key}{'}}'}
                  </button>
                ))}
                <span className="var-bar-hint">drag in, or click</span>
              </div>
            )}

            {/* One call is the common case and stays a single field above. This is
                for when it is not: several calls, each with the condition it
                applies under, tried top to bottom with the first match winning.
                So the specific rules go above the general one, and a rule with no
                condition at the bottom is the default.
                Collapsed by default — a list of conditions is not what somebody
                opening this window usually came for. */}
            {big === 'api_params' && (
              <div className="api-rules">
                <button type="button" className="api-rules-head"
                        onClick={() => setRulesOpen((o) => !o)}>
                  <span>{rulesOpen ? '−' : '+'}</span>
                  Conditional calls
                  {rules.length > 0 && <em>{rules.length}</em>}
                  <span className="api-rules-hint">
                    e.g. trade_type=scalper → a different call
                  </span>
                </button>

                {rulesOpen && (
                  <div className="api-rules-body">
                    {rules.map((r, i) => (
                      <div className="api-rule" key={i}>
                        <input
                          className="input api-rule-when"
                          value={r.when || ''}
                          placeholder="trade_type=scalper   (empty = default)"
                          onChange={(e) => setRules(rules.map((x, j) =>
                            (j === i ? { ...x, when: e.target.value } : x)))}
                        />
                        <ParamsField
                          className="api-rule-params"
                          value={r.params || ''}
                          placeholder="symbol={{symbol}}&timeframe=M1"
                          onChange={(v) => setRules(rules.map((x, j) =>
                            (j === i ? { ...x, params: v } : x)))}
                        />
                        <button type="button" className="var-del" title="Remove"
                                onClick={() => setRules(rules.filter((_, j) => j !== i))}>
                          <X size={13} />
                        </button>
                      </div>
                    ))}
                    <button type="button" className="btn btn--sm var-add"
                            onClick={() => setRules([...rules, { when: '', params: '' }])}>
                      <Plus size={13} strokeWidth={2} /> Add a condition
                    </button>
                    <p className="api-doc-note">
                      Tried top to bottom, first match wins. Use {'{{name}}'} for anything
                      the trigger declared. Leave the condition empty for a default.
                    </p>
                  </div>
                )}
              </div>
            )}

            {/* Blank is never the answer. A node with nothing documented says so,
                and names itself, rather than leaving an empty modal that looks
                identical to a bug — which is exactly what it was. */}
            {big === 'api_params' && apiDoc.length === 0 && (
              <div className="api-doc">
                <span className="api-doc-title">Parameters</span>
                <p className="api-doc-note">
                  This node ({node.data.kind || 'unknown'}) publishes no parameter list.
                  Anything you put above is still sent as-is.
                </p>
              </div>
            )}

            {big === 'api_params' && apiDoc.length > 0 && (
              <div className="api-doc">
                <span className="api-doc-title">Everything this node accepts</span>
                {apiDoc.map((d) => (
                  <div className="api-doc-row" key={d.key}>
                    {/* The key alone, for when the value you want is not one of
                        the listed ones. */}
                    <code className="api-doc-key" draggable
                          onDragStart={(e) => drag(e, `${d.key}=`)}
                          title={`Drag ${d.key}= into the field`}>{d.key}</code>
                    <div className="api-doc-vals">
                      {(d.values || []).map((v) => (
                        <button type="button" className="pill api-doc-val" key={v}
                                draggable
                                onDragStart={(e) => drag(e, `${d.key}=${v}`)}
                                title={`Drag in, or click to add ${d.key}=${v}`}
                                onClick={() => addParam(d.key, v)}>{v}</button>
                      ))}
                      {d.note && <span className="api-doc-note">{d.note}</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}

              </div>

              {(tryOut?.result || tryOut?.error) && (
                <div className="ptm-right">
                  <span className="ptm-right-label">What came back</span>
                  {tryOut.error
                    ? <p className="ins-warn try-err"><AlertTriangle size={14} /><span>{tryOut.error}</span></p>
                    : <pre className="try-out">{tryOut.result}
                        {tryOut.truncated ? '\n\n… trimmed. The point here is the shape, not every row.' : ''}
                      </pre>}
                </div>
              )}
            </div>

            <div className="modal-actions">
              <button className="btn btn--primary" onClick={() => setBig(null)}>Done</button>
            </div>
          </div>
        </div>
      )}
    </aside>
  )
}
