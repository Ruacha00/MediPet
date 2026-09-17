import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import test from 'node:test'
import { parse, compileScript } from 'vue/compiler-sfc'
import { transformWithEsbuild } from 'vite'

const require = createRequire(import.meta.url)
const Vue = require('vue')
const { renderToString } = require('vue/server-renderer')
const file = new URL('../src/components/MessageContent.vue', import.meta.url)
const { descriptor } = parse(await readFile(file, 'utf8'), { filename: 'MessageContent.vue' })
const script = compileScript(descriptor, { id: 'message-content', inlineTemplate: true })
const { code } = await transformWithEsbuild(script.content, 'MessageContent.js', { format: 'cjs', target: 'esnext' })
const module = { exports: {} }
new Function('require', 'module', 'exports', code)(require, module, module.exports)
const MessageContent = module.exports.default
const render = content => renderToString(Vue.createSSRApp(MessageContent, { content }))

test('paragraphs, emphasis, nested lists and explicit line breaks preserve the supplied facts', async () => {
  const html = await render('请带**身份证**，费用 *20 元*。\n下午就诊。\n\n1. 核对患者\n2. 准备材料\n   - 原始报告\n   - 不需要空腹')
  assert.match(html, /<p>请带<strong>身份证<\/strong>，费用 <em>20 元<\/em>。<br>\n下午就诊。<\/p>/)
  assert.match(html, /<ol>/)
  assert.match(html, /<ul>\n<li>原始报告<\/li>/)
  assert.ok(html.includes('不需要空腹'))
})

test('a Markdown table renders semantic cells inside its own keyboard reachable scroll region', async () => {
  const html = await render('| 科室 | 时间 |\n| --- | --- |\n| **眼科** | 09:00–09:30 |\n| 儿科 | 10:00 |')
  assert.match(html, /class="message-table-scroll" role="region" aria-label="表格，可横向滚动" tabindex="0"><table>/)
  assert.match(html, /<th>科室<\/th>/)
  assert.match(html, /<td><strong>眼科<\/strong><\/td>/)
  assert.match(html, /<td>09:00–09:30<\/td>/)
  assert.match(html, /<\/table><\/div>/)
})

test('only explicit web links become links and are isolated from the application window', async () => {
  const html = await render('[资料](https://example.org/label?q=a&dose=200 "说明书") 与 <http://example.org/clinic>，普通文本 https://example.org/plain')
  assert.match(html, /href="https:\/\/example.org\/label\?q=a&amp;dose=200" title="说明书" target="_blank" rel="noopener noreferrer"/)
  assert.match(html, /href="http:\/\/example.org\/clinic" target="_blank" rel="noopener noreferrer"/)
  assert.equal((html.match(/<a /g) || []).length, 2)
})

for (const destination of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'javascript&#x3a;alert(1)', 'jav&#x09;ascript:alert(1)', 'data:text/html;base64,PHNjcmlwdD4=', 'vbscript:msgbox(1)', 'file:///etc/passwd', '/api/python/appointments/confirm', '//example.org/tracker', 'mailto:patient@example.org']) {
  test(`unsafe or non-web destination remains visible text: ${destination}`, async () => {
    const html = await render(`[原文链接](<${destination}>)`)
    assert.doesNotMatch(html, /<a\b|<iframe\b|<object\b/)
    assert.ok(html.includes('原文链接'))
  })
}

test('raw HTML and event handlers are displayed as text rather than executable nodes', async () => {
  const html = await render('<script>globalThis.pwned = true</script>\n\n<img src="https://example.org/leak" onerror="alert(1)">\n\n<svg onload="alert(1)"></svg>\n\n<iframe srcdoc="<script>alert(1)</script>"></iframe>')
  assert.doesNotMatch(html, /<(?:script|img|svg|iframe|object|style)\b/i)
  assert.ok(html.includes('&lt;script&gt;globalThis.pwned = true&lt;/script&gt;'))
  assert.ok(html.includes('&lt;img src=&quot;https://example.org/leak&quot;'))
})

test('Markdown images retain their descriptions without remote or inline image loading', async () => {
  const html = await render('![报告截图](https://example.org/tracker.png)\n\n![<img src=x onerror=alert(1)>](https://example.org/unsafe.svg)\n\n![内嵌](data:image/png;base64,aGVsbG8=)')
  assert.doesNotMatch(html, /<img\b|<svg\b|<[a-z][^>]*\bsrc=/i)
  assert.ok(html.includes('图片：报告截图（未加载）'))
  assert.ok(html.includes('&lt;img'))
  assert.ok(html.includes('内嵌'))
})

test('link titles and fenced code cannot escape into attributes or HTML', async () => {
  const html = await render('[安全资料](https://example.org "&quot; onmouseover=&quot;alert(1)")\n\n```html\n<img src=x onerror=alert(1)>\n```\n\n`<script>alert(1)</script>`')
  assert.match(html, /title="&quot; onmouseover=&quot;alert\(1\)" target="_blank"/)
  assert.doesNotMatch(html, /<img\b|<script\b/)
  assert.match(html, /<pre><code class="language-html">&lt;img/)
})

test('text is not typographically rewritten, truncated or shared between component instances', async () => {
  const longId = 'proposal_' + '0123456789'.repeat(70)
  const [first, second, empty] = await Promise.all([render(`剂量 0.5 mg；值 < 3；"原文" (c) -- ...\n${longId}`), render('另一位患者的文字'), render('')])
  assert.ok(first.includes('0.5 mg；值 &lt; 3；&quot;原文&quot; (c) -- ...'))
  assert.ok(first.includes(longId))
  assert.ok(!first.includes('另一位患者'))
  assert.ok(!second.includes(longId))
  assert.match(empty, /^<div class="message-content"[^>]*><\/div>$/)
})
