# Markdown 对话输入与展示

Type: spec
Status: resolved

## 目标

改善问答界面的输入与输出 Markdown 体验：助手消息使用成熟的开源 Markdown 渲染核心安全展示 GitHub Flavored Markdown，输入区提供常用格式操作与发送前预览，并保持现有流式消息和结构化卡片行为。

## 验收

- 文本消息支持标题、列表、行内代码和链接等 GitHub Flavored Markdown。
- 外部链接使用安全的新窗口属性，原始 HTML 不直接执行。
- 输入区支持加粗、行内代码、无序/有序列表快捷操作与 Markdown 预览。
- 桌面与移动布局保持可用，并有单元测试和浏览器烟测覆盖。

## Answer

已完成。Web 使用 `react-markdown` 与 `remark-gfm` 渲染文本消息，输入区加入格式工具栏和预览；单元测试、ESLint、生产构建及浏览器烟测均通过。
