import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import test from 'node:test'
import { parse, compileScript } from 'vue/compiler-sfc'
import { transformWithEsbuild } from 'vite'

const require = createRequire(import.meta.url)
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')

async function component(name) {
  const source = await readFile(new URL(`../src/components/${name}.vue`, import.meta.url), 'utf8')
  const { descriptor } = parse(source, { filename: name })
  const script = compileScript(descriptor, { id: name, inlineTemplate: true })
  const { code } = await transformWithEsbuild(script.content, `${name}.js`, { format: 'cjs', target: 'esnext' })
  const module = { exports: {} }
  new Function('require', 'module', 'exports', code)(require, module, module.exports)
  return module.exports.default
}
const Panel = await component('PatientVisitPanel')
const Upload = await component('ReportUpload')

// Mount compiled Vue templates and exercise their real event handlers. This host
// does not simulate browser layout or the native OS file dialog; U003-07 covers those.
function mounted(t, comp, props = {}) {
  let focused = null
  const node = (type, text = '') => ({ type, text, props: {}, children: [], parent: null, listeners: {}, clicks: 0,
    addEventListener(name, callback) { this.listeners[name] = callback },
    focus() { focused = this }, select() { this.selected = true }, click() { this.clicks += 1 } })
  const renderer = Vue.createRenderer({
    createElement: type => node(type), createText: text => node('#text', text), createComment: text => node('#comment', text),
    setText: (item, text) => { item.text = text },
    setElementText: (item, text) => { item.text = text; item.children = [] },
    patchProp: (item, key, previous, value) => { item.props[key] = value },
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
  const state = Vue.reactive(props)
  const root = node('root')
  const app = renderer.createApp({ render: () => Vue.h(comp, state) })
  app.mount(root)
  t.after(() => app.unmount())
  const all = (item = root) => [item, ...item.children.flatMap(all)]
  const text = item => item.text + item.children.map(text).join('')
  return { state, all, text, root, focused: () => focused,
    find: type => all().find(item => item.type === type),
    button: label => all().find(item => item.type === 'button' && text(item).includes(label)),
    content: () => text(root) }
}
const patients = [
  { patient_id: 'self', name: '林女士', relationship: 'self' },
  { patient_id: 'child', name: '小满', relationship: 'child' }
]
const visit = { conv_id: 'visit-child', patient_id: 'child', title: '儿童门诊准备', archived: false, updated_at: '2026-09-17T01:00:00+08:00' }
const otherVisit = { ...visit, conv_id: 'visit-other', title: '报告复查' }
const panelProps = () => ({ patients, patientId: 'child', currentVisit: visit, visits: [visit, otherVisit], busy: false, loading: false, archived: false })
const tick = async () => { await Vue.nextTick(); await Vue.nextTick() }
function typeTitle(view, value) {
  const input = view.find('input')
  input.value = value
  input.listeners.input({ target: input })
}
function submit(view) { view.find('form').props.onSubmit({ preventDefault() {} }) }
function choose(view, file) {
  const target = { files: file ? [file] : [], value: 'selected' }
  view.find('input').props.onChange({ target })
  assert.equal(target.value, '', 'same file can be selected again after a failed request')
}

test('current visit uses the bound patient and has text plus aria selection state', async t => {
  const view = mounted(t, Panel, { ...panelProps(), patientId: 'self' })
  const summary = view.all().find(item => item.props.class === 'current-visit')
  assert.match(view.text(summary), /小满/)
  assert.doesNotMatch(view.text(summary), /林女士/)
  assert.equal(view.button(visit.title).props['aria-current'], 'true')
  assert.match(view.text(view.button(visit.title)), /当前/)
  assert.equal(view.button(otherVisit.title).props['aria-current'], undefined)
  assert.equal(view.all().filter(item => item.type === 'time').length, 2)
  view.state.currentVisit = otherVisit
  await tick()
  assert.equal(view.button(otherVisit.title).props['aria-current'], 'true')
  assert.equal(view.button(visit.title).props['aria-current'], undefined)
})

test('patient, new, select, archive, restore and archive filter preserve event payloads', async t => {
  const events = []
  const view = mounted(t, Panel, { ...panelProps(), visits: [visit],
    onPatientChange: id => events.push(['patient', id]), onNewVisit: id => events.push(['new', id]),
    onSelectVisit: id => events.push(['select', id]), onArchiveVisit: id => events.push(['archive', id]),
    onRestoreVisit: id => events.push(['restore', id]), onToggleArchived: value => events.push(['filter', value]) })
  view.find('select').props.onChange({ target: { value: 'self' } })
  view.button('新建就诊事项').props.onClick()
  view.button(visit.title).props.onClick()
  view.all().find(item => item.type === 'button' && view.text(item) === '归档').props.onClick()
  view.button('查看归档').props.onClick()
  assert.deepEqual(events, [['patient', 'self'], ['new', 'child'], ['select', visit.conv_id], ['archive', visit.conv_id], ['filter', true]])
  assert.equal(view.state.currentVisit.archived, false, 'component cannot declare a server action completed')
  view.state.archived = true
  view.state.visits = [{ ...visit, archived: true }]
  view.state.currentVisit = { ...visit, archived: true }
  await tick()
  assert.match(view.content(), /已归档事项 · 恢复后可继续/)
  view.button('恢复事项').props.onClick()
  view.button('返回进行中').props.onClick()
  assert.deepEqual(events.slice(-2), [['restore', visit.conv_id], ['filter', false]])
})

test('rename focuses the field, trims input and waits for server acknowledgement', async t => {
  const events = []
  const view = mounted(t, Panel, { ...panelProps(), visits: [visit], onRenameVisit: value => events.push(value) })
  await view.button('重命名').props.onClick()
  await tick()
  assert.equal(view.focused(), view.find('input'))
  assert.equal(view.find('input').selected, true)
  typeTitle(view, '  检查结果复诊  ')
  await tick()
  submit(view)
  assert.deepEqual(events, [{ convId: visit.conv_id, title: '检查结果复诊' }])
  assert.equal(view.state.currentVisit.title, visit.title)
  assert.ok(view.find('form'))
  view.state.error = '名称未保存，请重试。'
  await tick()
  assert.ok(view.find('form'), 'failure keeps the draft editable')
  assert.match(view.content(), /名称未保存/)
  view.state.visits = [{ ...visit, title: '检查结果复诊' }]
  view.state.currentVisit = view.state.visits[0]
  view.state.error = ''
  view.state.notice = '事项名称已保存。'
  await tick()
  assert.equal(view.find('form'), undefined)
  assert.match(view.content(), /事项名称已保存/)
})

test('blank and busy renames cannot submit; patient or list changes clear editing', async t => {
  const events = []
  const view = mounted(t, Panel, { ...panelProps(), visits: [visit], onRenameVisit: value => events.push(value) })
  await view.button('重命名').props.onClick()
  typeTitle(view, '   ')
  await tick()
  assert.equal(view.button('保存').props.disabled, true)
  submit(view)
  typeTitle(view, '新的名称')
  view.state.busy = true
  await tick()
  submit(view)
  assert.deepEqual(events, [])
  assert.equal(view.find('input').props.disabled, true)
  view.state.busy = false
  view.state.patientId = 'self'
  await tick()
  assert.equal(view.find('form'), undefined)
  await view.button('重命名').props.onClick()
  view.state.archived = true
  await tick()
  assert.equal(view.find('form'), undefined)
})

test('rename cancellation and Escape discard only local draft without a server mutation', async t => {
  const events = []
  const view = mounted(t, Panel, { ...panelProps(), visits: [visit], onRenameVisit: value => events.push(value) })
  await view.button('重命名').props.onClick()
  typeTitle(view, '不会保存')
  view.button('取消').props.onClick()
  await tick()
  assert.equal(view.find('form'), undefined)
  await view.button('重命名').props.onClick()
  assert.equal(view.find('input').value, visit.title)
  view.find('input').props.onKeydown({ key: 'Escape', preventDefault() {} })
  await tick()
  assert.equal(view.find('form'), undefined)
  assert.deepEqual(events, [])
})

test('loading and errors stay distinct from successful empty lists and disable controls', async t => {
  const view = mounted(t, Panel, { ...panelProps(), loading: true, error: '暂时无法读取事项。' })
  assert.equal(view.find('section').props['aria-busy'], true)
  assert.equal(view.find('select').props.disabled, true)
  assert.equal(view.button('新建就诊事项').props.disabled, true)
  assert.equal(view.button('查看归档').props.disabled, true)
  assert.equal(view.find('ul'), undefined)
  assert.match(view.content(), /正在读取事项/)
  assert.match(view.content(), /暂时无法读取事项/)
  assert.doesNotMatch(view.content(), /新建一个事项/)
  view.state.loading = false; view.state.error = ''; view.state.visits = []; view.state.currentVisit = null
  await tick()
  assert.match(view.content(), /新建一个事项/)
  view.state.archived = true
  await tick()
  assert.match(view.content(), /还没有归档事项/)
})

test('upload has a native keyboard-focusable button that opens the actual input', async t => {
  const view = mounted(t, Upload)
  const input = view.find('input'), button = view.button('上传报告')
  assert.equal(button.props.type, 'button')
  assert.notEqual(button.props.tabindex, -1)
  assert.ok(button.props['aria-describedby'])
  button.props.onClick()
  assert.equal(input.clicks, 1)
  const html = await renderToString(Vue.createSSRApp(Upload))
  assert.match(html, /<button[^>]+type="button"/)
  assert.match(html, /PDF 最多 10 页/)
  assert.doesNotMatch(html, /正在提取|progressbar|\d+%/)
})

test('invalid format and oversized file show local errors; valid file clears them and emits unchanged', async t => {
  const events = []
  const view = mounted(t, Upload, { onUpload: file => events.push(file) })
  choose(view, new File(['bad'], 'report.svg', { type: 'image/svg+xml' }))
  await tick()
  assert.match(view.content(), /请选择 PDF、PNG、JPEG 或 WebP 报告/)
  assert.equal(view.all().find(item => item.props.role === 'alert').type, 'p')
  choose(view, { name: 'too-large.pdf', size: 10 * 1024 * 1024 + 1 })
  await tick()
  assert.match(view.content(), /文件不能超过 10 MB/)
  assert.equal(events.length, 0)
  const file = new File(['report'], 'scan.JPEG', { type: 'image/jpeg' })
  choose(view, file)
  await tick()
  assert.strictEqual(events[0], file)
  assert.equal(view.all().some(item => item.props.role === 'alert'), false)
  choose(view, { name: 'boundary.pdf', size: 10 * 1024 * 1024 })
  assert.equal(events.length, 2, 'exact 10 MiB is accepted by the existing limit')
})

test('busy and disabled block both picker opening and upload; state recovers without invented success', async t => {
  const events = []
  const view = mounted(t, Upload, { busy: true, disabled: false, onUpload: file => events.push(file) })
  const input = view.find('input'), file = new File(['report'], 'report.pdf')
  assert.equal(view.button('正在识别报告').props.disabled, true)
  assert.equal(input.props.disabled, true)
  view.button('正在识别报告').props.onClick()
  choose(view, file)
  assert.equal(input.clicks, 0)
  assert.deepEqual(events, [])
  assert.match(view.content(), /正在提取报告内容/)
  view.state.busy = false; view.state.disabled = true
  await tick()
  view.button('上传报告').props.onClick()
  choose(view, file)
  assert.equal(input.clicks, 0)
  assert.equal(events.length, 0)
  assert.doesNotMatch(view.content(), /正在提取|识别成功|100%/)
  view.state.disabled = false
  await tick()
  view.button('上传报告').props.onClick()
  assert.equal(input.clicks, 1)
  choose(view, file)
  assert.strictEqual(events[0], file)
})

test('cancelling the file dialog emits nothing and a repeated selection remains possible', async t => {
  const events = []
  const view = mounted(t, Upload, { onUpload: file => events.push(file) })
  choose(view, null)
  assert.deepEqual(events, [])
  const file = new File(['report'], 'report.webp', { type: 'image/webp' })
  choose(view, file)
  choose(view, null)
  choose(view, file)
  assert.deepEqual(events, [file, file])
})
