<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  patients: { type: Array, default: () => [] }, visits: { type: Array, default: () => [] },
  patientId: { type: String, default: '' }, currentVisit: { type: Object, default: null },
  loading: Boolean, busy: Boolean, archived: Boolean,
  error: { type: String, default: '' }, notice: { type: String, default: '' }
})
const emit = defineEmits(['patient-change', 'new-visit', 'select-visit', 'rename-visit', 'archive-visit', 'restore-visit', 'toggle-archived'])
const editing = ref('')
const titleDraft = ref('')
const activePatient = computed(() => props.patients.find(patient => patient.patient_id === (props.currentVisit?.patient_id || props.patientId)))
watch(() => props.visits, values => {
  if (values.some(visit => visit.conv_id === editing.value && visit.title === titleDraft.value.trim())) editing.value = ''
})
watch(() => props.patientId, () => { editing.value = '' })
function edit(visit) { editing.value = visit.conv_id; titleDraft.value = visit.title }
function rename() {
  if (!props.busy && titleDraft.value.trim()) emit('rename-visit', { convId: editing.value, title: titleDraft.value.trim() })
}
</script>

<template>
  <section class="patient-panel" aria-label="就诊人和事项">
    <header class="panel-heading"><span class="eyebrow">就诊空间</span><span class="demo-tag">演示</span></header>
    <label class="patient-select">选择就诊人
      <select :value="patientId" :disabled="loading || busy" @change="emit('patient-change', $event.target.value)">
        <option v-if="!patients.length" value="">{{ loading ? '正在加载…' : '暂无就诊人' }}</option>
        <option v-for="patient in patients" :key="patient.patient_id" :value="patient.patient_id">{{ patient.name }} · {{ patient.relationship === 'self' ? '本人' : '家属' }}</option>
      </select>
    </label>
    <div v-if="currentVisit" class="current-visit">
      <span class="current-mark" aria-hidden="true">{{ activePatient?.name?.slice(-1) }}</span>
      <div><small>{{ currentVisit.archived ? '已归档事项 · 恢复后可继续' : '当前事项' }}</small><strong>{{ currentVisit.title }}</strong><span>{{ activePatient?.name || currentVisit.patient_id }}</span></div>
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
          <strong>{{ visit.title }}</strong><small>{{ new Date(visit.updated_at).toLocaleDateString('zh-CN', { timeZone: 'Asia/Shanghai' }) }}{{ visit.archived ? ' · 已归档' : '' }}</small>
        </button>
        <form v-if="editing === visit.conv_id" class="rename-form" @submit.prevent="rename">
          <label>事项名称<input v-model="titleDraft" aria-label="新的事项名称" :disabled="busy" /></label>
          <div><button type="submit" :disabled="busy || !titleDraft.trim()">保存</button><button type="button" class="text-button" :disabled="busy" @click="editing = ''">取消</button></div>
        </form>
        <div v-else class="visit-actions">
          <button type="button" class="text-button" :disabled="busy" @click="edit(visit)">重命名</button>
          <button v-if="visit.archived" type="button" class="text-button restore" :disabled="busy" @click="emit('restore-visit', visit.conv_id)">恢复事项</button>
          <button v-else type="button" class="text-button" :disabled="busy" @click="emit('archive-visit', visit.conv_id)">归档</button>
        </div>
      </li>
    </ul>
    <p class="history-note">每个事项绑定一位就诊人，消息和办理记录会完整保留。</p>
  </section>
</template>

<style scoped>
.patient-panel{padding:22px 18px;min-width:0}.panel-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:26px}.eyebrow{font-size:13px;letter-spacing:.12em;color:var(--text-soft)}.demo-tag{border:1px solid var(--line-strong);color:var(--muted);font-size:11px;border-radius:4px;padding:3px 6px}.patient-select{display:grid;gap:10px;font-size:12px;color:var(--text-soft)}.patient-select select{width:100%;min-height:46px;background:var(--panel-2);color:var(--text);border:1px solid var(--line-strong);border-radius:8px;padding:0 10px;font:inherit;font-size:15px}.current-visit{display:flex;gap:12px;margin:20px 0;padding:14px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}.current-mark{width:35px;height:35px;display:grid;place-items:center;flex-shrink:0;border-radius:50%;background:var(--green-soft);color:var(--green)}.current-visit div{display:grid;gap:5px;min-width:0}.current-visit strong{font-size:14px;overflow-wrap:anywhere}.current-visit small,.current-visit div>span{font-size:11px;color:var(--text-soft)}.new-visit{width:100%;margin:16px 0;background:var(--green-soft);color:var(--green);border-color:rgba(65,201,140,.3)}.list-heading{display:flex;align-items:center;justify-content:space-between;gap:8px}.list-heading h3{font-size:13px;font-weight:500;color:var(--text-soft)}.text-button{background:transparent;color:var(--text-soft);font-size:11px;padding:0 6px;min-height:32px;font-weight:500}.text-button:hover:not(:disabled){background:var(--panel-3);box-shadow:none}.visit-list{list-style:none;padding:0;margin:8px 0}.visit-list>li{border:1px solid transparent;border-radius:8px;padding:8px;margin-bottom:8px;background:var(--panel)}.visit-list>li.selected{border-color:rgba(65,201,140,.5);background:var(--green-soft)}.visit-title{display:grid;gap:8px;text-align:left;width:100%;background:transparent;padding:8px;font-weight:500;color:var(--text);min-height:58px}.visit-title strong{font-weight:500;overflow-wrap:anywhere}.visit-title small{font-size:10px;color:var(--muted)}.visit-title:hover:not(:disabled){background:var(--panel-2);box-shadow:none}.visit-actions{display:flex;justify-content:space-between;margin-top:4px}.restore{color:var(--green)}.empty-state,.history-note,.panel-status{font-size:12px;line-height:1.8;color:var(--muted)}.history-note{border-top:1px solid var(--line);padding-top:18px;margin-top:22px}.panel-error{font-size:12px;color:var(--red);line-height:1.6;overflow-wrap:anywhere}.panel-notice{font-size:12px;color:var(--green)}.rename-form{display:grid;gap:10px;padding:8px}.rename-form label{display:grid;gap:8px;font-size:12px}.rename-form>div{display:flex;gap:8px}.rename-form button{font-size:12px}@media(max-width:640px){.patient-panel{padding:18px}.visit-list{max-height:300px;overflow:auto}.panel-heading{margin-bottom:16px}}
</style>
