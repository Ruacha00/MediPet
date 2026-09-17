<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

const props = defineProps({
  artifacts: { type: Array, default: () => [] }, busy: Boolean,
  confirmingId: { type: String, default: '' }, proposalStates: { type: Object, default: () => ({}) },
  error: { type: String, default: '' }, disabled: Boolean
})
const emit = defineEmits(['select-slot', 'confirm-proposal'])
const now = ref(Date.now())
const clicked = ref('')
let ticker
onMounted(() => { ticker = setInterval(() => { now.value = Date.now() }, 1000) })
onUnmounted(() => clearInterval(ticker))
watch(() => [props.confirmingId, props.error, props.proposalStates], () => { if (!props.confirmingId) clicked.value = '' })
const cards = computed(() => props.artifacts.filter(item => item && item.data))
const stateLabels = { pending: '待确认', checking: '结果待核对', executed: '已执行', expired: '已过期', superseded: '已更换方案', active: '预约有效', cancelled: '已取消' }
const stateExplanations = {
  checking: '办理结果待核对，请刷新记录查看是否已有回执；核对前不要重复提交。',
  executed: '操作已完成，请查看对应预约记录。',
  expired: '此确认资料已过期，请重新查询并准备方案。',
  superseded: '已更换为其他方案，请核对最新确认资料。',
  cancelled: '此方案已取消，不能继续确认。'
}
const money = value => `¥${(Number(value) / 100).toFixed(2)}`
const dateTime = value => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
function proposalStatus(proposal) {
  const status = props.proposalStates[proposal.proposal_id] || proposal.status
  return status === 'pending' && Date.parse(proposal.expires_at) <= now.value ? 'expired' : status
}
function confirm(proposal) {
  if (props.disabled || props.busy || props.confirmingId || clicked.value || proposalStatus(proposal) !== 'pending') return
  clicked.value = proposal.proposal_id
  emit('confirm-proposal', proposal.proposal_id)
}
function select(slot, listId, index) {
  if (props.disabled || props.busy || slot.remaining <= 0) return
  emit('select-slot', { slotId: slot.slot_id, listId, index: index + 1 })
}
</script>

<template>
  <div class="business-artifacts">
    <p v-if="error" class="card-error" role="alert">{{ error }}</p>
    <article v-for="card in cards" :key="card.id" class="business-card" :class="{ 'settled-card': card.type === 'appointment_proposal' && proposalStatus(card.data) !== 'pending' }" :data-artifact="card.type">
      <template v-if="card.type === 'catalog'">
        <p class="eyebrow">医院公开资料</p>
        <div v-for="item in card.data.items" :key="item.hospital_id || item.department_id || item.doctor_id || item.location_id" class="catalog-item">
          <h3>{{ item.name }} <small v-if="item.title">{{ item.title }}</small></h3>
          <p>{{ item.description || item.introduction }}</p>
          <p v-if="item.address">{{ item.address }}</p>
          <p v-if="item.outpatient_hours">门诊时间 · {{ item.outpatient_hours }}</p>
          <p v-if="item.building">{{ item.building }} · {{ item.floor }}</p>
          <small v-if="item.source" class="source">来源 · {{ item.source.title }}</small>
        </div>
      </template>
      <template v-else-if="card.type === 'slot_list'">
        <header><h3>可选号源</h3><small>{{ dateTime(card.data.queried_at) }} 查询</small></header>
        <p v-if="!card.data.slots.length" class="empty-state">当前条件下没有可选号源，请调整日期或时段。</p>
        <ol v-else class="slots">
          <li v-for="(slot, index) in card.data.slots" :key="slot.slot_id">
            <div><span class="slot-number">{{ String(index + 1).padStart(2, '0') }}</span><strong>{{ slot.department_name }} · {{ slot.doctor_name }}</strong>
              <p>{{ slot.date }} · {{ slot.period === 'morning' ? '上午' : '下午' }} {{ slot.start_time }}—{{ slot.end_time }}</p>
              <small>{{ slot.hospital_name }} · {{ slot.location_name }}</small>
              <p class="availability" :class="{ 'sold-out': slot.remaining <= 0 }">{{ slot.remaining > 0 ? `剩余 ${slot.remaining} 个号源` : '当前号源已满，请选择其他时段' }}</p></div>
            <div class="slot-action"><strong>{{ money(slot.fee_fen) }}</strong><button type="button" :disabled="disabled || busy || slot.remaining <= 0" @click="select(slot, card.data.list_id, index)">{{ slot.remaining <= 0 ? '暂无余号' : '选择此号源' }}</button></div>
          </li>
        </ol>
      </template>
      <template v-else-if="card.type === 'appointment_proposal' || card.type === 'appointment_record'">
        <header><p class="eyebrow">{{ card.type === 'appointment_record' ? '预约记录' : card.data.operation === 'cancel' ? '取消预约确认资料' : '预约确认资料' }}</p>
          <span class="status" :data-status="card.type === 'appointment_proposal' ? proposalStatus(card.data) : card.data.status">{{ stateLabels[card.type === 'appointment_proposal' ? proposalStatus(card.data) : card.data.status] || '状态待核对' }}</span></header>
        <h3>{{ card.data.snapshot.patient_name }} <span class="patient-note">就诊人</span></h3>
        <p class="doctor-line">{{ card.data.snapshot.slot.department_name }} · {{ card.data.snapshot.slot.doctor_name }}</p>
        <dl class="visit-details">
          <div><dt>日期与时段</dt><dd>{{ card.data.snapshot.slot.date }} · {{ card.data.snapshot.slot.period === 'morning' ? '上午' : '下午' }} {{ card.data.snapshot.slot.start_time }}—{{ card.data.snapshot.slot.end_time }}</dd></div>
          <div><dt>医院与地点</dt><dd>{{ card.data.snapshot.slot.hospital_name }} · {{ card.data.snapshot.slot.location_name }}</dd></div>
          <div><dt>挂号费用</dt><dd>{{ money(card.data.snapshot.slot.fee_fen) }} <small>演示费用</small></dd></div>
        </dl>
        <template v-if="card.type === 'appointment_proposal'">
          <p v-if="proposalStatus(card.data) === 'pending'" class="confirmation-note">{{ card.data.operation === 'cancel' ? '请核对要取消的预约；点击确认后才会取消。' : '请核对就诊人与号源；点击确认后才会办理预约。' }}</p>
          <p v-if="proposalStatus(card.data) === 'pending'" class="expiry">有效至 {{ dateTime(card.data.expires_at) }}</p>
          <button v-if="proposalStatus(card.data) === 'pending'" type="button" class="confirm-button" :disabled="disabled || busy || !!confirmingId || clicked === card.data.proposal_id" @click="confirm(card.data)">
            {{ confirmingId === card.data.proposal_id || clicked === card.data.proposal_id ? '正在确认…' : card.data.operation === 'cancel' ? '确认取消预约' : '确认预约' }}
          </button>
          <p v-else class="state-explanation">{{ stateExplanations[proposalStatus(card.data)] || '状态待核对，此确认资料暂不可执行。' }}</p>
        </template>
        <p v-else-if="card.data.status === 'cancelled'" class="state-explanation">此预约已取消。<template v-if="card.data.cancelled_at">取消时间：{{ dateTime(card.data.cancelled_at) }}</template></p>
        <details class="record-details">
          <summary>查看办理编号与时间</summary>
          <dl>
            <div><dt>事项编号</dt><dd>{{ card.data.conv_id }}</dd></div>
            <div v-if="card.data.proposal_id"><dt>方案编号</dt><dd>{{ card.data.proposal_id }}</dd></div>
            <div v-if="card.data.appointment_id || card.data.operation === 'cancel'"><dt>预约编号</dt><dd>{{ card.data.appointment_id || card.data.target_id }}</dd></div>
            <div v-if="card.data.created_at"><dt>创建时间</dt><dd>{{ dateTime(card.data.created_at) }}</dd></div>
            <div v-if="card.data.expires_at"><dt>方案有效至</dt><dd>{{ dateTime(card.data.expires_at) }}</dd></div>
          </dl>
        </details>
      </template>
      <template v-else-if="card.type === 'visit_checklist'">
        <p class="eyebrow">就诊准备</p><h3>{{ card.data.title }}</h3>
        <ul class="checklist"><li v-for="item in card.data.items" :key="item">{{ item }}</li></ul>
        <small class="source">来源 · {{ card.data.source.title }}</small>
      </template>
      <template v-else-if="card.type === 'wayfinding'">
        <p class="eyebrow">{{ card.data.mode === 'accessible' ? '无障碍文字指引' : '院内文字指引' }}</p>
        <ol class="directions"><li v-for="step in card.data.steps" :key="step">{{ step }}</li></ol>
        <small class="source">来源 · {{ card.data.source.title }}</small>
      </template>
      <template v-else-if="card.type === 'contact_info'">
        <p class="eyebrow">联系导诊</p><h3>{{ card.data.label }}</h3>
        <p class="phone">{{ card.data.phone }}</p><p>{{ card.data.hours }} · {{ card.data.location }}</p>
        <label v-if="card.data.summary" class="contact-summary">可复制的问题摘要<textarea readonly :value="card.data.summary" aria-label="可复制的问题摘要" /></label>
        <small class="source">仅展示演示联系资料 · {{ card.data.source.title }}</small>
      </template>
      <p v-else>暂不支持显示此类资料。</p>
    </article>
  </div>
</template>

<style scoped>
.business-artifacts { display:grid; gap:12px; margin-top:14px; min-width:0; }
.business-card { padding:20px; border:1px solid var(--line-strong); border-left:3px solid var(--green); border-radius:12px; background:var(--panel); color:var(--text); min-width:0; overflow-wrap:anywhere; }
.business-card header { display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; margin-bottom:12px; }
.eyebrow { font-size:13px; font-weight:600; color:var(--green); margin:0 0 10px; }
header .eyebrow { margin:0; }
.business-card h3 { margin:0 0 8px; font-size:18px; line-height:1.5; }
.business-card p { font-size:16px; line-height:1.65; margin:8px 0; }
.business-card small, .source, .expiry { color:var(--text-soft); font-size:13px; }
.source { display:block; line-height:1.65; margin-top:10px; }
.catalog-item + .catalog-item { border-top:1px solid var(--line); margin-top:16px; padding-top:16px; }
.slots { list-style:none; padding:0; margin:12px 0 0; }
.slots li { display:flex; justify-content:space-between; gap:16px; padding:16px 0; border-top:1px solid var(--line); }
.slots li > div:first-child { min-width:0; }
.slots p { margin:8px 0 4px; }
.slot-number { font-variant-numeric:tabular-nums; margin-right:10px; color:var(--green); }
.slot-action { display:flex; flex-direction:column; align-items:flex-end; justify-content:center; gap:10px; flex-shrink:0; }
.slot-action strong { font-size:18px; font-variant-numeric:tabular-nums; }
.business-card button { min-height:44px; font-size:15px; padding:10px 14px; line-height:1.4; }
.business-card button:focus-visible, summary:focus-visible, textarea:focus-visible { outline:2px solid var(--green); outline-offset:3px; }
.business-card button:disabled { cursor:not-allowed; }
.business-card .availability { font-size:13px; color:var(--green); }
.business-card .sold-out { color:var(--text-soft); }
.status { font-size:13px; font-weight:600; padding:6px 10px; border-radius:6px; background:var(--panel-3); color:var(--text-soft); }
.status[data-status=pending] { color:var(--green); background:var(--green-soft); }
.status[data-status=executed], .status[data-status=active] { color:var(--green); }
.status[data-status=expired], .status[data-status=cancelled] { border:1px solid var(--line-strong); }
.patient-note { font-size:13px; color:var(--text-soft); margin-left:8px; font-weight:400; }
.doctor-line { font-weight:600; }
.visit-details { display:grid; gap:10px; margin:14px 0; }
.visit-details > div { display:grid; grid-template-columns:90px minmax(0,1fr); gap:12px; }
.visit-details dt { color:var(--text-soft); font-size:13px; line-height:1.7; }
.visit-details dd { margin:0; font-size:16px; line-height:1.5; }
.confirm-button { width:100%; margin-top:4px; background:var(--green); color:#08251a; }
.business-card .confirmation-note { font-size:14px; color:var(--text-soft); }
.business-card .expiry { font-size:13px; }
.record-details { margin-top:12px; border-top:1px solid var(--line); }
summary { min-height:44px; padding:12px 0; cursor:pointer; color:var(--text-soft); font-size:13px; line-height:1.5; }
.record-details dl { margin:0; display:grid; gap:8px; padding-bottom:8px; }
.record-details dl > div { display:grid; grid-template-columns:90px minmax(0,1fr); gap:12px; font-size:13px; line-height:1.6; }
.record-details dt { color:var(--text-soft); }
.record-details dd { margin:0; font-variant-numeric:tabular-nums; }
.settled-card { padding-block:14px; border-left-color:var(--line-strong); }
.settled-card .visit-details { gap:6px; margin:10px 0; }
.settled-card .visit-details dd { font-size:14px; }
.checklist, .directions { padding-left:22px; font-size:16px; line-height:1.75; margin:12px 0; }
.checklist li, .directions li { padding-bottom:6px; }
.business-card .empty-state, .business-card .state-explanation { color:var(--text-soft); font-size:14px; }
.card-error { color:var(--red); margin:0; font-size:14px; line-height:1.6; padding:10px 12px; border:1px solid var(--red); border-radius:8px; }
.business-card .phone { font-size:20px; font-variant-numeric:tabular-nums; }
.contact-summary { display:grid; gap:8px; margin:14px 0; font-size:13px; color:var(--text-soft); }
.contact-summary textarea { min-height:88px; resize:vertical; font-size:15px; line-height:1.6; }
@media (max-width:560px) {
  .business-card { padding:16px; }
  .slots li { flex-direction:column; gap:10px; }
  .slot-action { flex-direction:row; align-items:center; justify-content:space-between; }
  .slot-action button { min-width:140px; }
  .visit-details > div { grid-template-columns:1fr; gap:2px; }
  .record-details dl > div { grid-template-columns:1fr; gap:2px; }
}
</style>
