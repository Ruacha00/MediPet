import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import test from 'node:test'
import { parse, compileScript, compileTemplate } from 'vue/compiler-sfc'
import { transformWithEsbuild } from 'vite'
import * as api from '../src/lib/backends.js'

const require = createRequire(import.meta.url)
const Vue = require('vue')
const fixture = async name => JSON.parse(await readFile(new URL(`./fixtures/api/${name}.json`, import.meta.url), 'utf8'))
async function compile(path, imports = {}) {
  const { descriptor } = parse(await readFile(new URL(path, import.meta.url), 'utf8'), { filename: path })
  const script = compileScript(descriptor, { id: path })
  const template = compileTemplate({ source: descriptor.template.content, filename: path, id: path, compilerOptions: { bindingMetadata: script.bindings } })
  async function load(source) {
    const { code } = await transformWithEsbuild(source, `${path}.js`, { format: 'cjs', target: 'esnext' })
    const module = { exports: {} }
    new Function('require', 'module', 'exports', code)(name => imports[name] || require(name), module, module.exports)
    return module.exports
  }
  const component = (await load(script.content)).default
  component.render = (await load(template.code)).render
  return component
}
const Business = await compile('../src/components/BusinessArtifacts.vue')
const Panel = await compile('../src/components/PatientVisitPanel.vue')
const Health = await compile('../src/components/HealthArtifacts.vue')
const ReportUpload = await compile('../src/components/ReportUpload.vue')
const App = await compile('../src/App.vue', {
  './lib/backends': api,
  './components/BusinessArtifacts.vue': { default: Business, __esModule: true },
  './components/PatientVisitPanel.vue': { default: Panel, __esModule: true },
  './components/HealthArtifacts.vue': { default: Health, __esModule: true },
  './components/ReportUpload.vue': { default: ReportUpload, __esModule: true }
})
const patients = (await fixture('patients')).items
const childVisit = await fixture('visit')
const selfVisit = { ...childVisit, patient_id: 'patient_self', conv_id: 'visit-self', title: '本人门诊事项' }
const cards = await fixture('artifacts')
const confirmation = await fixture('confirm-create')
const cancellation = await fixture('confirm-cancel')
const future = card => ({ ...structuredClone(card), data: { ...structuredClone(card.data), expires_at: '2099-01-01T00:00:00+08:00' } })
function message(visit, id, content, artifacts = [], extras = {}) {
  return { user_id: visit.user_id, patient_id: visit.patient_id, conv_id: visit.conv_id, message_id: id,
    role: 'assistant', kind: 'chat', content, artifacts, metadata: {}, created_at: visit.created_at, ...extras }
}
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
const settle = async () => { for (let i = 0; i < 6; i += 1) { await new Promise(done => setImmediate(done)); await Vue.nextTick() } }

function server(t, { saved = childVisit.conv_id, histories = {}, chat, confirm, history, management } = {}) {
  const calls = []
  const visits = new Map([childVisit, selfVisit].map(item => [item.conv_id, structuredClone(item)]))
  const records = new Map([
    [childVisit.conv_id, histories[childVisit.conv_id] || [message(childVisit, 'child-greeting', '孩子事项的服务器历史')]],
    [selfVisit.conv_id, histories[selfVisit.conv_id] || [message(selfVisit, 'self-greeting', '本人事项的服务器历史')]]
  ])
  const storage = new Map([['medipet.frontend.settings', JSON.stringify({ userId: 'anonymous', conversationId: saved })]])
  t.mock.method(globalThis, 'fetch', async (input, options = {}) => {
    const url = new URL(input, 'http://test.local'), path = url.pathname.replace('/api/python', '')
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : options.body || null
    calls.push({ path, method: options.method || 'GET', body, query: url.searchParams })
    let result = await management?.(path, body, options)
    if (result !== undefined) { /* A test-specific management response takes priority. */ }
    else if (path === '/health') result = { status: 'ok' }
    else if (path === '/monitor') result = { agent_stats: {}, active_alerts: [] }
    else if (path === '/skills') result = { skills: [], count: 0 }
    else if (path === '/knowledge/stats') result = { total_chunks: 17 }
    else if (path === '/patients') result = { items: patients }
    else if (path === '/visits' && options.method === 'POST') {
      result = { ...selfVisit, patient_id: body.patient_id, conv_id: `visit-new-${visits.size}`, title: '新的就诊事项' }
      visits.set(result.conv_id, result); records.set(result.conv_id, [])
    } else if (path === '/visits') result = { items: [...visits.values()].filter(item => item.patient_id === url.searchParams.get('patient_id') && item.archived === (url.searchParams.get('archived') === 'true')) }
    else if (/\/visits\/[^/]+\/messages$/.test(path)) {
      const id = path.split('/')[2]
      result = history ? await history(id, { visits, records }) : { visit: visits.get(id), items: records.get(id) }
    } else if (path.startsWith('/visits/') && options.method === 'PATCH') {
      const id = path.split('/')[2]
      result = { ...visits.get(id), ...body }; visits.set(id, result)
    } else if (path === '/chat') {
      result = await chat?.(body, { visits, records })
      result ||= { conv_id: body.conv_id, patient_id: body.patient_id, visit: visits.get(body.conv_id), response: '仅存在响应中的文字', agent_type: 'appointment', supporting_agents: [], tool_traces: [] }
    } else if (path.includes('/confirm')) result = await confirm?.(path.split('/')[2], body, { visits, records })
    else throw new Error(`Unexpected test request: ${options.method || 'GET'} ${path}`)
    const status = result?.httpStatus || 200
    return new Response(JSON.stringify(result?.httpStatus ? { detail: result.detail } : result), { status, headers: { 'Content-Type': 'application/json' } })
  })
  const originalWindow = globalThis.window, originalStorage = globalThis.localStorage
  globalThis.window = { addEventListener() {}, removeEventListener() {} }
  globalThis.localStorage = { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) }
  t.after(() => { globalThis.window = originalWindow || { removeEventListener() {} }; globalThis.localStorage = originalStorage })
  return { calls, visits, records, storage }
}

function mount(t) {
  const node = (type, text = '') => ({ type, text, props: {}, children: [], parent: null, listeners: {}, style: { setProperty() {} },
    addEventListener(name, callback) { this.listeners[name] = callback }, getBoundingClientRect: () => ({ height: 700 }), scrollTo() {} })
  const renderer = Vue.createRenderer({
    createElement: type => node(type), createText: text => node('#text', text), createComment: text => node('#comment', text),
    setText: (item, text) => { item.text = text }, setElementText: (item, text) => { item.text = text; item.children = [] },
    patchProp: (item, key, previous, value) => { item.props[key] = value },
    insert(item, parent, anchor) { if (item.parent) item.parent.children = item.parent.children.filter(child => child !== item)
      item.parent = parent; const index = anchor ? parent.children.indexOf(anchor) : -1
      if (index < 0) parent.children.push(item); else parent.children.splice(index, 0, item) },
    remove(item) { if (item.parent) item.parent.children = item.parent.children.filter(child => child !== item) },
    parentNode: item => item.parent, nextSibling: item => item.parent?.children[item.parent.children.indexOf(item) + 1] || null
  })
  let state
  const component = { ...App, setup(props, context) { state = App.setup(props, context); return state } }
  const root = node('root'), app = renderer.createApp(component)
  app.mount(root); t.after(() => app.unmount())
  const all = (item = root) => [item, ...item.children.flatMap(all)]
  const text = item => item.text + item.children.map(text).join('')
  return { state, all, text: () => text(root), button: label => all().find(item => item.type === 'button' && text(item).includes(label)) }
}

test('refresh restores the bound patient, every server history message and business cards without local transcripts', async t => {
  const history = await fixture('history')
  const backend = server(t, { histories: { [childVisit.conv_id]: history.items } })
  const view = mount(t); await settle()
  assert.equal(view.state.patientId.value, 'patient_child')
  assert.equal(view.state.currentVisit.value.conv_id, childVisit.conv_id)
  assert.equal(view.state.messages.value.length, history.items.length)
  for (const expected of ['林小满', '业务办理回执', '预约已创建', '儿童就诊材料']) assert.ok(view.text().includes(expected), expected)
  assert.equal(view.state.proposalStates.value['proposal-example'], 'executed')
  assert.ok(!view.button('确认预约'))
  assert.ok(!JSON.parse(backend.storage.get('medipet.frontend.settings')).messages)
  await view.state.refreshHistory()
  assert.deepEqual(view.state.messages.value, history.items)
})

test('slot selection sends its real ID, chat renders only server history and confirmation bypasses chat', async t => {
  const backend = server(t, {
    histories: { [childVisit.conv_id]: [message(childVisit, 'slots', '可选的门诊号源', [cards[1]])] },
    chat(body, { records }) {
      records.get(body.conv_id).push(message(childVisit, 'proposal', '请核对方案', [future(cards[2])]))
    },
    confirm(id, body, { records }) {
      records.get(body.conv_id).push(message(childVisit, 'confirm-operation', '你的确认已保存', [], { role: 'user', kind: 'confirmation_event', proposal_id: id }),
        message(childVisit, 'operation-result', '预约已创建', confirmation.artifacts, { kind: 'operation_result', proposal_id: id, metadata: { receipt: confirmation.receipt } }))
      return confirmation
    }
  })
  const view = mount(t); await settle()
  view.button('选择此号源').props.onClick(); await settle()
  const chatCalls = backend.calls.filter(item => item.path === '/chat')
  assert.equal(chatCalls.length, 1)
  assert.deepEqual(chatCalls[0].body, { user_id: 'anonymous', patient_id: 'patient_child', conv_id: childVisit.conv_id, message: '请为当前就诊人准备预约，号源编号：slot-example' })
  assert.ok(view.text().includes('请核对方案'))
  assert.ok(!view.text().includes('仅存在响应中的文字'))
  const button = view.button('确认预约'); button.props.onClick(); button.props.onClick(); await settle()
  assert.equal(backend.calls.filter(item => item.path === '/chat').length, 1)
  const confirmed = backend.calls.filter(item => item.path.includes('/confirm'))
  assert.equal(confirmed.length, 1)
  assert.deepEqual(confirmed[0].body, { user_id: 'anonymous', conv_id: childVisit.conv_id })
  assert.equal(view.state.messages.value.length, 4)
  assert.ok(view.text().includes('业务办理回执') && view.text().includes('预约已创建'))
  assert.ok(!view.button('确认预约'))
})

test('a delayed chat response cannot replace the selected patient, visit, cards or request details', async t => {
  const held = deferred()
  const backend = server(t, { chat: async () => held.promise })
  const view = mount(t); await settle()
  const pending = view.state.sendMessage('明天呢')
  await view.state.selectPatient('patient_self')
  held.resolve({ conv_id: childVisit.conv_id, patient_id: 'patient_child', visit: childVisit, response: '孩子的迟到响应', artifacts: cards, agent_type: 'appointment' })
  await pending; await settle()
  assert.equal(view.state.patientId.value, 'patient_self')
  assert.equal(view.state.settings.conversationId, selfVisit.conv_id)
  assert.equal(view.state.messages.value[0].content, '本人事项的服务器历史')
  assert.equal(view.state.lastResponse.value, null)
  assert.ok(!view.text().includes('孩子的迟到响应'))
  assert.equal(backend.calls.find(item => item.path === '/chat').body.patient_id, 'patient_child')
})

test('late history reads cannot overwrite a newer selection and identity mismatches stay errors', async t => {
  const held = deferred(); let delay = false
  server(t, { history: async (id, { visits, records }) => delay && id === childVisit.conv_id ? held.promise : { visit: visits.get(id), items: records.get(id) } })
  const view = mount(t); await settle(); delay = true
  const pending = view.state.refreshHistory()
  await view.state.selectPatient('patient_self')
  held.resolve({ visit: childVisit, items: [message(childVisit, 'late-history', '旧的完整历史')] })
  await pending; await settle()
  assert.equal(view.state.currentVisit.value.conv_id, selfVisit.conv_id)
  assert.ok(!view.text().includes('旧的完整历史'))
  await view.state.selectVisit(childVisit.conv_id)
  assert.ok(view.state.workspaceError.value.includes('就诊人或事项不一致'))
  assert.equal(view.state.currentVisit.value, null)
  assert.equal(view.state.messages.value.length, 0)
})

test('rename, archive, restore and new visits use server state; an archived visit cannot chat', async t => {
  const backend = server(t)
  const view = mount(t); await settle()
  await view.state.renameVisit({ convId: childVisit.conv_id, title: '孩子复诊准备' })
  assert.equal(view.state.currentVisit.value.title, '孩子复诊准备')
  await view.state.archiveVisit(childVisit.conv_id)
  assert.equal(view.state.canChat.value, false)
  await view.state.sendMessage('不能发送')
  assert.equal(backend.calls.filter(item => item.path === '/chat').length, 0)
  await view.state.toggleArchived(true)
  assert.equal(view.state.messages.value[0].content, '孩子事项的服务器历史')
  await view.state.restoreVisit(childVisit.conv_id)
  assert.equal(view.state.canChat.value, true)
  assert.equal(view.state.showArchived.value, false)
  await view.state.newVisit('patient_child')
  assert.equal(view.state.currentVisit.value.patient_id, 'patient_child')
  assert.deepEqual(view.state.messages.value, [])
  assert.equal(backend.calls.filter(item => item.method === 'PATCH').length, 3)
  assert.equal(backend.calls.filter(item => item.path === '/visits' && item.method === 'POST').length, 1)
})

test('failed confirmation is readable and never creates a successful receipt or history message', async t => {
  server(t, { histories: { [childVisit.conv_id]: [message(childVisit, 'proposal', '待确认方案', [future(cards[2])])] },
    confirm: () => ({ httpStatus: 409, detail: { code: 'proposal_superseded', message: '已被新方案替代', retryable: false } }) })
  const view = mount(t); await settle()
  view.button('确认预约').props.onClick(); await settle()
  assert.ok(view.text().includes('请核对最新的方案卡片'))
  assert.equal(view.state.operationReceipt.value, null)
  assert.equal(view.state.messages.value.length, 1)
  assert.ok(!view.text().includes('业务办理回执'))
  assert.ok(!view.button('确认预约'))
})

test('cancellation prepares through chat then confirms via its own endpoint and restores the cancelled record', async t => {
  const cancelProposal = future((await fixture('proposal-states')).find(item => item.data.operation === 'cancel' && item.data.status === 'pending'))
  cancelProposal.data.conv_id = childVisit.conv_id
  const sameVisitReceipt = { ...structuredClone(cancellation), conv_id: childVisit.conv_id, receipt: { ...structuredClone(cancellation.receipt), conv_id: childVisit.conv_id } }
  const backend = server(t, { histories: { [childVisit.conv_id]: [message(childVisit, 'active', '已有预约', confirmation.artifacts)] },
    chat(body, { records }) { records.get(body.conv_id).push(message(childVisit, 'cancel-proposal', '请核对取消方案', [cancelProposal])) },
    confirm(id, body, { records }) { records.get(body.conv_id).push(message(childVisit, 'cancel-result', '预约已取消', sameVisitReceipt.artifacts, { kind: 'operation_result', proposal_id: id })); return sameVisitReceipt }
  })
  const view = mount(t); await settle()
  view.button('准备取消此预约').props.onClick(); await settle()
  assert.ok(backend.calls.find(item => item.path === '/chat').body.message.endsWith('预约编号：appointment-example'))
  view.button('确认取消预约').props.onClick(); await settle()
  assert.equal(backend.calls.filter(item => item.path === '/chat').length, 1)
  assert.ok(view.text().includes('预约已取消'))
  assert.ok(!view.button('准备取消此预约'))
  await view.state.refreshHistory()
  assert.ok(view.text().includes('预约已取消'))
})

test('confirmation finishing after a patient switch stays in its original visit and is recovered from history', async t => {
  const held = deferred()
  server(t, { histories: { [childVisit.conv_id]: [message(childVisit, 'proposal', '核对孩子预约', [future(cards[2])])] },
    async confirm(id, body, { records }) {
      await held.promise
      records.get(body.conv_id).push(message(childVisit, 'receipt', '孩子预约已创建', confirmation.artifacts, { kind: 'operation_result', proposal_id: id }))
      return confirmation
    }
  })
  const view = mount(t); await settle()
  const pending = view.state.confirmProposal('proposal-example')
  await view.state.selectPatient('patient_self')
  held.resolve(); await pending; await settle()
  assert.equal(view.state.operationReceipt.value, null)
  assert.equal(view.state.currentVisit.value.conv_id, selfVisit.conv_id)
  assert.ok(!view.text().includes('孩子预约已创建'))
  await view.state.selectPatient('patient_child')
  assert.ok(view.text().includes('孩子预约已创建'))
  assert.equal(view.state.proposalStates.value['proposal-example'], 'executed')
})

test('runtime details start collapsed and retain successful and failed traces, sources and business receipts separately', async t => {
  const traces = [
    { kind: 'tool_call', tool_name: 'search_knowledge_base', agent_type: 'general', success: true, input: { query: '儿童材料' }, result_summary: { result_count: 1 },
      sources: [{ title: '儿童准备', source: 'knowledge/child.md', source_id: 'child-materials', doc_id: 'child', chunk_id: 'child:0' }],
      rewrite_error: '改写服务失败', rerank_error: '重排索引无效', recall_errors: ['一个召回失败'], partial: true },
    { kind: 'tool_call', tool_name: 'search_slots', agent_type: 'appointment', success: false, error_code: 'storage_unavailable', error: '号源读取失败', input: { date: '2026-09-17' }, result_summary: { success: false } },
    { kind: 'composition', success: false, error: '整合失败，保留主辅结果' }
  ]
  const history = [message(childVisit, 'request-with-evidence', '本次结果', [], { metadata: { request_id: 'request-u05', intent: 'visit_preparation', intent_confidence: .8,
    primary_agent: 'guidance', supporting_agents: ['appointment'], routing_reason: '材料与预约并行', routing_confidence: .9, latency_ms: 75, tool_traces: traces } }),
    message(childVisit, 'actual-business-receipt', '预约完成', confirmation.artifacts, { kind: 'operation_result', metadata: { receipt: confirmation.receipt } })]
  server(t, { histories: { [childVisit.conv_id]: history } })
  const view = mount(t); await settle()
  assert.equal(view.all().filter(item => item.props['data-trace-status'] === 'failed').length, 2)
  assert.equal(view.all().filter(item => item.props['data-trace-status'] === 'success').length, 1)
  for (const expected of ['医院信息', '就诊指引', '预约事务', '材料与预约并行', '80.0%', 'knowledge/child.md', 'child:0', '改写服务失败', '重排索引无效', '号源读取失败', '整合失败']) assert.ok(view.text().includes(expected), expected)
  for (const details of view.all().filter(item => item.type === 'details')) assert.ok(!details.props.open)
  const receipt = view.all().find(item => item.props['data-message-id'] === 'actual-business-receipt')
  assert.ok(!view.all(receipt).some(item => item.props.class === 'message-trace request-evidence'))
  assert.ok(view.all(receipt).some(item => item.props.class === 'message-trace business-receipt-detail'))
})

test('knowledge search distinguishes partial success, empty recall, semantic failure and HTTP failure without stale results', async t => {
  let result = { success: true, results: [{ title: '儿童材料', content: '携带既往资料', score: .7, source: 'knowledge/child.md', source_id: 'child-materials', doc_id: 'child', chunk_id: 'child:0' }],
    reranked: false, partial: true, rewrite_error: '改写失败', rerank_error: '重排失败', recall_errors: ['某次召回超时'] }
  server(t, { management: path => path === '/search' ? result : undefined })
  const view = mount(t); await settle(); view.state.activeView.value = 'knowledge'
  await view.state.searchKnowledge(); await Vue.nextTick()
  for (const expected of ['knowledge/child.md', 'child-materials', 'child:0', '改写失败', '重排失败', '某次召回超时', '仅部分召回成功']) assert.ok(view.text().includes(expected), expected)
  result = { success: true, results: [], reranked: false }
  await view.state.searchKnowledge(); await Vue.nextTick()
  assert.ok(view.text().includes('没有检索到相关资料'))
  result = { success: false, results: [{ content: '不能伪装为检索资料' }], error: '召回全部失败', fallback_used: true }
  await view.state.searchKnowledge(); await Vue.nextTick()
  assert.deepEqual(view.state.searchResults.value, [])
  assert.ok(view.text().includes('召回全部失败'))
  assert.ok(!view.text().includes('不能伪装为检索资料'))
  assert.ok(!view.text().includes('没有检索到相关资料'))
  result = { httpStatus: 503, detail: '知识服务尚未就绪' }
  await view.state.searchKnowledge(); await Vue.nextTick()
  assert.ok(view.text().includes('知识服务尚未就绪'))
  assert.equal(view.state.searchReport.value, null)
})

test('knowledge import sends supplied source fields and upload failures clear previous success feedback', async t => {
  let fail = false
  const backend = server(t, { management: path => {
    if (path === '/knowledge/add') return { message: '导入完成', added_chunks: 2, total_chunks: 19 }
    if (path === '/knowledge/upload') return fail ? { httpStatus: 400, detail: 'JSON 文件应为数组' } : { message: '文件导入完成', added_chunks: 1, total_chunks: 20 }
  } })
  const view = mount(t); await settle(); view.state.activeView.value = 'knowledge'
  view.state.docTitle.value = '补充材料'; view.state.docContent.value = '演示医院资料'; view.state.docSource.value = '公开就诊须知'; view.state.docSourceId.value = 'visit-materials'
  await view.state.submitKnowledge()
  assert.deepEqual(backend.calls.find(item => item.path === '/knowledge/add').body.documents, [{ title: '补充材料', content: '演示医院资料', source: '公开就诊须知', source_id: 'visit-materials' }])
  assert.ok(view.state.importNotice.value.includes('2 个片段'))
  const file = new File(['[]'], 'hospital.json', { type: 'application/json' })
  await view.state.handleUpload({ target: { files: [file], value: 'hospital.json' } })
  const upload = backend.calls.find(item => item.path === '/knowledge/upload')
  assert.equal(upload.body.get('file').name, 'hospital.json')
  assert.ok(view.state.importNotice.value.includes('1 个片段'))
  fail = true
  await view.state.handleUpload({ target: { files: [file], value: 'hospital.json' } }); await Vue.nextTick()
  assert.equal(view.state.importNotice.value, '')
  assert.ok(view.text().includes('JSON 文件应为数组'))
  assert.equal(view.state.importLoading.value, false)
})

test('Skills reload uses the existing endpoint and exposes partial parse failures and actual role metadata', async t => {
  const initial = { count: 1, errors: [], skills: [{ name: '就诊指引', agents: ['guidance'], keywords: [], description: '材料与文字路线', path: 'skills/visit_guidance/SKILL.md', enabled: true, content_chars: 100 }] }
  let updated = { count: 1, errors: ['skills/broken/SKILL.md：格式错误'], skills: [{ ...initial.skills[0], content_chars: 160 }] }
  const backend = server(t, { management: path => path === '/skills' ? initial : path === '/skills/reload' ? updated : undefined })
  const view = mount(t); await settle(); view.state.activeView.value = 'knowledge'
  await view.state.reloadSkillSet(); await Vue.nextTick()
  assert.ok(view.text().includes('160 字符') && view.text().includes('持续生效'))
  assert.ok(view.text().includes('格式错误'))
  assert.equal(view.state.skillsNotice.value, '')
  assert.equal(backend.calls.find(item => item.path === '/skills/reload').method, 'POST')
  updated = { ...updated, errors: [] }
  await view.state.reloadSkillSet(); await Vue.nextTick()
  assert.ok(view.text().includes('已重新加载 1 份能力'))
  updated = { httpStatus: 503, detail: 'Skills 服务暂不可用' }
  await view.state.reloadSkillSet(); await Vue.nextTick()
  assert.ok(view.text().includes('Skills 服务暂不可用'))
  assert.equal(view.state.skillsNotice.value, '')
})

test('monitor statistics show actual roles and tools while transport failure never claims healthy status', async t => {
  let failure = false
  server(t, { management: path => path === '/monitor' ? failure ? { httpStatus: 503, detail: '监控暂不可用' } : {
    agent_stats: { general_0: { total: 7, success_rate: .5, avg_ms: 42 }, future_role_2: { total: 0, success_rate: 1, avg_ms: 0 } }, tool_stats: { knowledge_search: { success_rate: .4, avg_latency_ms: 84, consecutive_fails: 3 } },
    active_alerts: [], suggestions: [{ title: '检索失败较多', action: '检查资料集合', priority: 2 }]
  } : undefined })
  const view = mount(t); await settle()
  for (const expected of ['医院信息', 'future_role_2', '50.0%', '40.0%', '连续失败 3', '检查资料集合']) assert.ok(view.text().includes(expected), expected)
  assert.ok(view.all().some(item => item.type === 'dt' && item.text === '医院信息'))
  assert.ok(view.all().some(item => item.type === 'dt' && item.text === 'future_role_2'))
  assert.ok(view.text().includes('当前没有活跃告警'))
  failure = true; await view.state.loadMonitor(); await Vue.nextTick()
  assert.ok(view.text().includes('监控暂不可用'))
  assert.ok(!view.text().includes('当前没有活跃告警'))
})

test('evaluation renders failure classes and sample evidence; baseline acceptance requires a reviewer and accepted status', async t => {
  let report = { timestamp: '2026-09-16T16:00:00+08:00', run_id: 'u05-report', total: 4, passed: 1, pass_rate: .25, avg_scores: { accuracy: .8 },
    judge_failures: ['judge-1'], call_failures: ['call-1'], skipped: ['skipped-1'], candidate_path: '.data/evaluation/u05-report.json', accepted_by: null,
    metadata: { model: 'fake-model', baseline_status: 'accepted', valid_quality_count: 1, business_failures: [], scope: 'isolated test' }, regressions: ['accuracy -0.10'], recommendations: ['先检查 Judge'],
    results: [{ test_id: 'passed-1', passed: true, scores: { accuracy: .8 }, detail: '有效样本', metadata: { status: 'passed' } },
      { test_id: 'judge-1', passed: false, scores: {}, detail: 'Judge 返回无效 JSON', metadata: { judge_failed: true, status: 'failed' } },
      { test_id: 'call-1', passed: false, scores: {}, detail: '模型调用超时', metadata: { call_failed: true, status: 'failed' } },
      { test_id: 'skipped-1', passed: false, scores: {}, detail: '不支持的断言', metadata: { status: 'skipped' } }] }
  const backend = server(t, { management: path => path === '/eval/run' ? report : undefined })
  const view = mount(t); await settle(); view.state.activeView.value = 'evaluation'
  await view.state.runEvaluation(); await Vue.nextTick()
  for (const expected of ['候选报告 · 尚未接受为基线', 'Judge 失败', '调用失败', '跳过', '.data/evaluation/u05-report.json', 'Judge 返回无效 JSON', '模型调用超时', '不支持的断言', '80.0%', '25.0%']) assert.ok(view.text().includes(expected), expected)
  assert.equal(view.state.baselineAccepted.value, false)
  assert.equal(view.all().find(item => item.props['data-baseline-status']).props['data-baseline-status'], 'candidate')
  view.state.failedOnly.value = true; await Vue.nextTick()
  assert.equal(view.all().filter(item => item.props['data-result-status']).length, 3)
  report = { ...report, accepted_by: '演示复核人', metadata: { ...report.metadata, baseline_status: 'candidate' } }
  await view.state.runEvaluation(); assert.equal(view.state.baselineAccepted.value, false)
  report = { ...report, metadata: { ...report.metadata, baseline_status: 'accepted' } }
  await view.state.runEvaluation(); await Vue.nextTick()
  assert.equal(view.all().find(item => item.props['data-baseline-status']).props['data-baseline-status'], 'accepted')
  assert.equal(backend.calls.filter(item => item.path === '/eval/run').length, 3)
  report = { httpStatus: 503, detail: '评测依赖不可用' }
  await view.state.runEvaluation(); await Vue.nextTick()
  assert.equal(view.state.evalData.value, null)
  assert.ok(view.text().includes('评测依赖不可用'))
  assert.ok(!view.all().some(item => item.props['data-baseline-status']))
})


test('report upload uses bound identity and survives a patient switch through server history', async t => {
  const hold = deferred()
  const card = { id: 'report-1', type: 'report_summary', data: { title: '报告整理', summary: '请核对', input_kind: 'pdf', extracted_text: 'Test 8 mg/L 4-6', observations: [{ item: 'Test', value: '8', unit: 'mg/L', reference_range: '4–6', flag: 'above', raw_line: 'Test 8 mg/L 4-6' }], warnings: [], sources: [] } }
  const backend = server(t, { management: async (path, body) => {
    if (path !== '/reports/preprocess') return undefined
    assert.equal(body.get('patient_id'), 'patient_child')
    assert.equal(body.get('conv_id'), childVisit.conv_id)
    assert.equal(body.get('user_id'), 'anonymous')
    assert.equal(body.get('file').name, 'report.pdf')
    await hold.promise
    backend.records.get(childVisit.conv_id).push(message(childVisit, 'report-result', '请核对报告', [card]))
    return { ...childVisit, visit: childVisit, artifacts: [card], response: '请核对报告' }
  } })
  const view = mount(t); await settle()
  const pending = view.state.submitReport(new File(['%PDF-test'], 'report.pdf', { type: 'application/pdf' }))
  await settle(); assert.equal(view.state.reportPending.value, true)
  await view.state.selectPatient('patient_self'); await settle()
  hold.resolve(); await pending; await settle()
  assert.equal(view.state.patientId.value, 'patient_self')
  assert.ok(!view.text().includes('Test 8 mg/L'))
  await view.state.selectPatient('patient_child'); await view.state.selectVisit(childVisit.conv_id); await settle()
  assert.ok(view.text().includes('Test 8 mg/L') && view.text().includes('高于原报告区间'))
  assert.equal(backend.calls.filter(call => call.path === '/reports/preprocess').length, 1)
  assert.equal(backend.calls.filter(call => call.path === '/chat' || call.path.includes('/confirm')).length, 0)
})
