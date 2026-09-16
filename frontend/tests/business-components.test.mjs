import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import test from 'node:test'
import { parse, compileScript } from 'vue/compiler-sfc'
import { build, transformWithEsbuild } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const fixture = async name => JSON.parse(await readFile(new URL(`./fixtures/api/${name}.json`, import.meta.url), 'utf8'))
async function component(name) {
  const file = new URL(`../src/components/${name}.vue`, import.meta.url)
  const { descriptor } = parse(await readFile(file, 'utf8'), { filename: name })
  const script = compileScript(descriptor, { id: name, inlineTemplate: true })
  const { code } = await transformWithEsbuild(script.content, `${name}.js`, { format: 'cjs', target: 'esnext' })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', code)(require, module, module.exports)
  return module.exports.default
}
const Business = await component('BusinessArtifacts')
const Panel = await component('PatientVisitPanel')
const Health = await component('HealthArtifacts')
const ReportUpload = await component('ReportUpload')
const html = (comp, props) => renderToString(Vue.createSSRApp(comp, props))

function mounted(t, comp, props) {
  const node = (type, text = '') => ({ type, text, props: {}, children: [], parent: null, listeners: {},
    addEventListener(name, callback) { this.listeners[name] = callback } })
  const renderer = Vue.createRenderer({
    createElement: type => node(type), createText: text => node('#text', text), createComment: text => node('#comment', text),
    setText: (item, text) => { item.text = text },
    setElementText: (item, text) => { item.text = text; item.children = [] },
    patchProp: (item, key, prev, value) => { item.props[key] = value },
    insert(item, parent, anchor) {
      if (item.parent) item.parent.children = item.parent.children.filter(child => child !== item)
      item.parent = parent
      const index = anchor ? parent.children.indexOf(anchor) : -1
      if (index < 0) parent.children.push(item)
      else parent.children.splice(index, 0, item)
    },
    remove(item) { if (item.parent) item.parent.children = item.parent.children.filter(child => child !== item) },
    parentNode: item => item.parent, nextSibling: item => item.parent?.children[item.parent.children.indexOf(item) + 1] || null
  })
  const state = Vue.reactive(props)
  const root = node('root')
  const app = renderer.createApp({ render: () => Vue.h(comp, state) })
  app.mount(root)
  t.after(() => app.unmount())
  function all(item = root) { return [item, ...item.children.flatMap(child => all(child))] }
  function text(item) { return item.text + item.children.map(text).join('') }
  return { state, all, text, button: label => all().find(item => item.type === 'button' && text(item).includes(label)) }
}

test('seven artifact types render actual facts, order, source and contact-only information', async () => {
  const artifacts = await fixture('artifacts')
  const rendered = await html(Business, { artifacts })
  for (const type of new Set(artifacts.map(item => item.type))) assert.ok(rendered.includes(`data-artifact="${type}"`))
  for (const fact of ['明和虚构医院', '许知宁', '2026-09-17', '¥20.00', '林小满', '儿童就诊准备说明', '无障碍文字指引', '仅展示演示联系资料']) assert.ok(rendered.includes(fact), fact)
  assert.ok(!rendered.includes('已提交给工作人员'))
  const empty = structuredClone(artifacts[1]); empty.data.slots = []
  const noSlots = await html(Business, { artifacts: [empty], error: '未找到该地点' })
  assert.ok(noSlots.includes('没有可选号源') && noSlots.includes('未找到该地点'))
  assert.ok(!noSlots.includes('选择此号源'))
})

test('slot selection uses backend ID and confirmation emits once until server state changes', async t => {
  const artifacts = await fixture('artifacts')
  const proposal = structuredClone(artifacts[2]); proposal.data.expires_at = '2099-01-01T00:00:00+08:00'
  const selected = [], confirmed = []
  const view = mounted(t, Business, { artifacts: [artifacts[1], proposal], onSelectSlot: item => selected.push(item), onConfirmProposal: id => confirmed.push(id) })
  view.button('选择此号源').props.onClick()
  assert.deepEqual(selected, [{ slotId: 'slot-example', listId: 'list-example', index: 1 }])
  const button = view.button('确认预约')
  button.props.onClick(); button.props.onClick()
  assert.deepEqual(confirmed, ['proposal-example'])
  await Vue.nextTick()
  assert.equal(view.button('正在确认').props.disabled, true)
  view.state.confirmingId = 'proposal-example'; await Vue.nextTick()
  view.state.confirmingId = ''; view.state.error = '并发冲突，请重试'; await Vue.nextTick()
  view.button('确认预约').props.onClick()
  assert.equal(confirmed.length, 2)
})

test('executed, expired and superseded proposals cannot emit a confirmation', async () => {
  const variants = await fixture('proposal-states')
  for (const card of variants.filter(item => item.data.status !== 'pending')) {
    const rendered = await html(Business, { artifacts: [card] })
    assert.ok(rendered.includes(`data-status="${card.data.status}"`))
    assert.ok(!rendered.includes('class="confirm-button"'))
  }
  const cancel = variants.find(item => item.data.operation === 'cancel' && item.data.status === 'pending')
  cancel.data.expires_at = '2099-01-01T00:00:00+08:00'
  assert.ok((await html(Business, { artifacts: [cancel] })).includes('确认取消预约'))
})

test('patient and visit actions emit stable IDs while bound patient follows server visit', async t => {
  const patients = (await fixture('patients')).items, visit = await fixture('visit')
  const events = []
  const view = mounted(t, Panel, { patients, patientId: 'patient_self', visits: [visit], currentVisit: visit,
    onPatientChange: id => events.push(['patient', id]), onNewVisit: id => events.push(['new', id]),
    onSelectVisit: id => events.push(['select', id]), onArchiveVisit: id => events.push(['archive', id]),
    onRestoreVisit: id => events.push(['restore', id]), onRenameVisit: value => events.push(['rename', value]) })
  view.all().find(item => item.type === 'select').props.onChange({ target: { value: 'patient_child' } })
  view.button('新建就诊事项').props.onClick()
  view.button('儿童门诊事项').props.onClick()
  view.all().find(item => item.type === 'button' && view.text(item) === '归档').props.onClick()
  assert.ok(view.text(view.all().find(item => item.props.class === 'current-visit')).includes('林小满'))
  assert.deepEqual(events.slice(0, 3), [['patient', 'patient_child'], ['new', 'patient_self'], ['select', 'visit-example']])
  assert.deepEqual(events[3], ['archive', 'visit-example'])
  view.button('重命名').props.onClick(); await Vue.nextTick()
  const input = view.all().find(item => item.type === 'input')
  input.value = '新的标题'; input.listeners.input({ target: input }); await Vue.nextTick()
  view.all().find(item => item.type === 'form').props.onSubmit({ preventDefault() {} })
  assert.deepEqual(events.at(-1), ['rename', { convId: 'visit-example', title: '新的标题' }])
  assert.equal(view.state.currentVisit.title, '儿童门诊事项')
  view.state.error = '标题保存失败'; await Vue.nextTick()
  assert.ok(view.text(view.all()[0]).includes('标题保存失败'))
  view.state.visits = [{ ...visit, archived: true }]; view.state.currentVisit = { ...visit, archived: true }
  view.button('取消').props.onClick(); await Vue.nextTick()
  view.button('恢复事项').props.onClick()
  assert.deepEqual(events.at(-1), ['restore', 'visit-example'])
})

test('loading and errors are not rendered as successful empty lists', async () => {
  const rendered = await html(Panel, { loading: true, error: '事项暂时不可用' })
  assert.ok(rendered.includes('正在读取事项') && rendered.includes('事项暂时不可用'))
  assert.ok(!rendered.includes('新建一个事项，开始'))
  assert.ok((await html(Panel, { patientId: 'patient_child' })).includes('新建一个事项，开始'))
})

test('both standalone Vue components bundle with their scoped styles', async () => {
  const output = await build({ configFile: false, logLevel: 'silent', plugins: [vue()],
    build: { write: false, lib: { entry: {
      BusinessArtifacts: fileURLToPath(new URL('../src/components/BusinessArtifacts.vue', import.meta.url)),
      PatientVisitPanel: fileURLToPath(new URL('../src/components/PatientVisitPanel.vue', import.meta.url))
    }, formats: ['es'] }, rollupOptions: { external: ['vue'] } } })
  const assets = (Array.isArray(output) ? output : [output]).flatMap(bundle => bundle.output)
  assert.ok(assets.some(item => item.type === 'asset' && item.fileName.endsWith('.css')))
  assert.equal(assets.filter(item => item.type === 'chunk' && item.isEntry).length, 2)
})

test('health cards show actual fields, flags, original text and safely linked sources', async () => {
  const sources = [{ source_id: 'fact', title: '公开资料', url: 'https://example.org/medicine', reviewed_at: '2026-09-16' }]
  const rendered = await html(Health, { artifacts: [
    { id: 'triage', type: 'triage_guidance', data: { title: '科室参考', summary: '仅为初步建议', recommended_departments: [{ department_id: 'dep', name: '内科', reason: '依据描述' }], missing_information: ['持续多久？'], sources } },
    { id: 'med', type: 'medication_info', data: { title: '药品信息', drug_name: '演示药名', formulation: '片剂', summary: '公开说明', sections: [{ heading: '禁忌', items: ['需核对说明书'] }], sources } },
    { id: 'report', type: 'report_summary', data: { title: '报告整理', summary: '核对原文', input_kind: 'image', extracted_text: 'Test 8 mg/L 4-6\n<script>bad()</script>', observations: [{ item: 'Test', value: '8', unit: 'mg/L', reference_range: '4–6', flag: 'above', raw_line: 'Test 8 mg/L 4-6' }], warnings: ['不推断疾病'], sources: [{ ...sources[0], url: 'javascript:bad()' }] } }
  ] })
  for (const text of ['triage_guidance', 'medication_info', 'report_summary', '内科', '持续多久', '演示药名', '片剂', '需核对说明书', '8 mg/L', '4–6', '高于原报告区间', 'Test 8 mg/L 4-6', '不推断疾病', '2026-09-16']) assert.ok(rendered.includes(text), text)
  assert.ok(rendered.includes('data-report-flag="above"'))
  assert.ok(rendered.includes('https://example.org/medicine'))
  assert.ok(!rendered.includes('href="javascript:') && !rendered.includes('<script>bad()'))
})

test('report picker emits a valid file once, rejects extension/size, and respects busy state', async t => {
  const events = []
  const view = mounted(t, ReportUpload, { onUpload: file => events.push(file), busy: false })
  const change = file => view.all().find(item => item.type === 'input').props.onChange({ target: { files: [file], value: 'selected' } })
  change({ name: 'report.exe', size: 100 }); await Vue.nextTick()
  assert.equal(events.length, 0)
  change({ name: 'large.pdf', size: 10 * 1024 * 1024 + 1 }); await Vue.nextTick()
  assert.equal(events.length, 0)
  const file = { name: 'scan.png', size: 100 }
  change(file); assert.deepEqual(events, [file])
  view.state.busy = true; await Vue.nextTick(); change(file)
  assert.equal(events.length, 1)
})
