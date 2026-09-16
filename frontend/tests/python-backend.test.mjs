import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createServer as createHttpServer } from 'node:http'
import { once } from 'node:events'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createServer as createViteServer } from 'vite'

const frontendRoot = new URL('../', import.meta.url)
const backendUrl = new URL('../src/lib/backends.js', import.meta.url)
let moduleSequence = 0

function browserState(t, { runtime = {}, saved = {} } = {}) {
  const storage = new Map(Object.entries(saved))
  const previousWindow = globalThis.window
  const previousStorage = globalThis.localStorage
  globalThis.window = { __MEDIPET_CONFIG__: runtime }
  globalThis.localStorage = {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value)
  }
  t.after(() => {
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
    if (previousStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = previousStorage
  })
  return storage
}

function loadBackend() {
  moduleSequence += 1
  return import(`${backendUrl.href}?test=${moduleSequence}`)
}

test('saved settings retain identity but cannot select another service or stale endpoint', async t => {
  const storage = browserState(t, {
    saved: {
      'medipet.frontend.settings': JSON.stringify({
        backend: 'obsolete', userId: 'demo-user', conversationId: 'visit-1',
        endpoints: { python: '/stale-service', obsolete: '/other-service' }
      })
    }
  })
  const api = await loadBackend()
  const settings = api.createInitialSettings()
  assert.deepEqual(settings, {
    backend: 'python', userId: 'demo-user', conversationId: 'visit-1',
    endpoints: { python: '/api/python' }
  })
  assert.equal(api.backendMeta(settings.backend, settings).id, 'python')
  api.saveSettings(settings)
  assert.deepEqual(JSON.parse(storage.get('medipet.frontend.settings')), settings)
})

test('all existing request helpers use the Python prefix and preserve methods and payloads', async t => {
  browserState(t)
  const api = await loadBackend()
  const settings = api.createInitialSettings()
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    calls.push({ url, ...options })
    return new Response(JSON.stringify({ status: 'ok', response: 'ready', conv_id: 'visit-2' }))
  })
  await api.requestHealth('python', settings)
  await api.requestMonitor('python', settings)
  await api.requestSkills('python', settings)
  await api.reloadSkills('python', settings)
  await api.requestKnowledgeStats('python', settings)
  await api.runEvaluation('python', settings, { mode: 'intent' })
  await api.requestSearch('python', settings, 'visit guide', 3)
  const chat = await api.requestChat('python', settings, 'hello')
  await api.requestToolTrace('python', settings, 'request/1')
  await api.addKnowledge('python', settings, [{ title: 'Guide', content: 'Text' }])
  await api.uploadKnowledge('python', settings, new File(['text'], 'guide.txt'))
  assert.deepEqual(calls.map(call => [call.method || 'GET', call.url]), [
    ['GET', '/api/python/health'], ['GET', '/api/python/monitor'],
    ['GET', '/api/python/skills'], ['POST', '/api/python/skills/reload'],
    ['GET', '/api/python/knowledge/stats'], ['POST', '/api/python/eval/run'],
    ['POST', '/api/python/search?query=visit+guide&top_k=3'], ['POST', '/api/python/chat'],
    ['GET', '/api/python/trace/tool/request%2F1'], ['POST', '/api/python/knowledge/add'],
    ['POST', '/api/python/knowledge/upload']
  ])
  assert.deepEqual(JSON.parse(calls[7].body), { message: 'hello', user_id: 'anonymous' })
  assert.equal(calls[10].body.get('file').name, 'guide.txt')
  assert.equal(chat.conversationId, 'visit-2')
  assert.equal(chat.backend, 'python')
})

test('runtime Python address is consumed and normalized', async t => {
  browserState(t, { runtime: { pythonApiUrl: 'https://example.invalid/api/python/' } })
  const api = await loadBackend()
  const calls = []
  t.mock.method(globalThis, 'fetch', async url => {
    calls.push(url)
    return new Response('{"status":"ok"}')
  })
  await api.requestHealth('python', api.createInitialSettings())
  assert.deepEqual(calls, ['https://example.invalid/api/python/health'])
})

test('the actual App health handler reports one failed request without service fallback', async t => {
  browserState(t)
  const source = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const script = compileScript(descriptor, { id: 'b02-health-check' }).content
    // This handler-only SSR probe does not render child components; App integration tests mount them.
    .replace(/^import (BusinessArtifacts|PatientVisitPanel|HealthArtifacts|ReportUpload) from '.+'$/gm, 'const $1 = {}')
    .replace("from 'vue'", `from '${import.meta.resolve('vue')}'`)
    .replace("from './lib/backends'", `from '${backendUrl.href}'`)
  const { default: App } = await import(`data:text/javascript;base64,${Buffer.from(script).toString('base64')}`)
  let state
  await renderToString(createSSRApp({
    setup(props, context) {
      state = App.setup(props, context)
      return () => ''
    }
  }))
  const calls = []
  t.mock.method(globalThis, 'fetch', async url => {
    calls.push(url)
    throw new Error('Python service unavailable')
  })
  await state.checkHealth()
  assert.deepEqual(calls, ['/api/python/health'])
  assert.equal(state.settings.backend, 'python')
  assert.equal(state.healthOk.value, false)
  assert.equal(state.healthLabel.value, '不可用')
  assert.equal(state.statusText.value, 'Python service unavailable')
})

test('Vite proxies the agreed prefix to a local Python-shaped stub and serves runtime configuration', async t => {
  const requests = []
  const upstream = createHttpServer((request, response) => {
    requests.push(request.url)
    response.setHeader('Content-Type', 'application/json')
    response.end('{"status":"ok"}')
  })
  upstream.listen(0, '127.0.0.1')
  await once(upstream, 'listening')
  t.after(() => new Promise(resolve => upstream.close(resolve)))
  const previousUpstream = process.env.MEDIPET_PYTHON_API_URL
  process.env.MEDIPET_PYTHON_API_URL = `http://127.0.0.1:${upstream.address().port}`
  t.after(() => {
    if (previousUpstream === undefined) delete process.env.MEDIPET_PYTHON_API_URL
    else process.env.MEDIPET_PYTHON_API_URL = previousUpstream
  })
  const vite = await createViteServer({
    root: fileURLToPath(frontendRoot),
    configFile: fileURLToPath(new URL('../vite.config.js', import.meta.url)),
    server: { host: '127.0.0.1', port: 0 }, logLevel: 'silent'
  })
  t.after(() => vite.close())
  await vite.listen()
  assert.deepEqual(Object.keys(vite.config.server.proxy), ['/api/python'])
  const origin = `http://127.0.0.1:${vite.httpServer.address().port}`
  const health = await fetch(`${origin}/api/python/health`)
  assert.deepEqual(await health.json(), { status: 'ok' })
  assert.deepEqual(requests, ['/health'])
  const runtime = await (await fetch(`${origin}/runtime-config.js`)).text()
  assert.match(runtime, /window\.__MEDIPET_CONFIG__/)
  assert.match(runtime, /pythonApiUrl: '\/api\/python'/)
})

async function fixture(name) {
  return JSON.parse(await readFile(new URL(`./fixtures/api/${name}.json`, import.meta.url), 'utf8'))
}

test('the old demo identity migrates to anonymous while explicit identities remain intact', async t => {
  browserState(t, { saved: { 'medipet.frontend.settings': JSON.stringify({ userId: 'u1001' }) } })
  const api = await loadBackend()
  assert.equal(api.createInitialSettings().userId, 'anonymous')
  globalThis.localStorage.setItem('medipet.frontend.settings', JSON.stringify({ userId: 'explicit-user' }))
  assert.equal(api.createInitialSettings().userId, 'explicit-user')
  globalThis.localStorage.setItem('medipet.frontend.settings', '{}')
  assert.equal(api.createInitialSettings().userId, 'anonymous')
})

test('patients, visits, history and confirmation methods follow H01 and preserve complete responses', async t => {
  browserState(t)
  const api = await loadBackend()
  const settings = { ...api.createInitialSettings(), conversationId: 'visit-example', patientId: 'patient_child' }
  const patients = await fixture('patients')
  const visit = await fixture('visit')
  const history = await fixture('history')
  const confirm = await fixture('confirm-create')
  const cancellation = await fixture('confirm-cancel')
  const responses = [patients, { items: [visit] }, { items: [] }, visit, visit, { ...visit, archived: false }, history, confirm, confirm, cancellation]
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options = {}) => {
    calls.push({ url, ...options })
    return new Response(JSON.stringify(responses[calls.length - 1]), { status: calls.length === 4 ? 201 : 200 })
  })
  assert.deepEqual(await api.requestPatients('python', settings), patients)
  assert.deepEqual(await api.requestVisits('python', settings, 'patient_child'), { items: [visit] })
  assert.deepEqual(await api.requestVisits('python', settings, 'patient_child', true), { items: [] })
  assert.deepEqual(await api.createVisit('python', settings, 'patient_child'), visit)
  await api.createVisit('python', settings, 'patient_child', '继续就诊')
  await api.updateVisit('python', settings, 'visit-example', { title: '复诊', archived: false, patient_id: 'not-permitted' })
  assert.deepEqual(await api.requestVisitMessages('python', settings, 'visit-example'), history)
  const first = await api.confirmAppointmentProposal('python', settings, 'proposal-example')
  const repeated = await api.confirmAppointmentProposal('python', settings, 'proposal-example')
  assert.deepEqual(first, confirm)
  assert.deepEqual(repeated, first)
  assert.deepEqual(await api.confirmAppointmentProposal('python', settings, 'proposal-cancel-example', 'visit-followup'), cancellation)
  assert.deepEqual(calls.map(call => [call.method || 'GET', call.url]), [
    ['GET', '/api/python/patients?user_id=anonymous'],
    ['GET', '/api/python/visits?user_id=anonymous&patient_id=patient_child&archived=false'],
    ['GET', '/api/python/visits?user_id=anonymous&patient_id=patient_child&archived=true'],
    ['POST', '/api/python/visits'], ['POST', '/api/python/visits'],
    ['PATCH', '/api/python/visits/visit-example'],
    ['GET', '/api/python/visits/visit-example/messages?user_id=anonymous'],
    ['POST', '/api/python/appointment-proposals/proposal-example/confirm'],
    ['POST', '/api/python/appointment-proposals/proposal-example/confirm'],
    ['POST', '/api/python/appointment-proposals/proposal-cancel-example/confirm']
  ])
  assert.deepEqual(JSON.parse(calls[3].body), { user_id: 'anonymous', patient_id: 'patient_child' })
  assert.deepEqual(JSON.parse(calls[4].body), { user_id: 'anonymous', patient_id: 'patient_child', title: '继续就诊' })
  assert.deepEqual(JSON.parse(calls[5].body), { user_id: 'anonymous', title: '复诊', archived: false })
  assert.deepEqual(JSON.parse(calls[7].body), { user_id: 'anonymous', conv_id: 'visit-example' })
  assert.deepEqual(JSON.parse(calls[9].body), { user_id: 'anonymous', conv_id: 'visit-followup' })
  assert.equal(cancellation.receipt.conv_id, 'visit-followup')
  assert.equal(cancellation.receipt.appointment.conv_id, 'visit-example')
  assert.equal(Object.hasOwn(first, 'intent'), false)
  assert.equal(Object.hasOwn(first, 'agentType'), false)
})

test('chat sends controlled patient/visit fields and preserves artifacts, visit and routing evidence', async t => {
  browserState(t)
  const api = await loadBackend()
  const settings = { ...api.createInitialSettings(), patientId: 'patient_child' }
  const response = await fixture('chat-response')
  const calls = []
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, ...options })
    return new Response(JSON.stringify(response))
  })
  const first = await api.requestChat('python', settings, '明天儿科还有号吗？')
  assert.deepEqual(JSON.parse(calls[0].body), { message: '明天儿科还有号吗？', user_id: 'anonymous', patient_id: 'patient_child' })
  settings.conversationId = response.conv_id
  await api.requestChat('python', settings, '第一个')
  assert.deepEqual(JSON.parse(calls[1].body), { message: '第一个', user_id: 'anonymous', patient_id: 'patient_child', conv_id: response.conv_id })
  assert.equal(first.patientId, response.patient_id)
  assert.equal(first.conversationId, response.conv_id)
  assert.deepEqual(first.visit, response.visit)
  assert.deepEqual(first.artifacts, response.artifacts)
  assert.deepEqual(first.toolsUsed, response.tools_used)
  assert.deepEqual(first.supportingAgents, response.supporting_agents)
  assert.deepEqual(first.raw, response)
})

test('every H01 business failure preserves status, detail, code and retryability', async t => {
  browserState(t)
  const api = await loadBackend()
  const settings = { ...api.createInitialSettings(), conversationId: 'visit-example' }
  const errors = await fixture('errors')
  let next
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify(next.body), { status: next.status }))
  for (const sample of errors) {
    next = sample
    await assert.rejects(api.confirmAppointmentProposal('python', settings, 'proposal-example'), error => {
      assert.equal(error.kind, 'business')
      assert.equal(error.status, sample.status)
      assert.equal(error.code, sample.body.detail.code)
      assert.equal(error.retryable, sample.body.detail.retryable)
      assert.deepEqual(error.detail, sample.body.detail)
      assert.deepEqual(error.data, sample.body)
      assert.ok(error.message.includes(sample.body.detail.message))
      return true
    })
  }
})

test('validation arrays, legacy string errors and network failures remain distinguishable', async t => {
  browserState(t)
  const api = await loadBackend()
  const settings = api.createInitialSettings()
  const validation = { detail: [{ loc: ['body', 'patient_id'], msg: 'Field required', type: 'missing' }] }
  let reply = new Response(JSON.stringify(validation), { status: 422 })
  t.mock.method(globalThis, 'fetch', async () => {
    if (reply instanceof Error) throw reply
    return reply
  })
  await assert.rejects(api.createVisit('python', settings), error => {
    assert.equal(error.kind, 'validation')
    assert.equal(error.status, 422)
    assert.deepEqual(error.detail, validation.detail)
    assert.equal(error.code, undefined)
    return true
  })
  for (const body of [JSON.stringify({ detail: 'Skills 未初始化' }), 'Upstream unavailable']) {
    reply = new Response(body, { status: 503 })
    await assert.rejects(api.requestSkills('python', settings), error => {
      assert.equal(error.kind, 'http')
      assert.equal(error.status, 503)
      assert.equal(typeof error.detail, 'string')
      assert.equal(error.code, undefined)
      assert.ok(error.message.includes(error.detail))
      return true
    })
  }
  reply = new TypeError('Network unavailable')
  await assert.rejects(api.requestPatients('python', settings), error => {
    assert.equal(error.kind, 'network')
    assert.equal(error.message, 'Network unavailable')
    assert.equal(error.cause, reply)
    assert.equal(error.status, undefined)
    return true
  })
})

test('empty successes and chat tool failures do not become HTTP business failures', async t => {
  browserState(t)
  const api = await loadBackend()
  const settings = api.createInitialSettings()
  const empty = await fixture('empty-results')
  let reply
  t.mock.method(globalThis, 'fetch', async () => reply)
  reply = new Response(JSON.stringify(empty.patients))
  assert.deepEqual(await api.requestPatients('python', settings), { items: [] })
  reply = new Response(null, { status: 204 })
  assert.equal(await api.reloadSkills('python', settings), null)
  reply = new Response('plain success')
  assert.equal(await api.requestMonitor('python', settings), 'plain success')
  reply = new Response(JSON.stringify({ ...empty.chat, tool_result: empty.tool_result }))
  const chat = await api.requestChat('python', settings, '查号')
  assert.deepEqual(chat.artifacts, empty.chat.artifacts)
  assert.equal(chat.artifacts[0].data.slots.length, 0)
  assert.deepEqual(chat.raw.tool_result, empty.tool_result)
})

test('seven-card and cancellation fixtures stay aligned with the frozen H01 examples', async () => {
  const contract = await readFile(new URL('../../docs/internal/rebuild/contracts.md', import.meta.url), 'utf8')
  const examples = marker => JSON.parse(contract.split(`<!-- ${marker} -->`)[1].match(/```json\s*([\s\S]*?)```/)[1])
  const artifacts = await fixture('artifacts')
  assert.deepEqual(artifacts, examples('artifact-examples'))
  assert.equal(new Set(artifacts.map(item => item.type)).size, 7)
  assert.deepEqual((await fixture('confirm-cancel')).receipt, examples('cancellation-example'))
  const variants = await fixture('proposal-states')
  assert.deepEqual(new Set(variants.map(item => item.data.status)), new Set(['pending', 'executed', 'expired', 'superseded']))
  assert.ok(variants.some(item => item.data.operation === 'cancel' && item.data.status === 'pending'))
})
