<script setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'

const props = defineProps({ content: { type: String, required: true } })

// Render only parser-generated markup. Do not enable raw HTML or HTML-producing plugins.
const markdown = new MarkdownIt({ html: false, breaks: true, linkify: false, typographer: false })
markdown.validateLink = (href) => {
  if (!/^https?:\/\//i.test(href) || /[\u0000-\u0020\u007f]/.test(href)) return false
  try {
    const url = new URL(href)
    return ['http:', 'https:'].includes(url.protocol) && Boolean(url.hostname)
  } catch {
    return false
  }
}
markdown.renderer.rules.link_open = (tokens, index, options, _env, renderer) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return renderer.renderToken(tokens, index, options)
}
// Keep the supplied description, but never create an image or a network-loading element.
markdown.renderer.rules.image = (tokens, index) => {
  const description = markdown.utils.escapeHtml(tokens[index].content || '无文字说明')
  return `<span class="message-image-placeholder">图片：${description}（未加载）</span>`
}
markdown.renderer.rules.table_open = (tokens, index, options, _env, renderer) => (
  '<div class="message-table-scroll" role="region" aria-label="表格，可横向滚动" tabindex="0">'
  + renderer.renderToken(tokens, index, options)
)
markdown.renderer.rules.table_close = () => '</table></div>\n'

const renderedContent = computed(() => markdown.render(props.content))
</script>

<template>
  <div class="message-content" v-html="renderedContent"></div>
</template>

<style scoped>
.message-content {
  min-width: 0;
  max-width: 100%;
  font-size: 1rem;
  line-height: 1.7;
  white-space: normal;
  overflow-wrap: anywhere;
}
.message-content :deep(p) { margin: .65rem 0; white-space: normal; }
.message-content :deep(> :first-child) { margin-top: 0; }
.message-content :deep(> :last-child) { margin-bottom: 0; }
.message-content :deep(ul), .message-content :deep(ol) { margin: .65rem 0; padding-left: 1.5rem; }
.message-content :deep(li + li) { margin-top: .25rem; }
.message-content :deep(h1), .message-content :deep(h2), .message-content :deep(h3),
.message-content :deep(h4), .message-content :deep(h5), .message-content :deep(h6) {
  margin: 1rem 0 .5rem;
  font-size: 1.05em;
  line-height: 1.5;
}
.message-content :deep(a) { color: var(--green, #41c98c); text-decoration: underline; text-underline-offset: .2em; }
.message-content :deep(a:focus-visible), .message-content :deep(.message-table-scroll:focus-visible) {
  outline: 2px solid var(--green, #41c98c);
  outline-offset: 3px;
}
.message-content :deep(blockquote) { margin: .75rem 0; padding-left: .8rem; border-left: 3px solid var(--line-strong, #4b5262); }
.message-content :deep(code) { font-size: .9em; padding: .1em .3em; border-radius: 4px; background: var(--panel-2, #1a1d26); }
.message-content :deep(pre) { max-width: 100%; padding: .75rem; overflow-x: auto; background: var(--panel-2, #1a1d26); border-radius: 8px; }
.message-content :deep(pre code) { padding: 0; white-space: pre; overflow-wrap: normal; }
.message-content :deep(.message-table-scroll) { max-width: 100%; overflow-x: auto; margin: .75rem 0; border: 1px solid var(--line-strong, #4b5262); border-radius: 8px; }
.message-content :deep(table) { width: max-content; min-width: 100%; border-collapse: collapse; }
.message-content :deep(th), .message-content :deep(td) { max-width: 26rem; padding: .55rem .75rem; text-align: left; vertical-align: top; border-bottom: 1px solid var(--line, #343b48); }
.message-content :deep(th) { background: var(--panel-2, #1a1d26); font-weight: 700; }
.message-content :deep(tr:last-child td) { border-bottom: 0; }
.message-content :deep(.message-image-placeholder) { color: var(--text-soft, #b0b7c7); }
</style>
