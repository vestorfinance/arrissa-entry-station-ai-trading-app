import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import ReactFlow, {
  Background, BackgroundVariant, Controls, MiniMap, MarkerType,
  addEdge, updateEdge, useNodesState, useEdgesState,
} from 'reactflow'
import 'reactflow/dist/style.css'
import { ArrowLeft, Bot, Plus, AlertTriangle, Play, Download, History, ChevronLeft, X, Globe, Lock, Repeat } from 'lucide-react'
import DashboardLayout from '../components/DashboardLayout.jsx'
import CopyId from '../components/CopyId.jsx'
import TriggerNode from '../components/flow/TriggerNode.jsx'
import ActionNode from '../components/flow/ActionNode.jsx'
import OctoNode from '../components/flow/OctoNode.jsx'
import DeletableEdge from '../components/flow/DeletableEdge.jsx'
import NodeSettings from '../components/flow/NodeSettings.jsx'
import { TraceView, mdHtml } from '../components/ToolResult.jsx'
import AgentBuilderChat from '../components/flow/AgentBuilderChat.jsx'
import { fullPalette, paletteItem, setModulePalette } from '../components/flow/palette.js'
import * as moduleBus from '../services/moduleBus.js'
import * as store from '../services/agents.js'
import { backdrop } from '../services/backdrop.js'
import * as api from '../services/api.js'

// Core node types. A module's node type is not listed here — it cannot be, the
// bundle predates the module — so `nodeTypesFor` adds each one as an ActionNode,
// which is what every data node already is.
const BASE_NODE_TYPES = {
  trigger: TriggerNode,
  triggerInterval: TriggerNode,   // same entry-point shape, a clock instead of a caller
  octoAgent: OctoNode,            // a body with a second, downward handle for its tentacles
  artificialSentiment: ActionNode,
  marketData: ActionNode,
  riskManagement: ActionNode,
  timeSession: ActionNode,
  if: ActionNode,
  respond: ActionNode,
  versatile: ActionNode,
  callAgent: ActionNode,
}

// The fallbacks go FIRST and the core map LAST, because the last spread wins.
// The other way round — which is how this was written — every core type was
// silently replaced by ActionNode the moment it also appeared in the palette,
// so a node with its own component (the Octo body, the triggers) rendered as a
// plain action node with none of its handles.
const nodeTypesFor = (palette) => ({
  ...Object.fromEntries(palette.map((p) => [p.type, ActionNode])),
  ...BASE_NODE_TYPES,
})
const edgeTypes = { connector: DeletableEdge }

// LLM cost: DeepSeek runs cost fractions of a cent — show enough precision.
function fmtCost(c) {
  if (c === null || c === undefined) return null
  const n = Number(c)
  if (!n) return '$0'
  if (n < 0.01) return '$' + n.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')
  return '$' + n.toFixed(4)
}
// "in 12m" / "in 3h" / "overdue" — how long until a scheduled agent next runs.
function untilText(iso) {
  if (!iso) return null
  const ms = new Date(iso).getTime() - Date.now()
  if (!Number.isFinite(ms)) return null
  if (ms <= 0) return 'due now'
  const m = Math.round(ms / 60000)
  if (m < 1) return 'in under a minute'
  if (m < 60) return `in ${m}m`
  const h = Math.round(m / 60)
  return h < 48 ? `in ${h}h` : `in ${Math.round(h / 24)}d`
}

// Build a "1,234 in · 567 out · ~$0.0004 (model)" usage line from a run/summary.
function usageLine({ tin, tout, cache, cost, model, calls }) {
  if (tin == null && tout == null) return null
  const parts = [`${(tin || 0).toLocaleString()} in`, `${(tout || 0).toLocaleString()} out`]
  if (cache) parts.push(`${cache.toLocaleString()} cached`)
  if (calls) parts.push(`${calls} calls`)
  let s = parts.join(' · ')
  const c = fmtCost(cost)
  if (c) s += ` · ~${c}`
  if (model) s += ` · ${model}`
  return s
}

export default function AnalysisAgentFlow() {
  const { id } = useParams()
  const navigate = useNavigate()
  // The palette is core's list plus whatever the installed modules add right
  // now, refetched when a module is switched on or off so the canvas never
  // offers a node whose module has gone.
  const [palette, setPalette] = useState(fullPalette())
  useEffect(() => {
    const load = () => api.modulePalette()
      .then((r) => {
        setModulePalette(r.nodes || [])
        setPalette(fullPalette())
      })
      .catch(() => {})
    load()
    return moduleBus.onChanged(load)
  }, [])
  const nodeTypes = useMemo(() => nodeTypesFor(palette), [palette])
  const [agent, setAgent] = useState(null)
  const [admin, setAdmin] = useState(false)
  // The operator of this instance — a Community owner or a cloud admin.
  // Distinct from `admin`, which is a cloud console flag and false by
  // design in Community; the usage numbers belong to whoever pays the
  // model bills, and on Community that is the person at the keyboard.
  const [operator, setOperator] = useState(false)
  const [nodes, setNodes, onNodesChange] = useNodesState([])

  // Re-read every node from the palette whenever the palette changes.
  //
  // The agent and the module palette load in parallel and the agent usually
  // wins, so `paletteItem('news')` found nothing at load time and the node kept
  // whatever the saved flow held. Label and sub survived that because they are
  // saved WITH the flow; anything that exists only in the palette — the args,
  // the API documentation — silently did not, and a module node ended up with no
  // documented parameters and no error to explain why.
  //
  // Its own effect rather than a line inside the fetch, so it also runs when a
  // module is switched on or off, and so it cannot reference setNodes before the
  // line that creates it.
  useEffect(() => {
    setNodes((ns) => ns.map((n) => {
      const item = paletteItem(n.data?.kind)
      return item
        ? { ...n, data: { ...n.data, label: item.label, sub: item.sub, args: item.args,
                          apiKeys: item.apiKeys || item.api_keys,
                          apiExample: item.apiExample || item.api_example,
                          apiDoc: item.apiDoc || item.api_doc } }
        : n
    }))
  }, [palette, setNodes])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [cot, setCot] = useState(false)   // chain-of-thought: feed each node's read into the next
  const [selectedId, setSelectedId] = useState(null)
  const [confirmId, setConfirmId] = useState(null)   // node pending delete confirmation
  const [models, setModels] = useState([])           // AI models enabled in Settings
  const [defaultModel, setDefaultModel] = useState('')   // what a node inherits when unset
  const [agents, setAgents] = useState([])            // the user's OTHER agents (for the call-agent node)
  const [testOpen, setTestOpen] = useState(false)
  const [testReq, setTestReq] = useState('')
  // Values for whatever the Trigger declares. Without these a test of an agent
  // that requires a symbol is refused before it reaches a node — a correct
  // refusal and a useless way to try a flow out.
  const [testVars, setTestVars] = useState({})
  const [testBusy, setTestBusy] = useState(false)
  const [testSecs, setTestSecs] = useState(0)
  const [testRes, setTestRes] = useState(null)
  const [progress, setProgress] = useState([])   // live node/tool lines while a test runs
  const [histOpen, setHistOpen] = useState(false)
  const [runs, setRuns] = useState(null)             // list of run summaries
  const [activeRun, setActiveRun] = useState(null)   // full trace of the selected run
  const [runBusy, setRunBusy] = useState(false)
  const [sched, setSched] = useState(null)           // live state of an interval trigger
  const seq = useRef(0)

  // A real flow talks to a model once per node and again to plan — 90 seconds is
  // an ordinary run, not a stuck one. Without a clock ticking, "Running…" is
  // indistinguishable from a hang, which is exactly how it was read.
  async function runTest() {
    setTestBusy(true); setTestRes(null); setTestSecs(0); setProgress([])
    const started = Date.now()
    const tick = setInterval(() => setTestSecs(Math.round((Date.now() - started) / 1000)), 1000)
    try {
      // Each event either opens a new line or completes the one it belongs to,
      // so the panel reads as a list of things done rather than a scroll of
      // noise — and the line still running is the one without a duration.
      setTestRes(await api.testRunAnalysisAgentStream(agent.id, { request: testReq, variables: testVars }, (ev) => {
        if (ev.type !== 'node' && ev.type !== 'tool') return
        setProgress((prev) => {
          if (ev.phase === 'start') {
            return [...prev, { key: `${ev.type}-${ev.node || ev.name}-${prev.length}`,
                               type: ev.type, kind: ev.kind, name: ev.name, asked: ev.asked }]
          }
          // Complete the last still-open line of the same shape.
          const i = prev.map((p) => p.done).lastIndexOf(undefined)
          if (i < 0) return prev
          const next = [...prev]
          next[i] = { ...next[i], done: true, ms: ev.ms,
                      failed: !!(ev.error || ev.failed), detail: ev.error || ev.summary,
                      usage: ev.usage, cost: ev.cost }
          return next
        })
      }))
    } catch (e) {
      setTestRes({ error: e.message })
    } finally {
      clearInterval(tick)
      setTestBusy(false)
      // Close whatever was still in flight. A run that dies mid-node leaves that
      // line open, and an open line spins for ever — so the failure looked like
      // a hang, and the one useful fact, WHICH node it died on, was the thing
      // being hidden.
      setProgress((prev) => prev.map((p) => (p.done ? p : {
        ...p, done: true, failed: true, ms: 0,
        detail: p.detail || 'stopped here — the run ended before this step finished',
      })))
    }
  }

  async function openHistory() {
    setHistOpen(true); setActiveRun(null); setRuns(null)
    try { setRuns((await api.listAnalysisRuns(agent.id)).runs || []) }
    catch (e) { setRuns([]); }
  }
  async function pickRun(id) {
    setRunBusy(true)
    try { setActiveRun(await api.getAnalysisRun(agent.id, id)) }
    catch (e) { setActiveRun({ error: e.message }) }
    finally { setRunBusy(false) }
  }

  const loaded = useRef(false)          // gate autosave until the graph is loaded
  useEffect(() => {
    let cancelled = false
    loaded.current = false
    store.getAgent(id).then((a) => {
      if (cancelled) return
      setAgent(a)
      // Re-read label/sub/args from the palette so saved flows pick up copy changes.
      setNodes((a.flow?.nodes || []).map((n) => {
        const item = paletteItem(n.data?.kind)
        return item ? { ...n, data: { ...n.data, label: item.label, sub: item.sub, args: item.args, apiKeys: item.apiKeys || item.api_keys, apiExample: item.apiExample || item.api_example, apiDoc: item.apiDoc || item.api_doc, } } : n
      }))
      setEdges(a.flow?.edges || [])
      setCot(!!a.flow?.cot)
      seq.current = (a.flow?.nodes || []).length
      loaded.current = true
    }).catch(() => { if (!cancelled) navigate('/analysis-agents', { replace: true }) })
    return () => { cancelled = true }
  }, [id, navigate, setNodes, setEdges])

  // Same model list the chat uses — whatever is enabled in Settings.
  // `models`, not `selected`: /api/ai/config has never returned a `selected`
  // key, so every node's model picker was permanently empty and said there were
  // none to add. Filtered to available, exactly as the chat composer does.
  useEffect(() => {
    api.getAIConfig()
      .then((r) => setModels((r.models || []).filter((m) => m.available)))
      .catch(() => setModels([]))
    // What "the agent default" actually is, so a node can name it instead of
    // making you go and look it up.
    api.getPrefs()
      .then((p) => setDefaultModel(p.analysis_model || p.chat_model || ''))
      .catch(() => {})
  }, [])

  // only an admin sees the public/private switch
  useEffect(() => {
    api.me().then((p) => { setAdmin(!!p?.admin); setOperator(!!p?.capabilities?.owner) })
      .catch(() => {})
  }, [])

  // The user's other agents — offered by the "Call another agent" node (never itself).
  useEffect(() => {
    store.listAgents()
      .then((list) => setAgents((list || []).filter((a) => String(a.id) !== String(id))))
      .catch(() => setAgents([]))
  }, [id])

  // The schedule, from the server rather than from the canvas: the canvas knows
  // what was ASKED for, only the server knows when it last actually ran and when
  // it is next due. Re-read after each save (the interval may have changed) and
  // once a minute while the page is open, so "next run" stays true.
  const refreshSchedule = useCallback(() => {
    store.getSchedule(id).then((r) => setSched(r.schedule || null)).catch(() => {})
  }, [id])
  useEffect(() => {
    refreshSchedule()
    const t = setInterval(refreshSchedule, 60000)
    return () => clearInterval(t)
  }, [refreshSchedule])

  // ── connecting / disconnecting ──────────────────────────
  const onConnect = useCallback((c) => {
    setEdges((eds) => addEdge({ ...c, type: 'connector' }, eds))
  }, [setEdges])

  const deleteEdge = useCallback((edgeId) => {
    setEdges((eds) => eds.filter((e) => e.id !== edgeId))
  }, [setEdges])

  // Dragging an endpoint off a handle and dropping it on empty canvas
  // disconnects; dropping it on another handle rewires the connector.
  const updateOk = useRef(true)
  const onEdgeUpdateStart = useCallback(() => { updateOk.current = false }, [])
  const onEdgeUpdate = useCallback((oldEdge, conn) => {
    updateOk.current = true
    setEdges((eds) => updateEdge(oldEdge, conn, eds))
  }, [setEdges])
  const onEdgeUpdateEnd = useCallback((_, edge) => {
    if (!updateOk.current) deleteEdge(edge.id)
    updateOk.current = true
  }, [deleteEdge])

  // Persist the graph to the backend (debounced) so a reopened agent keeps its
  // layout AND the server-side chat agent can load it as a tool.
  const [saveState, setSaveState] = useState('saved')   // saved | saving | error
  useEffect(() => {
    if (!agent || !loaded.current) return
    setSaveState('saving')
    const t = setTimeout(() => {
      store.saveFlow(agent.id, { nodes, edges, cot })
        .then(() => { setSaveState('saved'); refreshSchedule() })
        .catch(() => setSaveState('error'))
    }, 700)
    return () => clearTimeout(t)
  }, [agent, nodes, edges, cot, refreshSchedule])

  async function exportAgent() {
    if (!agent) return
    const payload = { arrissa_analysis_agent: 1, name: agent.name,
                      description: agent.description, flow: { nodes, edges } }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${(agent.name || 'agent').replace(/[^a-z0-9]+/gi, '-').toLowerCase()}.json`
    link.click()
    URL.revokeObjectURL(url)
  }

  // admin only: public means every user can RUN this agent; only its owner edits it
  async function togglePublic() {
    if (!agent) return
    try {
      const updated = await store.setPublic(agent.id, !agent.public)
      setAgent((a) => ({ ...a, public: updated.public }))
    } catch (e) { alert(e.message) }
  }

  async function toggleStatus() {
    if (!agent) return
    const next = agent.status === 'active' ? 'paused' : 'active'
    try {
      const updated = await store.setStatus(agent.id, next)
      setAgent((a) => ({ ...a, status: updated.status }))
    } catch { /* ignore */ }
  }

  // Apply a flow the AI builder produced: normalise nodes through the palette
  // (same as load) so labels/args are current, drop them on the canvas, and pick
  // up any suggested name/description. Autosave then persists it.
  const applyBuilt = useCallback((res) => {
    const flow = res?.flow
    if (!flow || !Array.isArray(flow.nodes)) return
    const built = flow.nodes.map((n) => {
      const item = paletteItem(n.data?.kind)
      return item
        ? { ...n, data: { ...n.data, label: item.label, sub: item.sub, args: item.args, apiKeys: item.apiKeys || item.api_keys, apiExample: item.apiExample || item.api_example, apiDoc: item.apiDoc || item.api_doc, values: n.data?.values || {} } }
        : n
    })
    setNodes(built)
    setEdges(flow.edges || [])
    seq.current = built.length
    setSelectedId(null)
    if (res.name || res.description) {
      setAgent((a) => ({ ...a, name: res.name || a.name, description: res.description || a.description }))
      store.rename(agent.id, {
        ...(res.name ? { name: res.name } : {}),
        ...(res.description ? { description: res.description } : {}),
      }).catch(() => {})
    }
  }, [agent, setNodes, setEdges])

  const addNode = useCallback((key) => {
    const item = paletteItem(key)
    if (!item) return
    const i = seq.current++
    const id = `${item.type}_${Date.now().toString(36)}${i}`
    setNodes((ns) => ns.concat({
      id,
      type: item.type,
      position: { x: 120 + (i % 4) * 40, y: 90 + i * 70 },
      data: { kind: item.key, label: item.label, sub: item.sub, args: item.args, apiKeys: item.apiKeys || item.api_keys, apiExample: item.apiExample || item.api_example, apiDoc: item.apiDoc || item.api_doc, values: {} },
    }))
    if (item.configurable) setSelectedId(id)
  }, [setNodes])

  // Deleting a node is confirmed first — it takes its connectors with it.
  const deleteNode = useCallback((nodeId) => {
    setNodes((ns) => ns.filter((n) => n.id !== nodeId))
    setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId))
    setSelectedId((cur) => (cur === nodeId ? null : cur))
    setConfirmId(null)
  }, [setNodes, setEdges])

  // Duplicate drops a copy — same settings, offset a little — next to the original.
  const duplicateNode = useCallback((nodeId) => {
    setNodes((ns) => {
      const src = ns.find((n) => n.id === nodeId)
      if (!src) return ns
      const copy = {
        ...src,
        id: `${src.type}_${Date.now().toString(36)}${seq.current++}`,
        position: { x: src.position.x + 40, y: src.position.y + 60 },
        selected: false,
        data: { ...src.data, values: { ...(src.data.values || {}) } },
      }
      return ns.concat(copy)
    })
  }, [setNodes])

  // Node settings write straight back into the node's data.values.
  const setValues = useCallback((nodeId, values) => {
    setNodes((ns) => ns.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, values } } : n)))
  }, [setNodes])

  const selected = nodes.find((n) => n.id === selectedId) || null

  // What the Trigger says this agent needs, read from the canvas rather than
  // from the server, so it updates the moment a variable is added.
  const declaredVars = useMemo(() => {
    const t = nodes.find((n) => ['trigger-agent-call', 'trigger', 'trigger-interval',
                                'triggerInterval'].includes(n.data?.kind))
    return ((t?.data?.values || {}).vars || []).filter((v) => (v.key || '').trim())
  }, [nodes])

  const defaultEdgeOptions = useMemo(() => ({
    type: 'connector',
    animated: true,
    markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: '#a0a0a0' },
  }), [])

  // Delete buttons live inside the node/edge, so hand each one the callback.
  // The type is re-resolved from the palette so a saved flow can never fall back
  // to React Flow's built-in (white) default node.
  const shownNodes = useMemo(
    () => nodes.map((n) => ({
      ...n,
      type: paletteItem(n.data?.kind)?.type || n.type,
      data: { ...n.data, onDelete: setConfirmId, onDuplicate: duplicateNode },
    })),
    [nodes, duplicateNode],
  )

  const pending = nodes.find((n) => n.id === confirmId) || null

  const shownEdges = useMemo(
    () => edges.map((e) => ({ ...e, type: e.type || 'connector', data: { ...e.data, onDelete: deleteEdge } })),
    [edges, deleteEdge],
  )

  if (!agent) return null

  return (
    <DashboardLayout title={agent.name} flush hideLive>
      <div className="flow-page">
        <div className="flow-bar">
          <button className="btn btn--ghost btn--icon" title="Back" onClick={() => navigate('/analysis-agents')}>
            <ArrowLeft size={16} strokeWidth={1.75} />
          </button>
          <span className="agent-avatar"><Bot size={16} strokeWidth={1.75} /></span>
          {/* Name and ID only. The description belongs to the agent list, where it
              is being chosen; here it is already chosen, and a paragraph of prose
              was the one thing in this bar with no fixed size. It stays on the
              title attribute for anyone who wants it. */}
          <div className="flow-bar-main" title={agent.description || ''}>
            <span className="flow-bar-name">{agent.name}</span>
            {/* the id the Analysis API takes as analysis_agent_id */}
            <CopyId value={agent.id} label="ID" title="Copy agent ID" />
          </div>
          <div className="flow-bar-actions">
            <label className="cot-toggle" title="Chain of thought: each node's read feeds into the next, so nodes build on one another instead of analysing in isolation.">
              <input type="checkbox" checked={cot} onChange={(e) => setCot(e.target.checked)} />
              <span>Chain of thought</span>
            </label>
            <span className="muted flow-save-state">
              {saveState === 'saving' ? 'Saving…' : saveState === 'error' ? 'Save failed' : 'Saved'}
            </span>
            {/* A schedule that is written down but not running is the one thing a
                user cannot see from the canvas, so the pill says which it is. */}
            {sched && (
              <span className={'pill pill--switch ' + (sched.error ? 'pill--warn'
                                : sched.running ? 'pill--ok' : 'pill--muted')}
                    title={[
                      sched.label && `Schedule: ${sched.label}`,
                      sched.error && `Not running: ${sched.error}`,
                      !sched.running && !sched.error && 'Only active agents run — activate it to start the schedule.',
                      sched.last_run_at && `Last run ${new Date(sched.last_run_at).toLocaleString()} · ${sched.last_status}`,
                      sched.last_error && `Last error: ${sched.last_error}`,
                      sched.runs ? `${sched.runs} run(s) so far` : null,
                    ].filter(Boolean).join('\n')}>
                <Repeat size={13} strokeWidth={2} />
                {sched.error ? 'schedule invalid'
                  : !sched.running ? 'schedule paused'
                  : (untilText(sched.next_run_at) || sched.label || 'scheduled')}
              </span>
            )}
            <button className="btn btn--ghost" onClick={exportAgent} title="Download this agent as JSON">
              <Download size={14} strokeWidth={2} /> <span className="btn-label">Export</span>
            </button>
            <button className="btn btn--ghost" onClick={openHistory} title="Execution history">
              <History size={14} strokeWidth={2} /> <span className="btn-label">History</span>
            </button>
            <button className="btn btn--ghost" onClick={() => { setTestRes(null); setTestOpen(true) }} title="Test run this agent">
              <Play size={14} strokeWidth={2} /> <span className="btn-label">Test</span>
            </button>
            <button className={'pill ' + (agent.status === 'active' ? 'pill--ok' : 'pill--muted')}
                    onClick={toggleStatus} style={{ cursor: 'pointer', border: 'none' }}
                    title={agent.status === 'active'
                      ? 'Active — callable by the chat agent. Click to pause.'
                      : 'Click to activate so the chat agent can call it.'}>
              {agent.status}
            </button>
            {admin && agent.mine !== false ? (
              <button className={'pill pill--switch ' + (agent.public ? 'pill--ok' : 'pill--muted')}
                      onClick={togglePublic} style={{ cursor: 'pointer', border: 'none' }}
                      title={agent.public
                        ? 'Public — every user can run this agent. Click to make it private.'
                        : 'Private — only you can run it. Click to make it public.'}>
                {agent.public ? <Globe size={13} strokeWidth={2} /> : <Lock size={13} strokeWidth={2} />}
                {agent.public ? 'Public' : 'Private'}
              </button>
            ) : agent.public ? (
              <span className="pill pill--ok pill--switch" title="Public — you can run it, only its owner edits it">
                <Globe size={13} strokeWidth={2} /> Public
              </span>
            ) : null}
          </div>
        </div>

        <div className="flow-work">
          <aside className="flow-palette">
            <div className="flow-palette-label">Nodes</div>
            {palette.map(({ key, Icon, label, sub, args, tone }) => (
              <button key={key} className="palette-item" onClick={() => addNode(key)} title={`Add ${label}`}>
                <span className="palette-item-top">
                  <span className={`fnode-icon fnode-icon--${tone}`}><Icon size={16} strokeWidth={1.75} /></span>
                  <span className="palette-item-top-main">
                    <span className="palette-item-title">{label}</span>
                    <span className="palette-item-sub">{sub}</span>
                  </span>
                </span>
                <span className="palette-item-foot">
                  <span className="palette-item-args">
                    {args.map((a) => (
                      <span className="pill palette-arg" key={a.name}>{a.name}: {a.type}</span>
                    ))}
                  </span>
                  <Plus className="palette-item-add" size={15} strokeWidth={2} />
                </span>
              </button>
            ))}
          </aside>

          <div className="flow-canvas">
            <ReactFlow
              nodes={shownNodes}
              edges={shownEdges}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onEdgeUpdate={onEdgeUpdate}
              onEdgeUpdateStart={onEdgeUpdateStart}
              onEdgeUpdateEnd={onEdgeUpdateEnd}
              edgesUpdatable
              onNodeClick={(_, n) => setSelectedId(n.id)}
              onPaneClick={() => setSelectedId(null)}
              defaultEdgeOptions={defaultEdgeOptions}
              connectionLineStyle={{ stroke: '#6366f1', strokeWidth: 2 }}
              deleteKeyCode={null}
              fitView
              proOptions={{ hideAttribution: true }}
            >
              <Background variant={BackgroundVariant.Dots} gap={22} size={1.5} color="#2d2d2d" />
              <Controls showInteractive={false} />
              <MiniMap pannable zoomable maskColor="rgba(0,0,0,0.6)" nodeColor="#4f46e5" />
            </ReactFlow>

            {nodes.length === 0 && (
              <div className="flow-empty">
                <div className="flow-empty-title">Empty flow</div>
                <p className="flow-empty-sub">Pick a node on the left to drop it on the canvas.</p>
              </div>
            )}
          </div>

          {selected && (
            <NodeSettings node={selected} models={models} agents={agents} defaultModel={defaultModel} onChange={setValues}
                          /* Declared on the TRIGGER, needed by every other node — so it is
                             passed down rather than each node hunting the canvas for it. */
                          variables={declaredVars}
                          onDelete={setConfirmId} onClose={() => setSelectedId(null)} />
          )}
        </div>
      </div>

      <AgentBuilderChat agentId={agent.id} onBuilt={applyBuilt} />

      {testOpen && (
        <div className="modal-overlay" {...backdrop(() => setTestOpen(false))}>
          <div className="modal modal--full" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <Play size={16} strokeWidth={1.9} />
              <span className="modal-title">Test run · {agent.name}</span>
              <button className="modal-x" onClick={() => setTestOpen(false)} title="Close"><X size={18} strokeWidth={2} /></button>
            </div>

            <div className="modal-scroll">
              <p className="card-sub" style={{ marginBottom: 12 }}>
                Runs the flow once from the Trigger, exactly as the chat agent would call it. Uses the
                model selected in your chat interface for the reasoning nodes. Every node consults the
                model, so a flow with several of them takes a minute or two — the timer shows it is
                still going.
              </p>
              {declaredVars.length > 0 && (
                <div className="field">
                  <span className="field-label">
                    Variables the Trigger declares
                    <span className="muted"> · a required one must have a value or the run refuses</span>
                  </span>
                  {declaredVars.map((v) => (
                    <div className="test-var" key={v.key}>
                      <code>{v.key}</code>
                      <input
                        className="input"
                        value={testVars[v.key] || ''}
                        placeholder={v.required ? 'required' : 'optional'}
                        onChange={(e) => setTestVars((m) => ({ ...m, [v.key]: e.target.value }))}
                      />
                      {v.required && !((testVars[v.key] || '').trim()) && (
                        <span className="test-var-need">needed</span>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <label className="field">
                <span className="field-label">Request (what to analyse)</span>
                <textarea className="node-settings-text" value={testReq} spellCheck={false}
                          placeholder="e.g. What's the outlook on gold right now?"
                          onChange={(e) => setTestReq(e.target.value)} />
              </label>

              {/* What is happening, while it happens. A two-minute run used to
                  show a spinner and nothing else, so a flow that was working and
                  one that was wedged looked identical — and when it did fail,
                  the failure arrived with no indication of where. Each line
                  opens when a node starts and closes with its duration; the one
                  without a duration is the one running now. */}
              {progress.length > 0 && (
                <div className="response" style={{ marginTop: 12 }}>
                  <div className="field-label" style={{ marginBottom: 8 }}>
                    {testBusy ? `Running — step ${progress.length}` : `Ran ${progress.length} step(s)`}
                  </div>
                  <div style={{ display: 'grid', gap: 4 }}>
                    {progress.map((p) => (
                      <div key={p.key} style={{
                        display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 12.5,
                        opacity: p.done ? 0.72 : 1,
                      }}>
                        <span style={{ width: 14, flex: 'none', textAlign: 'center' }}>
                          {/* A spinner on the step in flight. A static dot read
                              as stuck on a node that legitimately takes ninety
                              seconds, which is most of them. */}
                          {!p.done ? <span className="step-spin" /> : p.failed ? '×' : '✓'}
                        </span>
                        <span style={{ fontWeight: p.done ? 400 : 600 }}>
                          {p.name || p.kind}{p.type === 'tool' ? ' (tool)' : ''}
                        </span>
                        <span className="muted" style={{
                          flex: 1, minWidth: 0, overflow: 'hidden',
                          textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>
                          {p.failed ? p.detail : (p.asked || '')}
                        </span>
                        {/* What this step alone burned. A total tells you a run
                            was expensive; this tells you which node made it so. */}
                        {p.usage && (p.usage.in || p.usage.out) ? (
                          <span className="muted" style={{ flex: 'none', fontVariantNumeric: 'tabular-nums' }}>
                            {p.usage.calls ? `${p.usage.calls}× · ` : ''}
                            {(p.usage.in || 0).toLocaleString()} in · {(p.usage.out || 0).toLocaleString()} out
                            {p.cost ? ` · ${fmtCost(p.cost)}` : ''}
                          </span>
                        ) : null}
                        <span className="muted" style={{ flex: 'none', fontVariantNumeric: 'tabular-nums' }}>
                          {p.done ? `${(p.ms / 1000).toFixed(1)}s` : '…'}
                        </span>
                      </div>
                    ))}
                  </div>
                  {progress.some((p) => p.cost) && (
                    <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                      steps account for {fmtCost(progress.reduce((a, p) => a + (p.cost || 0), 0))}
                      {' · '}
                      {progress.reduce((a, p) => a + ((p.usage && p.usage.calls) || 0), 0)} calls
                    </div>
                  )}
                </div>
              )}

              {testRes && (
                <div className="response" style={{ marginTop: 12 }}>
                  {testRes.error && <div className="alert alert--danger">{testRes.error}</div>}
                  {testRes.llm_error && <div className="alert alert--danger">AI model error — {testRes.llm_error}. Every node fell back to defaults, so the analysis is incomplete.</div>}
                  {operator && testRes.used_model && <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>model: {testRes.used_model}</div>}
                  {/* Token counts, call counts and cost are operator numbers, not
                      product. They say how the sausage is made — how many calls a
                      run takes, what it costs us — and a user reading them starts
                      optimising against our margins instead of their trading. */}
                  {operator && testRes.usage && (
                    <div className="usage-pill">{usageLine({ tin: testRes.usage.in, tout: testRes.usage.out, cache: testRes.usage.cache_hit, calls: testRes.usage.calls, cost: testRes.cost_usd, model: testRes.usage_model })}</div>
                  )}
                  {testRes.response && <div className="dv-md msg-md" dangerouslySetInnerHTML={mdHtml(testRes.response)} />}
                  {Array.isArray(testRes.trace) && testRes.trace.length > 0 && (
                    <div style={{ marginTop: 12 }}>
                      <div className="field-label" style={{ marginBottom: 8 }}>Node reasoning ({testRes.trace.length} steps)</div>
                      <TraceView steps={testRes.trace} />
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="modal-actions">
              <button className="btn btn--ghost" onClick={() => setTestOpen(false)}>Close</button>
              <button className="btn btn--primary" onClick={runTest} disabled={testBusy}>
                <Play size={15} strokeWidth={2} />
                {testBusy ? `Running… ${testSecs}s` : 'Run'}
              </button>
            </div>
          </div>
        </div>
      )}

      {histOpen && (
        <div className="modal-overlay" {...backdrop(() => setHistOpen(false))}>
          <div className="modal modal--full" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              {activeRun ? (
                <button className="btn btn--ghost btn--icon btn--sm" onClick={() => setActiveRun(null)} title="Back to list">
                  <ChevronLeft size={16} strokeWidth={2} />
                </button>
              ) : <History size={16} strokeWidth={1.9} />}
              <span className="modal-title">{activeRun ? 'Run detail' : `Execution history · ${agent.name}`}</span>
              <button className="modal-x" onClick={() => setHistOpen(false)} title="Close"><X size={18} strokeWidth={2} /></button>
            </div>

            <div className="modal-scroll">
              {!activeRun && (
                <div className="run-list">
                  {runs === null && <p className="muted">Loading…</p>}
                  {runs && runs.length === 0 && <p className="muted">No runs yet. Test the agent or let the chat call it — every execution is recorded here.</p>}
                  {(runs || []).map((r) => (
                    <button key={r.id} className="run-row" onClick={() => pickRun(r.id)}>
                      <span className={`pill ${r.status === 'error' ? 'pill--warn' : 'pill--ok'}`}>{r.status}</span>
                      <span className="run-row-main">
                        <span className="run-row-req">{r.request || '(no input)'}</span>
                        <span className="run-row-meta">
                          {new Date(r.created_at).toLocaleString()} · {r.steps ?? 0} steps · {r.source}
                          {(r.tokens_in != null || r.tokens_out != null) &&
                            <> · {((r.tokens_in || 0) + (r.tokens_out || 0)).toLocaleString()} tok{fmtCost(r.cost_usd) ? ` · ~${fmtCost(r.cost_usd)}` : ''}</>}
                        </span>
                      </span>
                      <ChevronLeft size={15} className="run-row-go" />
                    </button>
                  ))}
                </div>
              )}

              {activeRun && (
                <div className="response">
                  {runBusy && <p className="muted">Loading…</p>}
                  {activeRun.error && <div className="alert alert--danger">{activeRun.error}</div>}
                  {activeRun.llm_error && <div className="alert alert--danger">AI model error — {activeRun.llm_error}</div>}
                  {operator && (activeRun.tokens_in != null || activeRun.tokens_out != null) && (
                    <div className="usage-pill">{usageLine({ tin: activeRun.tokens_in, tout: activeRun.tokens_out, cache: activeRun.tokens_cache_hit, calls: activeRun.llm_calls, cost: activeRun.cost_usd, model: activeRun.usage_model })}</div>
                  )}
                  {activeRun.request && <div className="run-detail-req"><span className="field-label">Input</span>{activeRun.request}</div>}
                  {activeRun.response && <div className="dv-md msg-md" style={{ marginTop: 12 }} dangerouslySetInnerHTML={mdHtml(activeRun.response)} />}
                  {Array.isArray(activeRun.trace) && activeRun.trace.length > 0 && (
                    <div style={{ marginTop: 14 }}>
                      <div className="field-label" style={{ marginBottom: 8 }}>Node reasoning ({activeRun.trace.length} steps)</div>
                      <TraceView steps={activeRun.trace} />
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="modal-actions">
              <button className="btn btn--ghost" onClick={() => setHistOpen(false)}>Close</button>
            </div>
          </div>
        </div>
      )}

      {pending && (
        <div className="modal-overlay" {...backdrop(() => setConfirmId(null))}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <AlertTriangle className="modal-warn-icon" size={18} strokeWidth={1.75} />
              <span className="modal-title">Delete node?</span>
            </div>
            <p className="modal-body">
              <strong>{pending.data.label}</strong> and any connectors attached to it will be removed
              from this flow. This can’t be undone.
            </p>
            <div className="modal-actions">
              <button className="btn btn--ghost" onClick={() => setConfirmId(null)}>Cancel</button>
              <button className="btn btn--danger" onClick={() => deleteNode(pending.id)}>Delete node</button>
            </div>
          </div>
        </div>
      )}
    </DashboardLayout>
  )
}
