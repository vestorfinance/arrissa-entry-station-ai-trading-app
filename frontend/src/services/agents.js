// Analysis-agent store — now backed by the API so the main chat agent (server
// side) can load each agent and expose it as a tool. All calls are async.
import * as api from './api.js'

export const listAgents = () => api.listAnalysisAgents()
export const getAgent = (id) => api.getAnalysisAgent(id)
export const createAgent = ({ name, description }) => api.createAnalysisAgent(name, description)
export const deleteAgent = (id) => api.deleteAnalysisAgent(id)

// Persist just the graph. Returns the updated agent.
export const saveFlow = (id, flow) => api.updateAnalysisAgent(id, { flow })
// Draft ⇄ active ⇄ paused. Only 'active' agents are offered to the chat agent.
export const setStatus = (id, status) => api.updateAnalysisAgent(id, { status })
export const rename = (id, patch) => api.updateAnalysisAgent(id, patch)
// admin only: make an agent public so every user can RUN it (only the owner edits it)
export const setPublic = (id, is_public) => api.updateAnalysisAgent(id, { is_public })
// Run the flow once from the canvas to preview its output.
export const testRun = (id, body) => api.testRunAnalysisAgent(id, body)
// Build/modify the flow from a plain-language brief using AI.
export const buildWithAI = (id, body) => api.buildAnalysisAgent(id, body)

// Scheduling (the "Trigger on Intervals" node): what the agent's schedule is and
// how it has been going, and AI help turning a description into a cron expression.
export const getSchedule = (id) => api.getAnalysisSchedule(id)
export const suggestCron = (brief) => api.suggestCron(brief)

// Portable JSON: export one agent, or import a payload into a new draft agent.
export const exportAgent = (id) => api.exportAnalysisAgent(id)
export const importAgent = (payload) => api.importAnalysisAgent(payload)

// Which data conditions this instance can offer. A condition whose module is
// not installed is shown disabled rather than silently never firing.
export const triggerSources = () => api.get('/api/trigger-sources')
