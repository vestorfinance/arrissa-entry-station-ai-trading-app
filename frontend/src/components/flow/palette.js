import * as Icons from 'lucide-react'
import {
  Terminal,
  Zap, GitBranch, CornerUpLeft, CandlestickChart, Wand2, Clock,
  Target, Workflow, Repeat, Radar, Puzzle, Network,
} from 'lucide-react'

// Nodes the user can drop onto an agent's canvas.
//
// CORE nodes only. Every node an installed module adds — bond yields, news,
// sentiment, the calendar, HMR — arrives from /api/modules/palette and is
// merged in at runtime by `setModulePalette`. It used to be listed here too,
// which meant the canvas offered a node whose module might not be installed,
// and kept offering it after one was switched off.
//
// `args` are values the node needs; `configurable` args are filled in by the
// user in the node settings, the rest arrive at runtime.
// The field that lets a node STATE its API call instead of paying a model to
// guess it. `_params` on the server short-circuits on it for every data node —
// core and module alike — so the field belongs to all of them, and adding it
// here rather than in eleven module manifests means a module published tomorrow
// gets it without knowing it exists.
export const API_PARAMS_ARG = {
  name: 'api_params',
  type: 'text',
  label: 'API parameters (optional)',
  placeholder: 'symbol=XAUUSD&count=15&timeframe=M15',
  hint: 'State the call and the model is not asked to work it out — the node just '
      + 'fetches. Leave it empty and it reads your instruction as before.',
}

export const PALETTE = [
  {
    key: 'trigger-agent-call',
    type: 'trigger',
    Icon: Zap,
    label: 'Trigger',
    sub: 'When called by another agent',
    // `requirement` describes what input this agent expects — it becomes part of
    // the tool description the main chat agent sees.
    args: [{ name: 'requirement', type: 'text', required: false }],
    configurable: true,
    tone: 'trigger',
  },
  {
    key: 'trigger-interval',
    type: 'triggerInterval',
    Icon: Repeat,
    label: 'Trigger on Intervals',
    sub: 'Run this agent on a schedule',
    // The whole schedule is edited in one custom panel rather than as loose
    // args, because "every 15 minutes" is one decision, not four fields:
    //   mode  every | cron
    //   every + unit   seconds | minutes | hours | days
    //   cron + cron_brief   the expression, and the sentence it was written from
    //   text   what to analyse when the clock fires (the run's request)
    args: [],
    configurable: true,
    tone: 'schedule',
  },
  {
    key: 'artificial-sentiment',
    type: 'artificialSentiment',
    Icon: Radar,
    label: 'Artificial Sentiment',
    sub: 'Positioning read from the candles',
    args: [{ name: 'text', type: 'text', required: true },
      API_PARAMS_ARG,
    ],
    configurable: true,
    model: true,
    tone: 'artificial',
  },
  {
    // The other half of the same idea. A dedicated node has its endpoint decided
    // already and only wants parameters; this one wants both, for the endpoints
    // no node covers. Neither consults a model unless you ask it to.
    key: 'api-request',
    type: 'apiRequest',
    Icon: Terminal,
    label: 'API request',
    sub: 'Fetch from any endpoint, no model used',
    args: [
      { name: 'api_url', type: 'text', required: true, label: 'Endpoint',
        placeholder: '/api/market/chart' },
      { name: 'api_params', type: 'text', label: 'Parameters',
        placeholder: 'symbol=XAUUSD&count=15&timeframe=M15' },
      { name: 'text', type: 'text', label: 'Note (optional)',
        hint: 'Only read if you turn this node’s opinion on. No key is needed — the '
            + 'request runs as you.' },
    ],
    configurable: true,
    model: true,
    tone: 'market',
  },
  {
    key: 'market-data',
    type: 'marketData',
    Icon: CandlestickChart,
    label: 'Market Data',
    sub: 'Read live prices and candles',
    args: [{ name: 'text', type: 'text', required: true },
      API_PARAMS_ARG,
    ],
    configurable: true,
    model: true,
    tone: 'market',
  },
  {
    key: 'risk-management',
    type: 'riskManagement',
    Icon: Target,
    label: 'Risk Management',
    sub: 'Smart SL/TP + position sizing',
    args: [{ name: 'text', type: 'text', required: true },
      API_PARAMS_ARG,
    ],
    configurable: true,
    model: true,
    tone: 'risk',
  },
  {
    key: 'time-session',
    type: 'timeSession',
    Icon: Clock,
    label: 'Time & Session',
    sub: 'Current time + open trading sessions',
    args: [{ name: 'text', type: 'text', required: false },
      API_PARAMS_ARG,
    ],
    configurable: true,
    tone: 'time',
  },
  {
    key: 'if',
    type: 'if',
    Icon: GitBranch,
    label: 'If',
    sub: 'Branch the flow on a condition',
    args: [{ name: 'text', type: 'text', required: true }],
    configurable: true,
    tone: 'if',
    outputs: [{ id: 'true', label: 'true' }, { id: 'false', label: 'false' }],
  },
  {
    key: 'respond',
    type: 'respond',
    Icon: CornerUpLeft,
    label: 'Respond back',
    sub: 'Return a response to the calling agent',
    args: [{ name: 'text', type: 'text', required: true }],
    configurable: true,
    model: true,
    tone: 'respond',
  },
  {
    key: 'versatile',
    type: 'versatile',
    Icon: Wand2,
    label: 'Versatile',
    sub: 'Name it yourself and describe what it does',
    // `name` and `description` retitle the node on the canvas; `text` is its instruction.
    args: [
      { name: 'name', type: 'string', required: true },
      { name: 'description', type: 'string', required: false },
      { name: 'text', type: 'text', required: true },
    ],
    configurable: true,
    model: true,
    tone: 'versatile',
  },
  {
    key: 'call-agent',
    type: 'callAgent',
    Icon: Workflow,
    label: 'Call another agent',
    sub: 'Run another agent and use its response',
    // `agent_id` is the agent to call (chosen from a dropdown); `text` is an
    // optional instruction to send it. The reasoning so far is passed too when
    // chain of thought is on; the called agent's response flows to the next node.
    args: [
      { name: 'agent_id', type: 'agent', required: true },
      { name: 'text', type: 'text', required: false },
    ],
    configurable: true,
    tone: 'call',
  },
  {
    key: 'octo-agent',
    type: 'octoAgent',
    Icon: Network,
    label: 'Octo Agent',
    sub: 'Give it tools; it decides which to use',
    // One arg, and it is a sentence. Everything else this node does is decided
    // at run time from the tentacles wired to its bottom handle — which is the
    // point: the author says what is AVAILABLE, not what happens.
    args: [{ name: 'text', type: 'text', required: true }],
    configurable: true,
    model: true,
    tone: 'octo',
  },
]

// ── module nodes, merged at runtime ──────────────────────────────────────────
// A module ships an icon NAME, not a component — it cannot ship React into a
// bundle that was built without it — so the name is resolved against lucide
// here, with a sensible fallback for a name that does not exist.
let MODULE_PALETTE = []

export function setModulePalette(entries) {
  MODULE_PALETTE = (entries || []).map((e) => ({
    ...e,
    Icon: Icons[e.icon] || Puzzle,
    configurable: e.configurable !== false,
    // Every module data node reaches the same `_params` on the server, so every
    // one of them can be told its call outright. The field is added here rather
    // than asked of each module, so News, Bond Yields, Fed Watch, Sentiment, the
    // calendar and anything published later all get it at once.
    args: (e.args || []).some((a) => (a.name || a) === 'api_params')
      ? e.args
      : [...(e.args || []), API_PARAMS_ARG],
  }))
}

/** Core nodes plus whatever the installed modules currently add. */
export const fullPalette = () => [...PALETTE, ...MODULE_PALETTE]

export const paletteItem = (key) => fullPalette().find((p) => p.key === key)
