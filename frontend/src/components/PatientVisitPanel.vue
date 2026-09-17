<script setup>
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  patients: { type: Array, default: () => [] }, visits: { type: Array, default: () => [] },
  patientId: { type: String, default: '' }, currentVisit: { type: Object, default: null },
  loading: Boolean, busy: Boolean, archived: Boolean,
  error: { type: String, default: '' }, notice: { type: String, default: '' }
})
const emit = defineEmits(['patient-change', 'new-visit', 'select-visit', 'rename-visit', 'archive-visit', 'restore-visit', 'toggle-archived'])
const editing = ref('')
const titleDraft = ref('')
let renameInput = null
const activePatient = computed(() => props.patients.find(patient => patient.patient_id === (props.currentVisit?.patient_id || props.patientId)))
watch(() => props.visits, values => {
  if (values.some(visit => visit.conv_id === editing.value && visit.title === titleDraft.value.trim())) editing.value = ''
})
watch(() => props.patientId, () => { editing.value = '' })
watch(() => props.archived, () => { editing.value = '' })
async function edit(visit) {
  if (props.loading || props.busy) return
  editing.value = visit.conv_id
  titleDraft.value = visit.title
  await nextTick()
  renameInput?.focus?.()
  renameInput?.select?.()
}
function setRenameInput(element) { renameInput = element }
function rename() {
  if (!props.loading && !props.busy && editing.value && titleDraft.value.trim()) {
    emit('rename-visit', { convId: editing.value, title: titleDraft.value.trim() })
  }
}
</script>

<template>
  <section class="patient-panel" aria-label="就诊人和事项" :aria-busy="loading || busy">
    <label class="patient-select">选择就诊人
      <select :value="patientId" :disabled="loading || busy" @change="emit('patient-change', $event.target.value)">
        <option v-if="!patients.length" value="">{{ loading ? '正在加载…' : '暂无就诊人' }}</option>
        <option v-for="patient in patients" :key="patient.patient_id" :value="patient.patient_id">{{ patient.name }} · {{ patient.relationship === 'self' ? '本人' : '家属' }}</option>
      </select>
    </label>
    <div v-if="currentVisit" class="current-visit">
      <div><small>{{ currentVisit.archived ? '已归档事项 · 恢复后可继续' : '当前事项' }} · {{ activePatient?.name || currentVisit.patient_id }}</small><strong>{{ currentVisit.title }}</strong></div>
    </div>
    <button class="new-visit" type="button" :disabled="loading || busy || !patientId" @click="emit('new-visit', patientId)">＋ 新建就诊事项</button>
    <div class="list-heading"><h3>{{ archived ? '已归档' : '就诊事项' }}</h3><button type="button" class="text-button" :disabled="busy || loading" @click="emit('toggle-archived', !archived)">{{ archived ? '返回进行中' : '查看归档' }}</button></div>
    <p v-if="loading" class="panel-status" role="status">正在读取事项…</p>
    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="panel-notice" role="status">{{ notice }}</p>
    <p v-if="!loading && !error && !visits.length" class="empty-state">{{ archived ? '还没有归档事项。' : '新建一个事项，开始本次就诊准备。' }}</p>
    <ul v-if="!loading" class="visit-list">
      <li v-for="visit in visits" :key="visit.conv_id" :class="{ selected: currentVisit?.conv_id === visit.conv_id }">
        <button type="button" class="visit-title" :disabled="busy" :aria-current="currentVisit?.conv_id === visit.conv_id ? 'true' : undefined" @click="emit('select-visit', visit.conv_id)">
          <strong>{{ visit.title }}</strong><span class="visit-meta"><time :datetime="visit.updated_at">{{ new Date(visit.updated_at).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' }) }}</time><span v-if="currentVisit?.conv_id === visit.conv_id" class="selected-label">当前</span><span v-if="visit.archived">已归档</span></span>
        </button>
        <form v-if="editing === visit.conv_id" class="rename-form" @submit.prevent="rename">
          <label>事项名称<input :ref="setRenameInput" v-model="titleDraft" aria-label="新的事项名称" :disabled="busy" @keydown.esc.prevent="editing = ''" /></label>
          <div><button type="submit" :disabled="busy || !titleDraft.trim()">保存</button><button type="button" class="text-button" :disabled="busy" @click="editing = ''">取消</button></div>
        </form>
        <div v-else class="visit-actions">
          <button type="button" class="text-button" :disabled="busy" @click="edit(visit)">重命名</button>
          <button v-if="visit.archived" type="button" class="text-button restore" :disabled="busy" @click="emit('restore-visit', visit.conv_id)">恢复事项</button>
          <button v-else type="button" class="text-button" :disabled="busy" @click="emit('archive-visit', visit.conv_id)">归档</button>
        </div>
      </li>
    </ul>
    <p class="history-note">事项按就诊人分别保存，归档后仍保留完整记录。</p>
  </section>
</template>

<style scoped>
.patient-panel { min-width:0; padding:18px 14px; }
.patient-select { display:grid; gap:8px; font-size:13px; color:var(--text-soft); }
.patient-select select { width:100%; min-height:44px; padding:0 10px; border:1px solid var(--line-strong); border-radius:8px; background:var(--panel-2); color:var(--text); font:inherit; font-size:15px; }
.current-visit { margin:14px 0 0; padding-left:10px; border-left:2px solid var(--green); }
.current-visit div { display:grid; gap:4px; min-width:0; }
.current-visit strong { font-size:15px; font-weight:600; overflow-wrap:anywhere; }
.current-visit small { font-size:13px; line-height:1.5; color:var(--text-soft); overflow-wrap:anywhere; }
.patient-panel button { min-height:44px; }
.new-visit { width:100%; margin:14px 0 8px; background:var(--green-soft); color:var(--green); border-color:rgba(65,201,140,.3); font-size:14px; }
.list-heading { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.list-heading h3 { margin:0; font-size:14px; font-weight:600; color:var(--text-soft); }
.text-button { background:transparent; color:var(--text-soft); font-size:13px; padding:0 8px; font-weight:500; }
.text-button:hover:not(:disabled) { background:var(--panel-3); box-shadow:none; }
.visit-list { list-style:none; padding:0; margin:4px 0 0; }
.visit-list>li { border:1px solid var(--line); border-radius:8px; padding:4px; margin-bottom:8px; background:var(--panel); }
.visit-list>li.selected { border-color:var(--green); background:var(--green-soft); }
.visit-title { display:grid; gap:6px; text-align:left; width:100%; background:transparent; padding:8px; font-weight:500; color:var(--text); }
.visit-title strong { font-size:15px; line-height:1.5; font-weight:500; overflow-wrap:anywhere; }
.visit-meta { display:flex; align-items:center; flex-wrap:wrap; gap:6px 10px; color:var(--text-soft); font-size:13px; }
.selected-label { color:var(--green); font-weight:600; }
.visit-title:hover:not(:disabled) { background:var(--panel-2); box-shadow:none; }
.visit-actions { display:flex; justify-content:space-between; gap:8px; }
.restore { color:var(--green); }
.empty-state,.history-note,.panel-status,.panel-error,.panel-notice { font-size:13px; line-height:1.65; color:var(--text-soft); overflow-wrap:anywhere; }
.history-note { margin:14px 0 0; padding-top:12px; border-top:1px solid var(--line); }
.panel-error { color:var(--red); }
.panel-notice { color:var(--green); }
.rename-form { display:grid; gap:8px; padding:8px; }
.rename-form label { display:grid; gap:6px; font-size:13px; }
.rename-form input { min-height:44px; font-size:15px; }
.rename-form>div { display:flex; gap:8px; }
.rename-form button { font-size:13px; }
.patient-panel :is(button,select,input):focus-visible { outline:2px solid var(--green); outline-offset:2px; }
@media(max-width:640px) { .patient-panel { padding:16px; } }
</style>
