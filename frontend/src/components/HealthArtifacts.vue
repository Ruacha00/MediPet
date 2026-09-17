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
      <h3>{{ card.data.title }}</h3><p class="health-summary">{{ card.data.summary }}</p>
      <template v-if="card.type === 'triage_guidance'">
        <ul v-if="card.data.recommended_departments?.length" class="departments"><li v-for="department in card.data.recommended_departments" :key="department.department_id"><strong>{{ department.name }}</strong><p>{{ department.reason }}</p></li></ul>
        <section v-if="card.data.missing_information?.length"><h4>需要补充的信息</h4><ul><li v-for="item in card.data.missing_information" :key="item">{{ item }}</li></ul></section>
      </template>
      <template v-else-if="card.type === 'medication_info'">
        <p class="drug-name">{{ card.data.drug_name }}<template v-if="card.data.formulation"> · {{ card.data.formulation }}</template></p>
        <section v-for="section in card.data.sections || []" :key="section.heading"><h4>{{ section.heading }}</h4><ul><li v-for="item in section.items" :key="item">{{ item }}</li></ul></section>
      </template>
      <template v-else>
        <p class="health-note">来源：{{ { text: '粘贴文字', pdf: 'PDF 文件', image: '报告图片' }[card.data.input_kind] }} · 请对照原件核对</p>
        <ul v-if="card.data.warnings?.length" class="report-warnings" aria-label="报告核对提示"><li v-for="warning in card.data.warnings" :key="warning">{{ warning }}</li></ul>
        <p v-if="!card.data.observations?.length" class="report-empty">尚无可比对的项目。请展开提取文字，与原件核对后补充完整项目、单位和参考区间。</p>
        <div v-for="(item, index) in card.data.observations || []" :key="index" class="observation" :data-report-flag="item.flag">
          <div class="observation-heading"><strong>{{ item.item }}</strong><span class="range-status">{{ flags[item.flag] || '待核对' }}</span></div>
          <p class="observation-value">{{ item.value }} {{ item.unit }}<small v-if="item.reference_range">原参考区间：{{ item.reference_range }}</small><small v-else>原参考区间未提供</small></p>
          <details><summary>该项原文</summary><pre>{{ item.raw_line }}</pre></details>
        </div>
        <details class="extracted-text"><summary>全部提取文字（请核对）</summary><pre>{{ card.data.extracted_text }}</pre></details>
      </template>
      <section v-if="card.data.sources?.length" class="health-sources"><h4>资料来源</h4><ul><li v-for="source in card.data.sources" :key="source.source_id"><a v-if="sourceUrl(source.url)" :href="sourceUrl(source.url)" target="_blank" rel="noopener noreferrer">{{ source.title }}</a><span v-else>{{ source.title }}</span><small>核对日期：{{ source.reviewed_at }}</small></li></ul></section>
    </article>
  </div>
</template>

<style scoped>
.health-artifacts { display:grid; gap:12px; margin-top:14px; min-width:0; }
.health-card { background:var(--panel); border:1px solid var(--line-strong); border-left:3px solid var(--accent); border-radius:12px; padding:20px; color:var(--text); min-width:0; overflow-wrap:anywhere; }
.health-kind { margin:0 0 8px; color:var(--text-soft); font-size:13px; font-weight:600; }
h3 { font-size:18px; margin:0 0 8px; line-height:1.5; }
h4 { font-size:15px; margin:16px 0 8px; line-height:1.5; }
p, li { font-size:16px; line-height:1.7; }
p { margin:8px 0; }
ul { padding-left:22px; margin:10px 0; }
li + li { margin-top:6px; }
.health-note, small { color:var(--text-soft); font-size:13px; line-height:1.65; }
small { display:block; }
.departments { list-style:none; padding:0; }
.departments > li { border-top:1px solid var(--line); padding-top:12px; }
.departments strong { font-size:17px; }
.departments p { margin:4px 0; }
.drug-name { font-weight:600; border-bottom:1px solid var(--line); padding-bottom:12px; }
.report-warnings { color:var(--text-soft); background:var(--panel-2); padding:12px 12px 12px 32px; border-radius:8px; }
.report-warnings li, .report-empty { font-size:14px; }
.report-empty { color:var(--text-soft); }
.observation { border-top:1px solid var(--line); padding:14px 0 6px; }
.observation-heading { display:flex; justify-content:space-between; align-items:baseline; flex-wrap:wrap; gap:8px 12px; font-size:16px; }
.range-status { font-size:13px; color:var(--text-soft); }
[data-report-flag="above"] .range-status, [data-report-flag="below"] .range-status { color:var(--red); }
.observation-value { font-size:18px; font-variant-numeric:tabular-nums; margin-bottom:0; }
.observation-value small { margin-top:3px; }
details { font-size:13px; margin-top:6px; }
summary { min-height:44px; padding:12px 0; cursor:pointer; color:var(--text-soft); line-height:1.5; }
summary:focus-visible, a:focus-visible { outline:2px solid var(--green); outline-offset:3px; border-radius:3px; }
pre { white-space:pre-wrap; overflow-wrap:anywhere; font-family:inherit; font-size:13px; line-height:1.7; max-height:260px; overflow:auto; margin:0 0 8px; padding:12px; background:var(--panel-2); border-radius:6px; }
.extracted-text { border-top:1px solid var(--line); }
.health-sources { margin-top:14px; padding-top:2px; border-top:1px solid var(--line); }
.health-sources ul { list-style:none; padding:0; margin:0; }
.health-sources li { font-size:14px; margin:4px 0; }
.health-sources a { display:inline-flex; align-items:center; min-height:44px; padding:8px 0; color:var(--green); line-height:1.5; text-decoration:underline; text-underline-offset:3px; }
.health-sources small { margin-top:2px; }
@media (max-width:560px) { .health-card { padding:16px; } }
</style>
