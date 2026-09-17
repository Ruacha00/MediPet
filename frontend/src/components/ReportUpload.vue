<script setup>
import { ref, useId } from 'vue'
const props = defineProps({ disabled: Boolean, busy: Boolean })
const emit = defineEmits(['upload'])
const error = ref('')
const fileInput = ref(null)
const descriptionId = useId()
function openPicker() {
  if (!props.disabled && !props.busy) fileInput.value?.click()
}
function choose(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  error.value = ''
  if (!file || props.disabled || props.busy) return
  if (!/\.(pdf|png|jpe?g|webp)$/i.test(file.name)) { error.value = '请选择 PDF、PNG、JPEG 或 WebP 报告。'; return }
  if (file.size > 10 * 1024 * 1024) { error.value = '文件不能超过 10 MB。'; return }
  emit('upload', file)
}
</script>

<template>
  <div class="report-upload" :aria-busy="busy">
    <button type="button" class="report-upload-button" :disabled="disabled || busy" :aria-describedby="descriptionId" @click="openPicker">
      {{ busy ? '正在识别报告…' : '上传报告' }}
    </button>
    <input ref="fileInput" type="file" hidden aria-label="上传报告 PDF 或图片" accept=".pdf,.png,.jpg,.jpeg,.webp" :disabled="disabled || busy" @change="choose" />
    <span :id="descriptionId" class="upload-help">PDF、PNG、JPEG、WebP · 最多 10 MB<br />PDF 最多 10 页 · 本地提取，原件不保留</span>
    <span v-if="busy" class="upload-status" role="status">正在提取报告内容，请稍候。</span>
    <p v-if="error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.report-upload { display:flex; flex-wrap:wrap; align-items:center; gap:8px 12px; margin:4px 0 12px; color:var(--text-soft); }
.report-upload-button { min-height:44px; flex-shrink:0; padding:0 14px; border:1px solid var(--line-strong); border-radius:8px; color:var(--green); background:var(--panel-2); font-size:14px; font-weight:600; }
.report-upload-button:hover:not(:disabled) { background:var(--green-soft); border-color:var(--green); box-shadow:none; }
.report-upload-button:focus-visible { outline:2px solid var(--green); outline-offset:3px; }
.report-upload-button:disabled { opacity:.55; cursor:not-allowed; }
.upload-help,.upload-status,p { font-size:13px; line-height:1.5; overflow-wrap:anywhere; }
.upload-help { min-width:0; }
.upload-status,p { width:100%; margin:0; }
.upload-status { color:var(--text-soft); }
p { color:var(--red); }
</style>
