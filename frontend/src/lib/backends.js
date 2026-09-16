const PYTHON_BACKEND = {
  id: 'python',
  label: 'Python',
  baseUrl: runtimeConfig().pythonApiUrl || '/api/python',
  port: '8000'
}

export function createInitialSettings() {
  const saved = readSettings()
  return {
    backend: 'python',
    userId: saved.userId && saved.userId !== 'u1001' ? saved.userId : 'anonymous',
    conversationId: saved.conversationId || '',
    endpoints: {
      python: PYTHON_BACKEND.baseUrl
    }
  }
}

export function saveSettings(settings) {
  localStorage.setItem('medipet.frontend.settings', JSON.stringify(settings))
}

export function backendMeta(type, settings) {
  const meta = PYTHON_BACKEND
  return {
    ...meta,
    baseUrl: normalizeBaseUrl(settings.endpoints.python || meta.baseUrl)
  }
}

export async function requestHealth(type, settings) {
  return requestJson(backendMeta(type, settings).baseUrl, '/health')
}

export async function requestMonitor(type, settings) {
  return requestJson(backendMeta(type, settings).baseUrl, '/monitor')
}

export async function requestSkills(type, settings) {
  return requestJson(backendMeta(type, settings).baseUrl, '/skills')
}

export async function reloadSkills(type, settings) {
  return requestJson(backendMeta(type, settings).baseUrl, '/skills/reload', { method: 'POST' })
}

export async function requestKnowledgeStats(type, settings) {
  return requestJson(backendMeta(type, settings).baseUrl, '/knowledge/stats')
}

export async function runEvaluation(type, settings, body = null) {
  return requestJson(backendMeta(type, settings).baseUrl, '/eval/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined
  })
}

export async function requestSearch(type, settings, query, topK = 5) {
  const params = new URLSearchParams({ query, top_k: String(topK) })
  return requestJson(backendMeta(type, settings).baseUrl, `/search?${params}`, { method: 'POST' })
}

export async function requestChat(type, settings, message) {
  const meta = backendMeta(type, settings)
  const payload = buildChatPayload(type, settings, message)
  const raw = await requestJson(meta.baseUrl, '/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  return normalizeChatResponse(type, raw)
}

export async function requestPatients(type, settings) {
  const params = new URLSearchParams({ user_id: settings.userId || 'anonymous' })
  return requestJson(backendMeta(type, settings).baseUrl, `/patients?${params}`)
}

export async function uploadReport(type, settings, file) {
  const form = new FormData()
  form.append('file', file)
  form.append('user_id', settings.userId || 'anonymous')
  if (settings.patientId) form.append('patient_id', settings.patientId)
  if (settings.conversationId) form.append('conv_id', settings.conversationId)
  return requestJson(backendMeta(type, settings).baseUrl, '/reports/preprocess', { method: 'POST', body: form })
}

export async function requestVisits(type, settings, patientId, archived = false) {
  const params = new URLSearchParams({
    user_id: settings.userId || 'anonymous',
    patient_id: patientId,
    archived: String(archived)
  })
  return requestJson(backendMeta(type, settings).baseUrl, `/visits?${params}`)
}

export async function createVisit(type, settings, patientId, title) {
  return requestJson(backendMeta(type, settings).baseUrl, '/visits', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: settings.userId || 'anonymous', patient_id: patientId, title })
  })
}

export async function updateVisit(type, settings, conversationId, { title, archived } = {}) {
  return requestJson(backendMeta(type, settings).baseUrl, `/visits/${encodeURIComponent(conversationId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: settings.userId || 'anonymous', title, archived })
  })
}

export async function requestVisitMessages(type, settings, conversationId) {
  const params = new URLSearchParams({ user_id: settings.userId || 'anonymous' })
  return requestJson(backendMeta(type, settings).baseUrl, `/visits/${encodeURIComponent(conversationId)}/messages?${params}`)
}

export async function confirmAppointmentProposal(type, settings, proposalId, conversationId = settings.conversationId) {
  return requestJson(backendMeta(type, settings).baseUrl, `/appointment-proposals/${encodeURIComponent(proposalId)}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: settings.userId || 'anonymous', conv_id: conversationId })
  })
}

export async function requestToolTrace(type, settings, requestId) {
  if (!requestId) return null
  const raw = await requestJson(backendMeta(type, settings).baseUrl, `/trace/tool/${encodeURIComponent(requestId)}`)
  return normalizeToolTraceResponse(raw)
}

export async function addKnowledge(type, settings, documents) {
  return requestJson(backendMeta(type, settings).baseUrl, '/knowledge/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ documents })
  })
}

export async function uploadKnowledge(type, settings, file) {
  const form = new FormData()
  form.append('file', file)
  return requestJson(backendMeta(type, settings).baseUrl, '/knowledge/upload', {
    method: 'POST',
    body: form
  })
}

function buildChatPayload(type, settings, message) {
  return {
    message,
    user_id: settings.userId || 'anonymous',
    conv_id: settings.conversationId || undefined,
    patient_id: settings.patientId || undefined
  }
}

function normalizeChatResponse(type, raw) {
  return {
    backend: type,
    conversationId: raw.conversation_id || raw.conversationId || raw.conv_id || '',
    patientId: raw.patient_id || '',
    visit: raw.visit || null,
    artifacts: raw.artifacts || [],
    requestId: raw.request_id || raw.requestId || '',
    response: raw.response || '',
    intent: raw.intent || 'other',
    intentGroup: raw.intent_group || raw.intentGroup || 'other',
    agentType: raw.agent_type || raw.agentType || '',
    agentTypes: raw.agent_types || raw.agentTypes || [],
    primaryAgent: raw.primary_agent || raw.primaryAgent || '',
    supportingAgents: raw.supporting_agents || raw.supportingAgents || [],
    routingReason: raw.routing_reason || raw.routingReason || '',
    routingConfidence: Number(raw.routing_confidence ?? raw.routingConfidence ?? 0),
    toolsUsed: raw.tools_used || raw.toolsUsed || [],
    entities: raw.entities || {},
    intentConfidence: Number(raw.intent_confidence ?? raw.intentConfidence ?? 0),
    intentSourceScores: raw.intent_source_scores || raw.intentSourceScores || {},
    escalated: Boolean(raw.escalated),
    latencyMs: Number(raw.latency_ms ?? raw.latencyMs ?? 0),
    knowledgeUsed: Boolean(raw.knowledge_used ?? raw.knowledgeUsed),
    verified: raw.verified,
    grounded: raw.grounded,
    raw
  }
}

function normalizeToolTraceResponse(raw) {
  const trace = raw?.trace || {}
  return {
    requestId: raw?.request_id || raw?.requestId || '',
    found: Boolean(raw?.found),
    trace: {
      ...trace,
      toolsUsed: trace.tools_used || trace.toolsUsed || [],
      toolCalls: trace.tool_calls || trace.toolCalls || []
    },
    raw
  }
}

async function requestJson(baseUrl, path, options = {}) {
  const url = `${normalizeBaseUrl(baseUrl)}${path}`
  let response
  let text
  try {
    response = await fetch(url, options)
    text = await response.text()
  } catch (cause) {
    const error = new Error(cause.message || '网络请求失败', { cause })
    error.kind = 'network'
    throw error
  }
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!response.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : data
    const businessError = detail && !Array.isArray(detail) && typeof detail.code === 'string'
    const message = businessError ? detail.message : typeof detail === 'string' ? detail : JSON.stringify(detail)
    const error = new Error(`${response.status} ${response.statusText}: ${message}`)
    error.kind = businessError ? 'business' : Array.isArray(detail) && response.status === 422 ? 'validation' : 'http'
    error.status = response.status
    error.detail = detail
    error.data = data
    if (businessError) {
      error.code = detail.code
      error.retryable = detail.retryable
    }
    throw error
  }
  return data
}

function normalizeBaseUrl(value) {
  return String(value || '').replace(/\/+$/, '')
}

function readSettings() {
  try {
    return JSON.parse(localStorage.getItem('medipet.frontend.settings') || '{}')
  } catch {
    return {}
  }
}

function runtimeConfig() {
  if (typeof window === 'undefined') return {}
  return window.__MEDIPET_CONFIG__ || {}
}
