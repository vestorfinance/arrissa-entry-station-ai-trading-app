// API client for the Arrissa Exness API backend (FastAPI on :8000, proxied at /api).

const BASE = import.meta.env.VITE_API_BASE || '/api'

// Turn a FastAPI error body into a readable string. `detail` is a string for our
// HTTPExceptions but an ARRAY of {loc, msg} objects for 422 validation errors —
// the latter must be flattened or it renders as "[object Object]".
function apiError(data, status) {
  const d = data && data.detail
  if (Array.isArray(d)) {
    const msgs = d.map((e) => {
      if (typeof e === 'string') return e
      const field = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : ''
      if (field === 'email') return 'Please enter a valid email address.'
      if (field === 'password') return 'Password must be at least 8 characters.'
      return (e.msg || 'Invalid input').replace(/^Value error,\s*/i, '')
    })
    return [...new Set(msgs)].join(' ')
  }
  if (typeof d === 'string') return d
  return (data && data.message) || `Request failed (${status})`
}

async function req(path, opts = {}) {
  const token = localStorage.getItem('auth_token')
  const res = await fetch(BASE + path, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts.headers || {}),
    },
  })
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) {
    throw new Error(apiError(data, res.status))
  }
  return data
}

// auth
export const login = (email, password) =>
  req('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
export const me = () => req('/me')

// signup: email → verify code → complete profile (invite = private-link code)
export const signupStart = (email, invite = '') =>
  req('/signup/start', { method: 'POST', body: JSON.stringify({ email, invite }) })
export const signupVerify = (email, code, invite = '') =>
  req('/signup/verify', { method: 'POST', body: JSON.stringify({ email, code, invite }) })
export const signupComplete = (payload) =>
  req('/signup/complete', { method: 'POST', body: JSON.stringify(payload) })
// is registration reachable (open, or this invite code valid)?
export const checkInvite = (code) =>
  req(`/signup/invite?code=${encodeURIComponent(code)}`)
// admin (owner-only): the private invite link
export const getAdminInvite = () => req('/admin/invite')
export const rotateAdminInvite = () => req('/admin/invite/rotate', { method: 'POST' })
export const changePassword = (current_password, new_password) =>
  req('/me/password', { method: 'POST', body: JSON.stringify({ current_password, new_password }) })

// api keys
export const listKeys = () => req('/keys')
export const primaryKey = () => req('/keys/primary')

// Re-read a chart the chat is showing (called when it scrolls back into view).
export const chartData = ({ symbol, timeframe, count, account }) =>
  req(`/market/chart?${new URLSearchParams({
    symbol, timeframe, count: String(count),
    ...(account ? { account: String(account) } : {}),
  })}`)

// voice input — send recorded audio to Whisper, get the transcript back
export async function transcribe(blob) {
  const token = localStorage.getItem('auth_token')
  const type = blob.type || 'audio/webm'
  const ext = type.includes('ogg') ? 'ogg'
    : (type.includes('mp4') || type.includes('m4a')) ? 'mp4'
      : type.includes('wav') ? 'wav' : 'webm'
  const fd = new FormData()
  fd.append('file', blob, `audio.${ext}`)
  const res = await fetch(BASE + '/transcribe', {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},   // no Content-Type: browser sets the multipart boundary
    body: fd,
  })
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) throw new Error((data && (data.detail || data.message)) || `Request failed (${res.status})`)
  return data
}

// live-panel position actions (bearer auth, explicit account)
export const closePositions = (account, position_id = null) =>
  req('/positions/close', { method: 'POST', body: JSON.stringify({ account, position_id }) })
export const breakEvenPositions = (account, position_id = null) =>
  req('/positions/break-even', { method: 'POST', body: JSON.stringify({ account, position_id }) })

// per-user Exness connection (each user connects their OWN account)
export const exnessConnection = () => req('/exness/connection')
export const exnessConnect = (exness_email, exness_password) =>
  req('/exness/connect', { method: 'POST', body: JSON.stringify({ exness_email, exness_password }) })
// disconnect: deletes the session token — no credentials are ever kept
export const exnessDisconnect = () => req('/exness/disconnect', { method: 'POST' })

export const getExnessAccounts = () => req('/exness/accounts')
export const setExnessSelection = (selected, auto_connect_future) =>
  req('/exness/selection', { method: 'POST', body: JSON.stringify({ selected, auto_connect_future }) })
export const getActiveAccount = () => req('/exness/active')
export const setActiveAccount = (account) =>
  req('/exness/active', { method: 'POST', body: JSON.stringify({ account }) })

// unified accounts across ALL brokers (Accounts page)
export const getAllAccounts = () => req('/accounts')
export const setActiveAccountUnified = (broker, account) =>
  req('/accounts/active', { method: 'POST', body: JSON.stringify({ broker, account }) })

// TradeLocker connection (per-user; multiple logins: demo + live allowed)
export const tradelockerConnection = () => req('/tradelocker/connection')
export const tradelockerConnect = ({ email, password, server, environment }) =>
  req('/tradelocker/connect', {
    method: 'POST',
    body: JSON.stringify({ email, password, server, environment }),
  })
export const tradelockerDisconnect = (connection_id = null) =>
  req('/tradelocker/disconnect', { method: 'POST', body: JSON.stringify({ connection_id }) })
export const createKey = (name) =>
  req('/keys', { method: 'POST', body: JSON.stringify({ name }) })
export const revokeKey = (id) => req(`/keys/${id}`, { method: 'DELETE' })

// chat history
export const listChats = () => req('/chats')
export const createChat = (title, messages) =>
  req('/chats', { method: 'POST', body: JSON.stringify({ title, messages }) })
export const getChat = (id) => req(`/chats/${id}`)
export const updateChat = (id, patch) =>
  req(`/chats/${id}`, { method: 'PUT', body: JSON.stringify(patch) })
export const deleteChat = (id) => req(`/chats/${id}`, { method: 'DELETE' })

// per-user memory
export const getMemory = () => req('/memory')
export const setMemory = (content) =>
  req('/memory', { method: 'PUT', body: JSON.stringify({ content }) })

// per-user chat preferences (persisted model + account selection)
export const getPrefs = () => req('/prefs')
export const setPrefs = (patch) =>
  req('/prefs', { method: 'PUT', body: JSON.stringify(patch) })

// HMR (High Margin Requirement) notifications for the bottom-left notifier
export const getHmrAlerts = () => req('/hmr/alerts')

// risk parameters (profile-wide default + per-account overrides)
export const getRiskSettings = () => req('/risk-settings')
export const saveRiskSettings = (body) =>
  req('/risk-settings', { method: 'PUT', body: JSON.stringify(body) })
export const clearRiskSettings = (account = '') =>
  req(`/risk-settings?account=${encodeURIComponent(account)}`, { method: 'DELETE' })

// analysis agents (flow graphs the chat agent can call as tools)
export const listAnalysisAgents = () => req('/analysis-agents')
export const getAnalysisAgent = (id) => req(`/analysis-agents/${id}`)
export const createAnalysisAgent = (name, description) =>
  req('/analysis-agents', { method: 'POST', body: JSON.stringify({ name, description }) })
export const updateAnalysisAgent = (id, patch) =>
  req(`/analysis-agents/${id}`, { method: 'PUT', body: JSON.stringify(patch) })
export const deleteAnalysisAgent = (id) => req(`/analysis-agents/${id}`, { method: 'DELETE' })
export const testRunAnalysisAgent = (id, body) =>
  req(`/analysis-agents/${id}/test-run`, { method: 'POST', body: JSON.stringify(body) })

// The same run, narrated. `onEvent` is called with each {type: node|tool|done|failed}
// as it happens; the promise resolves with the final result.
//
// Not EventSource: that is GET-only and cannot carry the Authorization header,
// so the stream is read off a plain fetch. SSE framing is simple enough to parse
// here — events are separated by a blank line, payload lines start with "data:",
// and a line starting with ":" is a keep-alive comment to be ignored.
export async function testRunAnalysisAgentStream(id, body, onEvent) {
  const token = localStorage.getItem('auth_token')
  const res = await fetch(`${BASE}/analysis-agents/${id}/test-run/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    // An error here is a real HTTP failure (401, 402, 404) and still JSON.
    const text = await res.text()
    let data = null
    try { data = text ? JSON.parse(text) : null } catch { /* a proxy page, not ours */ }
    throw new Error(data ? apiError(data, res.status) : `Request failed (${res.status})`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let final = null
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    // Keep the last, possibly incomplete, frame in the buffer.
    const frames = buf.split('\n\n')
    buf = frames.pop()
    for (const frame of frames) {
      const data = frame.split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).trim())
        .join('\n')
      if (!data) continue
      let ev = null
      try { ev = JSON.parse(data) } catch { continue }
      if (ev.type === 'done') final = ev.result
      else if (ev.type === 'failed') final = { error: ev.message }
      onEvent?.(ev)
    }
  }
  if (!final) throw new Error('The run ended without a result — the connection dropped.')
  return final
}
export const listAnalysisRuns = (id) => req(`/analysis-agents/${id}/runs`)
export const getAnalysisRun = (id, runId) => req(`/analysis-agents/${id}/runs/${runId}`)
export const buildAnalysisAgent = (id, body) =>
  req(`/analysis-agents/${id}/build`, { method: 'POST', body: JSON.stringify(body) })
// ── modules ────────────────────────────────────────────────────────────────
// Guides and palette entries are readable by any signed-in user — they are how
// the app learns which pages exist. Anything that CHANGES a module is owner-only.
export const listModules = () => req('/modules')
export const moduleGuides = () => req('/modules/guides')
export const listBrokers = () => req('/brokers')
export const listConnections = () => req('/connections')
export const createConnection = (body) =>
  req('/connections', { method: 'POST', body: JSON.stringify(body) })
export const updateConnection = (id, body) =>
  req(`/connections/${id}`, { method: 'PUT', body: JSON.stringify(body) })
export const deleteConnection = (id) => req(`/connections/${id}`, { method: 'DELETE' })
export const getAgentPrompt = () => req('/agent/prompt')
export const saveAgentPrompt = (body) =>
  req('/agent/prompt', { method: 'PUT', body: JSON.stringify(body) })
export const instanceSettings = () => req('/instance/settings')
export const saveInstanceSettings = (body) =>
  req('/instance/settings', { method: 'PUT', body: JSON.stringify(body) })
export const runWatchListNow = () => req('/instance/watch-list/run', { method: 'POST' })
export const setAvailableAccounts = (broker, accounts) =>
  req('/accounts/available', { method: 'POST', body: JSON.stringify({ broker, accounts }) })
export const aiConfig = () => req('/ai/config')
export const setAiProviderKey = (provider, key) =>
  req('/ai/provider', { method: 'POST', body: JSON.stringify({ provider, key }) })
export const listAiModels = (provider) => req(`/ai/models?provider=${encodeURIComponent(provider)}`)
export const chooseAiModels = (models) =>
  req('/ai/models', { method: 'POST', body: JSON.stringify({ models }) })
export const moduleCatalog = () => req('/modules/catalog')
export const setModuleLicence = (key) =>
  req('/modules/licence', { method: 'POST', body: JSON.stringify({ key }) })
// Ask the store what this box has bought and apply it. The host is sent because
// it is the name the purchase was made against — the same one Buy carried out.
export const claimEntitlements = () =>
  req('/modules/claim', { method: 'POST', body: JSON.stringify({ instance: window.location.host }) })
export const installFromStore = (id) =>
  req(`/modules/install-remote/${id}`, { method: 'POST' })
export const modulePalette = () => req('/modules/palette')
export const enableModule = (id) => req(`/modules/${id}/enable`, { method: 'POST' })
export const disableModule = (id) => req(`/modules/${id}/disable`, { method: 'POST' })
export const removeModule = (id) => req(`/modules/${id}`, { method: 'DELETE' })
export const updateModule = (id) => req(`/modules/${id}/update`, { method: 'POST' })
export const moduleUpdates = () => req('/modules/updates')

// Multipart, so no Content-Type header — the browser sets the boundary itself.
export async function installModule(file) {
  const token = localStorage.getItem('auth_token')
  const fd = new FormData()
  fd.append('file', file, file.name)
  const res = await fetch(BASE + '/modules/install', {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  })
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) throw new Error(apiError(data, res.status))
  return data
}

export const getAnalysisSchedule = (id) => req(`/analysis-agents/${id}/schedule`)
// plain language → a checked cron expression (a model call: costs credits)
export const suggestCron = (brief) =>
  req('/cron/suggest', { method: 'POST', body: JSON.stringify({ brief }) })
export const exportAnalysisAgent = (id) => req(`/analysis-agents/${id}/export`)
export const importAnalysisAgent = (payload) =>
  req('/analysis-agents/import', { method: 'POST', body: JSON.stringify(payload) })

// admin (owner-only): Analysis API request sharing (one analysis per window)
export const getAdminByok = () => req('/admin/byok')
export const setAdminByok = (payload) =>
  req('/admin/byok', { method: 'PUT', body: JSON.stringify(payload) })
export const getAdminAnalysisApi = () => req('/admin/analysis-api')
export const setAdminAnalysisApi = (payload) =>
  req('/admin/analysis-api', { method: 'PUT', body: JSON.stringify(payload) })

// admin (owner-only): the Daily Watch List system agent — schedule + run now
export const getAdminWatchList = () => req('/admin/watch-list')
export const setAdminWatchSchedule = (hours) =>
  req('/admin/watch-list/schedule', { method: 'PUT', body: JSON.stringify({ hours }) })
export const runAdminWatchList = () => req('/admin/watch-list/run', { method: 'POST' })

// admin: TradeLocker developer/partner API key (app-level; owner-only)
export const getAdminTradelockerKey = () => req('/admin/tradelocker-key')
export const setAdminTradelockerKey = (key) =>
  req('/admin/tradelocker-key', { method: 'POST', body: JSON.stringify({ key }) })

// billing — plans, credits, (simulated) Paystack
export const getBillingCatalog = () => req('/billing/catalog')
export const getBilling = () => req('/billing/me')
export const getBillingLedger = () => req('/billing/ledger')
export const billingCheckout = (payload) =>
  req('/billing/checkout', { method: 'POST', body: JSON.stringify(payload) })
export const billingSimulate = (reference, outcome) =>
  req('/billing/simulate', { method: 'POST', body: JSON.stringify({ reference, outcome }) })
export const billingVerify = (reference) =>
  req('/billing/verify', { method: 'POST', body: JSON.stringify({ reference }) })
export const billingCancel = () => req('/billing/cancel', { method: 'POST' })

// admin (owner-only): Paystack keys, environment (test/live), plan sync
export const getAdminPaystack = () => req('/admin/paystack')
export const setAdminPaystackKeys = (mode, secret, publicKey) =>
  req('/admin/paystack/keys', { method: 'POST', body: JSON.stringify({ mode, secret, public: publicKey }) })
export const setAdminPaystackMode = (mode) =>
  req('/admin/paystack/mode', { method: 'POST', body: JSON.stringify({ mode }) })
export const syncAdminPaystackPlans = (mode) =>
  req('/admin/paystack/sync-plans', { method: 'POST', body: JSON.stringify({ mode }) })

// admin backend (owner-only)
export const adminOverview = () => req('/admin/overview')
export const adminHealth = () => req('/admin/system/health')
export const adminUsers = (params = {}) => req('/admin/users?' + new URLSearchParams(params).toString())
export const adminUser = (id) => req(`/admin/users/${id}`)
export const adminAdjustCredits = (id, amount, note) =>
  req(`/admin/users/${id}/credits`, { method: 'POST', body: JSON.stringify({ amount, note }) })
export const adminSetPlan = (id, plan, interval = 'monthly') =>
  req(`/admin/users/${id}/plan`, { method: 'POST', body: JSON.stringify({ plan, interval }) })
export const adminSuspend = (id, suspended) =>
  req(`/admin/users/${id}/suspend`, { method: 'POST', body: JSON.stringify({ suspended }) })
export const adminAudit = () => req('/admin/audit')
export const adminTransactions = (kind = '') =>
  req(`/admin/transactions${kind ? `?kind=${encodeURIComponent(kind)}` : ''}`)
// admin settings (everything the owner manages)
export const adminSettings = () => req('/admin/settings')
export const adminSetAIKey = (provider, key) =>
  req('/admin/ai-keys', { method: 'POST', body: JSON.stringify({ provider, key }) })
export const adminSetRegistrations = (open) =>
  req('/admin/registrations', { method: 'POST', body: JSON.stringify({ open }) })
export const adminSetBranding = (app_name) =>
  req('/admin/branding', { method: 'POST', body: JSON.stringify({ app_name }) })
export const adminSetSmtp = (body) => req('/admin/smtp', { method: 'POST', body: JSON.stringify(body) })
export const adminAddAdmin = (email, role = 'admin') =>
  req('/admin/admins', { method: 'POST', body: JSON.stringify({ email, role }) })
export const adminRemoveAdmin = (email) =>
  req(`/admin/admins/${encodeURIComponent(email)}`, { method: 'DELETE' })
// public branding config
export const appConfig = () => req('/app-config')

// AI models — the branded models the operator has enabled (arrissa-chat / arrissa-pro).
// No bring-your-own-key: users can't set provider keys or pick raw models.
export const getAIConfig = () => req('/ai/config')

// Agent chat — streams Server-Sent Events. onEvent gets each parsed event dict.
export async function agentChat({ model, messages, accounts }, onEvent, signal) {
  const token = localStorage.getItem('auth_token')
  const res = await fetch(BASE + '/agent/chat', {
    method: 'POST',
    signal,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ model, messages, accounts }),
  })
  if (!res.ok) {
    const text = await res.text()
    let msg = `Request failed (${res.status})`
    try { msg = JSON.parse(text).detail || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    // SSE frames are separated by a blank line; sse_starlette emits CRLF (\r\n\r\n),
    // so normalise line endings before splitting.
    buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
    const frames = buf.split('\n\n')
    buf = frames.pop()   // keep the trailing partial frame
    for (const frame of frames) {
      const line = frame.split('\n').find((l) => l.startsWith('data:'))
      if (!line) continue
      const json = line.slice(5).trim()
      if (!json) continue
      try { onEvent(JSON.parse(json)) } catch { /* ignore malformed */ }
    }
  }
}
