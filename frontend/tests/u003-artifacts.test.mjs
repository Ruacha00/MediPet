import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import test from 'node:test'
import { parse, compileScript } from 'vue/compiler-sfc'
import { transformWithEsbuild } from 'vite'

const require = createRequire(import.meta.url)
const Vue = require('vue')
const fixture = JSON.parse(await readFile(new URL('./fixtures/api/artifacts.json', import.meta.url), 'utf8'))
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
const Health = await component('HealthArtifacts')

function mount(t, comp, props) {
  const node = (type, text = '') => ({ type, text, props: {}, children: [], parent: null })
  const renderer = Vue.createRenderer({
    createElement: type => node(type), createText: text => node('#text', text), createComment: text => node('#comment', text),
    setText: (item, text) => { item.text = text },
    setElementText: (item, text) => { item.text = text; item.children = [] },
    patchProp: (item, key, old, value) => { item.props[key] = value },
    insert(item, parent, anchor) {
      if (item.parent) item.parent.children = item.parent.children.filter(child => child !== item)
      item.parent = parent
      const index = anchor ? parent.children.indexOf(anchor) : -1
      if (index < 0) parent.children.push(item)
      else parent.children.splice(index, 0, item)
    },
    remove(item) { if (item.parent) item.parent.children = item.parent.children.filter(child => child !== item) },
    parentNode: item => item.parent,
    nextSibling: item => item.parent?.children[item.parent.children.indexOf(item) + 1] || null
  })
  const state = Vue.reactive(props), root = node('root')
  const app = renderer.createApp({ render: () => Vue.h(comp, state) })
  app.mount(root)
  t.after(() => app.unmount())
  const all = (item = root) => [item, ...item.children.flatMap(all)]
  const text = item => item.type === '#comment' ? '' : item.text + item.children.map(text).join('')
  const visible = item => {
    if (item.type === '#comment') return ''
    const children = item.type === 'details' && !item.props.open
      ? item.children.filter(child => child.type === 'summary') : item.children
    return item.text + children.map(visible).join('')
  }
  return { state, root, all, text, visible,
    button: label => all().find(item => item.type === 'button' && text(item).includes(label)) }
}
function proposal(overrides = {}) {
  const card = structuredClone(fixture.find(item => item.type === 'appointment_proposal'))
  Object.assign(card.data, { expires_at: '2099-01-01T00:00:00+08:00' }, overrides)
  return card
}
const source = { source_id: 'reference', title: '公开说明书', url: 'https://example.org/reference', reviewed_at: '2026-09-17' }
function report(overrides = {}) {
  return { id: 'report', type: 'report_summary', data: { title: '报告整理', summary: '仅对照原报告范围', input_kind: 'image',
    observations: [{ item: 'CRP', value: '8', unit: 'mg/L', reference_range: '0–5', flag: 'above', raw_line: 'CRP 8 mg/L 0-5' }],
    warnings: ['OCR 内容待与原件核对'], extracted_text: 'CRP 8 mg/L 0-5\n完整历史原文不会删除', sources: [source], ...overrides } }
}

test('pending proposal keeps all decision fields visible and its identifiers under closed details', t => {
  const view = mount(t, Business, { artifacts: [proposal()] })
  const visible = view.visible(view.root)
  for (const fact of ['林小满', '许知宁', '儿科', '2026-09-17', '09:00—11:00', '门诊楼二层儿科', '¥20.00', '待确认', '确认预约']) assert.ok(visible.includes(fact), fact)
  for (const id of ['visit-example', 'proposal-example']) assert.ok(!visible.includes(id), id)
  const details = view.all().find(item => item.type === 'details')
  assert.equal(details.props.open, undefined)
  assert.ok(view.text(details).includes('visit-example') && view.text(details).includes('proposal-example'))
  details.props.open = true // Native details exposes the already rendered full record.
  assert.ok(view.visible(view.root).includes('proposal-example'))
})

test('confirmation emits the stable proposal ID once and authoritative execution removes the action', async t => {
  const emitted = []
  const card = proposal()
  const view = mount(t, Business, { artifacts: [card], proposalStates: {}, onConfirmProposal: id => emitted.push(id) })
  const click = view.button('确认预约').props.onClick
  click(); click()
  assert.deepEqual(emitted, ['proposal-example'])
  await Vue.nextTick()
  assert.equal(view.button('正在确认').props.disabled, true)
  view.state.proposalStates = { 'proposal-example': 'executed' }
  await Vue.nextTick()
  assert.equal(view.button('确认预约'), undefined)
  click()
  assert.equal(emitted.length, 1)
  assert.ok(view.visible(view.root).includes('已执行'))
})

for (const [status, label] of [['expired', '已过期'], ['superseded', '已更换方案'], ['cancelled', '已取消'], ['checking', '结果待核对']]) {
  test(`${status} is explicit and a stale click cannot execute the proposal`, async t => {
    const emitted = []
    const view = mount(t, Business, { artifacts: [proposal()], onConfirmProposal: id => emitted.push(id) })
    const stale = view.button('确认预约').props.onClick
    view.state.proposalStates = { 'proposal-example': status }
    await Vue.nextTick()
    assert.ok(view.visible(view.root).includes(label))
    assert.equal(view.button('确认预约'), undefined)
    stale()
    assert.deepEqual(emitted, [])
    if (status === 'checking') assert.ok(view.visible(view.root).includes('刷新记录查看是否已有回执'))
  })
}

test('a proposal expiring while the card is mounted loses its confirmation action', async t => {
  let now = Date.parse('2026-09-17T09:00:00+08:00')
  t.mock.method(Date, 'now', () => now)
  const emitted = []
  const view = mount(t, Business, { artifacts: [proposal({ expires_at: '2026-09-17T09:00:01+08:00' })], onConfirmProposal: id => emitted.push(id) })
  const stale = view.button('确认预约').props.onClick
  now += 2000
  await new Promise(resolve => setTimeout(resolve, 1100))
  await Vue.nextTick()
  assert.ok(view.visible(view.root).includes('已过期'))
  stale()
  assert.deepEqual(emitted, [])
})

test('busy and disabled states block actions; cancellation still requires its own explicit confirmation', async t => {
  const emitted = []
  const view = mount(t, Business, { artifacts: [proposal({ operation: 'cancel', target_id: 'appointment-example' })],
    busy: true, disabled: false, onConfirmProposal: id => emitted.push(id) })
  view.button('确认取消预约').props.onClick()
  assert.deepEqual(emitted, [])
  view.state.busy = false; view.state.disabled = true; await Vue.nextTick()
  view.button('确认取消预约').props.onClick()
  assert.deepEqual(emitted, [])
  view.state.disabled = false; await Vue.nextTick()
  assert.ok(view.visible(view.root).includes('点击确认后才会取消'))
  view.button('确认取消预约').props.onClick()
  assert.deepEqual(emitted, ['proposal-example'])
})

test('available slots emit the unchanged payload and exhausted slots show a real non-actionable state', async t => {
  const card = structuredClone(fixture.find(item => item.type === 'slot_list'))
  const selected = []
  const view = mount(t, Business, { artifacts: [card], onSelectSlot: value => selected.push(value) })
  const stale = view.button('选择此号源').props.onClick
  stale()
  assert.deepEqual(selected, [{ slotId: 'slot-example', listId: 'list-example', index: 1 }])
  view.state.artifacts[0].data.slots[0].remaining = 0
  await Vue.nextTick()
  assert.equal(view.button('暂无余号').props.disabled, true)
  assert.ok(view.visible(view.root).includes('当前号源已满'))
  stale()
  assert.equal(selected.length, 1)
})

test('history cards retain every supplied record and current operation error is displayed once', t => {
  const record = structuredClone(fixture.find(item => item.type === 'appointment_record'))
  const cancelled = structuredClone(record)
  cancelled.id = 'cancelled-history'; cancelled.data.status = 'cancelled'; cancelled.data.cancelled_at = '2026-09-17T10:00:00+08:00'
  const view = mount(t, Business, { artifacts: [proposal({ status: 'executed' }), record, cancelled], error: '当前操作需核对' })
  assert.equal(view.all().filter(item => item.type === 'article').length, 3)
  assert.equal(view.all().filter(item => item.props.role === 'alert').length, 1)
  assert.ok(view.visible(view.root).includes('预约有效') && view.visible(view.root).includes('此预约已取消'))
  assert.equal(view.button('确认预约'), undefined)
})

test('report values, flags, warnings and source stay visible while full OCR and raw lines remain expandable', async t => {
  const view = mount(t, Health, { artifacts: [report()] })
  const visible = view.visible(view.root)
  for (const value of ['CRP', '8 mg/L', '0–5', '高于原报告区间', 'OCR 内容待与原件核对', '公开说明书', '2026-09-17']) assert.ok(visible.includes(value), value)
  assert.ok(!visible.includes('完整历史原文不会删除'))
  const raw = view.all().find(item => item.props.class === 'extracted-text')
  assert.equal(raw.props.open, undefined)
  assert.ok(view.text(raw).includes('完整历史原文不会删除'))
  view.state.artifacts = [report({ observations: [{ item: 'CRP', value: '8', flag: 'unassessed', raw_line: 'CRP 8' }] })]
  await Vue.nextTick()
  assert.ok(view.visible(view.root).includes('待核对'))
  assert.ok(view.visible(view.root).includes('原参考区间未提供'))
  assert.ok(!view.visible(view.root).includes('高于原报告区间'))
})

test('health sources remain safe links and triage/medication facts survive reactive updates', async t => {
  const triage = { id: 'triage', type: 'triage_guidance', data: { title: '科室参考', summary: '请补充信息',
    recommended_departments: [{ department_id: 'dep', name: '内科', reason: '依据描述' }], missing_information: ['症状持续多久？'], sources: [source] } }
  const medication = { id: 'medicine', type: 'medication_info', data: { title: '药品说明', drug_name: '布洛芬', formulation: '普通片',
    sections: [{ heading: '相互作用', items: ['与华法林同用前咨询医生'] }], sources: [{ ...source, source_id: 'bad', url: 'javascript:alert(1)' }] } }
  const view = mount(t, Health, { artifacts: [triage, medication] })
  for (const fact of ['内科', '症状持续多久', '布洛芬', '普通片', '相互作用', '华法林']) assert.ok(view.visible(view.root).includes(fact), fact)
  const links = view.all().filter(item => item.type === 'a')
  assert.equal(links.length, 1)
  assert.equal(links[0].props.href, source.url)
  assert.equal(links[0].props.rel, 'noopener noreferrer')
  view.state.artifacts = [report({ observations: [], sources: [] })]
  await Vue.nextTick()
  assert.ok(view.visible(view.root).includes('尚无可比对的项目'))
  assert.ok(!view.visible(view.root).includes('布洛芬'))
})
