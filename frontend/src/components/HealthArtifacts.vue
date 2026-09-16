<script setup>
import { computed } from 'vue'
const props = defineProps({ artifacts: { type: Array, default: () => [] } })
const types = ['triage_guidance', 'medication_info', 'report_summary']
const flags = { below: '低于原报告区间', within: '在原报告区间内', above: '高于原报告区间', unassessed: '待核对' }
const cards = computed(() => props.artifacts.filter(card => types.includes(card?.type) && card.data))
const sourceUrl = url => typeof url === 'string' && /^https?:\/\//i.test(url) ? url : null
</script>

<template>
  <div class="health-artifacts">
    <article v-for="card in cards" :key="card.id" class="health-card" :data-artifact="card.type">
      <p class="health-kind">{{ { triage_guidance: '就诊科室参考', medication_info: '药品公开信息', report_summary: '报告原文核对' }[card.type] }}</p>
      <h3>{{ card.data.title }}</h3><p>{{ card.data.summary }}</p>
      <template v-if="card.type === 'triage_guidance'">
        <ul v-if="card.data.recommended_departments?.length" class="departments"><li v-for="department in card.data.recommended_departments" :key="department.department_id"><strong>{{ department.name }}</strong><p>{{ department.reason }}</p></li></ul>
        <section v-if="card.data.missing_information?.length"><h4>需要补充的信息</h4><ul><li v-for="item in card.data.missing_information" :key="item">{{ item }}</li></ul></section>
      </template>
      <template v-else-if="card.type === 'medication_info'">
        <p class="drug-name">{{ card.data.drug_name }} · {{ card.data.formulation }}</p>
        <section v-for="section in card.data.sections || []" :key="section.heading"><h4>{{ section.heading }}</h4><ul><li v-for="item in section.items" :key="item">{{ item }}</li></ul></section>
      </template>
      <template v-else>
        <p class="health-note">来源：{{ { text: '粘贴文字', pdf: 'PDF 文件', image: '报告图片' }[card.data.input_kind] }} · 请对照原件核对</p>
        <ul class="report-warnings"><li v-for="warning in card.data.warnings || []" :key="warning">{{ warning }}</li></ul>
        <div v-for="(item, index) in card.data.observations || []" :key="index" class="observation" :data-report-flag="item.flag">
          <div><strong>{{ item.item }}</strong><span>{{ flags[item.flag] || '待核对' }}</span></div>
          <p>{{ item.value }} {{ item.unit }}<small v-if="item.reference_range">原参考区间：{{ item.reference_range }}</small></p>
          <details><summary>该项原文</summary><pre>{{ item.raw_line }}</pre></details>
        </div>
        <details class="extracted-text" open><summary>全部提取文字（请核对）</summary><pre>{{ card.data.extracted_text }}</pre></details>
      </template>
      <section v-if="card.data.sources?.length" class="health-sources"><h4>资料来源</h4><ul><li v-for="source in card.data.sources" :key="source.source_id"><a v-if="sourceUrl(source.url)" :href="sourceUrl(source.url)" target="_blank" rel="noopener noreferrer">{{ source.title }}</a><span v-else>{{ source.title }}</span><small>核对日期：{{ source.reviewed_at }}</small></li></ul></section>
    </article>
  </div>
</template>

<style scoped>
.health-artifacts { display:grid; gap:12px; margin-top:12px; min-width:0; }
.health-card { background:var(--panel); border:1px solid var(--line-strong); border-left:3px solid var(--accent); border-radius:12px; padding:16px; color:var(--text); min-width:0; overflow-wrap:anywhere; }
.health-kind { margin:0 0 6px; color:var(--accent); font-size:11px; font-weight:700; letter-spacing:.06em; }
h3 { font-size:16px; margin:0 0 8px; } h4 { font-size:13px; margin:14px 0 6px; }
p, li { font-size:13px; line-height:1.65; } p { margin:6px 0; } ul { padding-left:20px; margin:8px 0; }
.health-note, small { color:var(--text-soft); font-size:11px; } small { display:block; }
.report-warnings { color:var(--text-soft); background:var(--panel-2); padding:10px 10px 10px 28px; border-radius:8px; }
.observation { border-top:1px solid var(--line); padding:10px 0; } .observation>div { display:flex; justify-content:space-between; gap:10px; font-size:13px; }
.observation span { font-size:11px; } [data-report-flag="above"] span, [data-report-flag="below"] span { color:var(--red); }
details { font-size:12px; margin-top:7px; } summary { cursor:pointer; color:var(--text-soft); }
pre { white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px; line-height:1.7; max-height:260px; overflow:auto; padding:10px; background:var(--panel-2); border-radius:6px; }
.health-sources a { color:var(--green); }
</style>
