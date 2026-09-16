<script setup>
import { ref } from 'vue'
const props = defineProps({ disabled: Boolean, busy: Boolean })
const emit = defineEmits(['upload'])
const error = ref('')
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
  <div class="report-upload">
    <label class="report-upload-button" :class="{ disabled: disabled || busy }">
      {{ busy ? '正在识别报告…' : '上传报告 PDF / 图片' }}
      <input type="file" aria-label="上传报告 PDF 或图片" accept=".pdf,.png,.jpg,.jpeg,.webp" :disabled="disabled || busy" @change="choose" />
    </label>
    <span>本地提取 · 最多 10 MB / 10 页 · 原件不保留</span>
    <p v-if="error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.report-upload { display:flex; flex-wrap:wrap; align-items:center; gap:.6rem; margin:.2rem 0 .8rem; font-size:.75rem; color:var(--text-soft); }
.report-upload-button { cursor:pointer; border:1px solid var(--line-strong); border-radius:8px; padding:.45rem .65rem; color:var(--green); background:var(--panel-2); font-weight:600; }
.report-upload-button.disabled { opacity:.5; cursor:default; }
input { display:none; }
p { width:100%; margin:0; color:var(--red); }
</style>
