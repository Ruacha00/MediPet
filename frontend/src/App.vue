<template>
  <main :class="['app-shell', `app-shell-${activeView}`]">
    <header class="topbar">
      <a class="brand" href="#" aria-label="MediPet 首页" @click.prevent="activeView = 'chat'">
        <span class="brand-mark">M</span>
        <span class="brand-name">MediPet</span>
      </a>

      <nav class="view-nav" aria-label="工作区">
        <button :class="{ active: activeView === 'chat' }" @click="activeView = 'chat'">对话</button>
        <button :class="{ active: activeView === 'knowledge' }" @click="activeView = 'knowledge'">知识库</button>
        <button :class="{ active: activeView === 'evaluation' }" @click="activeView = 'evaluation'">评测</button>
      </nav>

      <div class="topbar-tools">
        <span class="environment-pill">
          <i :class="healthOk ? 'online' : 'offline'"></i>
          {{ currentBackend.label }}
        </span>
        <a class="docs-link" :href="docsUrl" target="_blank" rel="noreferrer">API 文档</a>
        <button class="avatar-button" title="当前用户">{{ userInitial }}</button>
      </div>
    </header>

    <div v-if="toast" class="toast" role="status">{{ toast }}</div>

    <section v-if="activeView === 'chat'" class="page page-chat">
      <div class="page-heading">
        <div class="heading-copy">
          <span class="kicker">就诊助手 · 明和虚构医院</span>
          <h1>把就诊准备，一件件办好</h1>
          <p>查门诊、备材料、预约与取消，在同一个就诊事项中继续。</p>
        </div>
        <span class="demo-disclaimer">演示数据 · 不提供诊断</span>
      </div>

      <div class="chat-layout visit-workspace">
        <aside class="chat-sidebar" ref="sidebarRef">
          <div class="chat-sidebar-scroll">
            <PatientVisitPanel :patients="patients" :visits="visits" :patient-id="patientId"
              :current-visit="currentVisit" :loading="workspaceLoading" :busy="visitBusy"
              :archived="showArchived" :error="workspaceError" :notice="workspaceNotice"
              @patient-change="selectPatient" @new-visit="newVisit" @select-visit="selectVisit"
              @rename-visit="renameVisit" @archive-visit="archiveVisit" @restore-visit="restoreVisit"
              @toggle-archived="toggleArchived" />
            <button v-if="workspaceError" class="quiet-button retry-workspace" @click="initializeWorkspace">重新读取就诊空间</button>
            <details class="side-card diagnostic-panel">
              <summary>请求与运行详情</summary>
              <section v-if="lastResponse" class="request-detail">
                <h2>最近一次请求</h2>
                <dl class="detail-list">
                  <div><dt>主要角色</dt><dd>{{ agentLabel(lastResponse.primaryAgent || lastResponse.agentType) }}</dd></div>
                  <div><dt>协作角色</dt><dd>{{ lastResponse.supportingAgents.map(agentLabel).join('、') || '无' }}</dd></div>
                  <div><dt>意图</dt><dd>{{ lastResponse.intent || '-' }}</dd></div>
                  <div><dt>意图置信度</dt><dd>{{ metricPercent(lastResponse.intentConfidence) }}</dd></div>
                  <div><dt>路由分数</dt><dd>{{ formatPercent(lastResponse.routingConfidence) }}</dd></div>
                  <div><dt>耗时</dt><dd>{{ lastResponse.latencyMs }} ms</dd></div>
                </dl>
                <p class="side-empty">{{ lastResponse.routingReason }}</p>
                <details v-if="lastTrace"><summary>全部执行记录（含失败）</summary><pre>{{ formatJson(lastTrace) }}</pre></details>
                <p class="source-note">来源：{{ sourceNames(lastResponse.raw, lastResponse.artifacts) }}</p>
              </section>
              <p v-else class="side-empty">发送消息后可查看角色分工与工具执行记录。</p>
              <section class="monitor-card">
                <div class="card-heading"><h2>运行状态</h2><button class="link-button" :disabled="monitorLoading" @click="loadMonitor">刷新</button></div>
                <p v-if="monitorError" class="management-error" role="alert">{{ monitorError }}</p>
                <p v-if="monitorLoading" class="side-empty">正在读取监控…</p>
                <div v-if="!monitorError" class="mini-stats"><div><strong>{{ totalRequests }}</strong><span>请求</span></div><div><strong>{{ agentCount }}</strong><span>角色</span></div><div><strong>{{ activeAlerts.length }}</strong><span>告警</span></div></div>
                <p v-if="activeAlerts.length" class="alert-note">{{ activeAlerts[0].detail || activeAlerts[0].title }}</p>
                <p v-else-if="monitorLoaded && !monitorError" class="healthy-note">当前没有活跃告警。</p>
                <template v-if="monitorLoaded && !monitorError">
                  <details><summary>角色与工具统计</summary>
                    <dl class="detail-list"><div v-for="(stats, name) in monitorData.agent_stats" :key="name"><dt>{{ agentLabel(name) }}</dt><dd>{{ stats.total ?? '—' }} 次 · 成功 {{ metricPercent(stats.success_rate) }} · {{ stats.avg_ms ?? '—' }} ms</dd></div></dl>
                    <div v-for="(stats, name) in monitorData.tool_stats" :key="name" class="monitor-tool"><strong>{{ name }}</strong><p>成功 {{ metricPercent(stats.success_rate) }} · 平均 {{ stats.avg_latency_ms ?? '—' }} ms · 连续失败 {{ stats.consecutive_fails ?? '—' }}</p></div>
                    <details><summary>完整监控数据</summary><pre>{{ formatJson(monitorData) }}</pre></details>
                  </details>
                  <p v-for="(alert, index) in activeAlerts.slice(1)" :key="index" class="management-error">{{ alert.title }} {{ alert.detail }}</p>
                  <div v-for="(suggestion, index) in monitorData.suggestions" :key="index" class="monitor-suggestion"><strong>{{ suggestion.title }}</strong><p>{{ suggestion.action }}</p></div>
                </template>
              </section>
              <div class="side-actions"><button class="quiet-button" @click="checkHealth">检查连接</button><span>{{ healthLabel }}</span></div>
              <details><summary>连接资料</summary><code>{{ currentBackend.baseUrl }}</code><pre>{{ statusText }}</pre></details>
            </details>
          </div>
        </aside>

        <section class="chat-stage" :aria-busy="workspaceLoading || chatPending">
          <div class="stage-bar">
            <div class="stage-context"><span class="context-dot"></span><span>{{ patientName }}<template v-if="currentVisit"> · {{ currentVisit.title }}</template></span></div>
            <button class="link-button" :disabled="!currentVisit || workspaceLoading || operationPending || visitBusy" @click="refreshHistory">刷新记录</button>
          </div>
          <div class="messages" ref="messageList" aria-label="事项完整记录">
            <p v-if="workspaceLoading" class="history-status" role="status">正在读取完整记录…</p>
            <template v-else>
              <article v-for="item in messages" :key="item.message_id" :class="['message', item.role, { 'operation-message': item.kind === 'operation_result' }]" :data-message-id="item.message_id">
                <div class="message-meta"><span>{{ messageLabel(item) }}</span><small>{{ messageTime(item.created_at) }}</small></div>
                <p>{{ item.content }}</p>
                <BusinessArtifacts v-if="item.artifacts?.length" :artifacts="item.artifacts.filter(card => !healthCardTypes.includes(card.type))" :disabled="!canChat"
                  :busy="operationPending" :confirming-id="confirmingId" :proposal-states="proposalStates" :error="chatError"
                  @select-slot="selectSlot" @confirm-proposal="confirmProposal" />
                <HealthArtifacts :artifacts="item.artifacts || []" />
                <div v-for="record in cancellableRecords(item)" :key="record.appointment_id" class="record-actions">
                  <button class="quiet-button" :disabled="!canChat || operationPending" @click="prepareCancellation(record.appointment_id)">准备取消此预约</button>
                </div>
                <details v-if="item.kind === 'operation_result'" class="message-trace business-receipt-detail">
                  <summary>业务办理回执详情</summary><p class="trace-note">明确确认后执行的业务操作。</p><pre>{{ formatJson(item.metadata?.receipt || { receipt_id: item.receipt_id, proposal_id: item.proposal_id }) }}</pre>
                </details>
                <details v-else-if="item.role === 'assistant' && (item.metadata?.request_id || item.metadata?.tool_traces?.length)" class="message-trace request-evidence">
                  <summary>运行详情 · {{ agentLabel(item.metadata.primary_agent || item.metadata.agent_type) }}</summary>
                  <dl class="request-facts"><div><dt>请求</dt><dd>{{ item.metadata.request_id || '未返回' }}</dd></div><div><dt>意图 / 置信度</dt><dd>{{ item.metadata.intent || '未返回' }} · {{ metricPercent(item.metadata.intent_confidence) }}</dd></div><div><dt>主要角色</dt><dd>{{ agentLabel(item.metadata.primary_agent || item.metadata.agent_type) }}</dd></div><div><dt>协作角色</dt><dd>{{ (item.metadata.supporting_agents || []).map(agentLabel).join('、') || '无' }}</dd></div><div><dt>路由分数 / 耗时</dt><dd>{{ metricPercent(item.metadata.routing_confidence) }} · {{ item.metadata.latency_ms ?? '未返回' }} ms</dd></div></dl>
                  <p class="trace-note">{{ item.metadata.routing_reason || '本次未返回路由理由。' }}</p>
                  <p class="source-note">知识或业务资料来源：{{ sourceNames(item.metadata, item.artifacts) }}</p>
                  <div v-for="(trace, index) in item.metadata.tool_traces || []" :key="index" class="trace-evidence" :data-trace-status="trace.success === false ? 'failed' : trace.success === true ? 'success' : 'unknown'">
                    <div class="trace-title"><strong>{{ trace.tool_name || trace.kind || '执行记录' }}</strong><span>{{ trace.success === false ? '失败' : trace.success === true ? '成功' : '状态未返回' }} · {{ agentLabel(trace.agent_type) }}</span></div>
                    <p v-if="trace.error" class="management-error">{{ trace.error_code ? trace.error_code + '：' : '' }}{{ trace.error }}</p>
                    <ul v-if="retrievalIssues(trace).length" class="management-warning"><li v-for="(problem, problemIndex) in retrievalIssues(trace)" :key="problemIndex">{{ problem }}</li></ul>
                    <dl class="request-facts"><div><dt>输入</dt><dd><pre>{{ formatJson(trace.input ?? {}) }}</pre></dd></div><div><dt>结果摘要</dt><dd><pre>{{ formatJson(trace.result_summary ?? trace.result ?? {}) }}</pre></dd></div></dl>
                    <p class="source-note">来源：{{ sourceNames({ tool_traces: [trace] }) }}</p>
                    <details><summary>完整记录</summary><pre>{{ formatJson(trace) }}</pre></details>
                  </div>
                  <p v-if="!item.metadata.tool_traces?.length" class="trace-note">本次没有工具执行记录。</p>
                  <details><summary>识别与路由原始字段</summary><pre>{{ formatJson(item.metadata) }}</pre></details>
                </details>
              </article>
              <div v-if="!messages.length" class="empty-state visit-empty">
                <div class="empty-symbol" aria-hidden="true">＋</div>
                <h2>{{ currentVisit ? '从这次就诊的需要开始' : '先选就诊人，再新建一个事项' }}</h2>
                <p>{{ currentVisit ? '消息、预约方案和办理记录都会保存在这里。' : '本人和家属各有独立的事项与预约记录。' }}</p>
                <div v-if="canChat" class="starter-prompts">
                  <button @click="usePrompt('明天儿科上午还有号吗？孩子第一次就诊要带什么？')">儿科号源与材料</button>
                  <button @click="usePrompt('从门诊大厅到药房怎么走？需要无障碍路线。')">院内路线</button>
                  <button @click="usePrompt('查询我的预约记录')">我的预约</button>
                </div>
              </div>
            </template>
          </div>
          <form class="composer" @submit.prevent="sendMessage">
            <ReportUpload :key="settings.conversationId" :disabled="!canChat || operationPending" :busy="reportPending" @upload="submitReport" />
            <p v-if="currentVisit?.archived" class="history-status">这件事项已归档。请在左侧查看归档并恢复后继续。</p>
            <p v-if="chatPending" class="history-status" role="status">正在处理当前事项，请稍候…</p>
            <p v-if="operationReceipt" class="operation-notice" role="status">{{ operationReceipt.operation === 'cancel' ? '取消预约' : '预约' }}已完成，回执已保存。<small>回执：{{ operationReceipt.receipt_id }}</small></p>
            <p v-if="chatError" class="chat-error" role="alert">{{ chatError }}</p>
            <textarea v-model="draft" rows="2" aria-label="就诊问题" :disabled="!canChat || operationPending"
              placeholder="例如：明天儿科上午还有号吗？" @keydown.meta.enter.prevent="sendMessage" @keydown.ctrl.enter.prevent="sendMessage"></textarea>
            <div class="composer-bottom"><span>方案仅供核对，点击卡片确认后才办理。</span><button type="submit" :disabled="!canChat || operationPending || !draft.trim()">{{ chatPending ? '处理中…' : '发送' }} <span aria-hidden="true">↑</span></button></div>
          </form>
        </section>
      </div>
    </section>

    <section v-else-if="activeView === 'knowledge'" class="page page-knowledge">
      <div class="page-heading">
        <div class="heading-copy"><span class="kicker">医院公开资料</span><h1>知识库</h1><p>查询与补充就诊说明，查看当前生效的场景能力。</p></div>
        <div class="count-display"><strong>{{ knowledgeCount }}</strong><span>文档片段</span><button class="link-button" @click="loadStats">刷新统计</button></div>
      </div>
      <p v-if="statsError" class="management-error" role="alert">统计读取失败：{{ statsError }}</p>
      <div class="knowledge-layout">
        <section class="workspace-card search-workspace">
          <div class="card-heading"><div><span class="kicker">检索与来源</span><h2>检索知识</h2></div></div>
          <div class="search-line"><input v-model="searchQuery" aria-label="检索问题" placeholder="例如：儿童就诊需要哪些材料" @keydown.enter="searchKnowledge" /><button @click="searchKnowledge" :disabled="searchLoading || !searchQuery.trim()">{{ searchLoading ? '检索中…' : '搜索' }}</button></div>
          <p v-if="searchError" class="management-error" role="alert">{{ searchError }}</p>
          <ul v-if="searchReport && retrievalIssues(searchReport).length" class="management-warning"><li v-for="(problem, index) in retrievalIssues(searchReport)" :key="index">{{ problem }}</li></ul>
          <p v-if="searchReport?.success === true" class="management-note">{{ searchReport.reranked ? '已完成重排' : '未完成重排，保留召回顺序' }} · {{ searchResults.length }} 条有效结果</p>
          <div v-if="searchResults.length" class="result-list">
            <article v-for="(item, index) in searchResults" :key="item.chunk_id || item.id || index" class="result-item">
              <span class="result-number">{{ String(index + 1).padStart(2, '0') }}</span><div>
                <div class="result-title"><strong>{{ item.title || '未返回标题' }}</strong><small>相关度 {{ item.score ?? '未返回' }}</small></div><p>{{ item.content }}</p>
                <p class="source-note">来源：{{ item.source || '未返回来源' }}<br />来源标识：{{ item.source_id || '未返回' }} · 文档：{{ item.doc_id || '未返回' }}<br />片段：{{ item.chunk_id || '未返回' }}</p>
              </div>
            </article>
          </div>
          <div v-else-if="!searchError && !searchLoading" class="workspace-empty">{{ searchReport ? '没有检索到相关资料，可以换个问法。' : '输入就诊问题开始搜索。' }}</div>
        </section>
        <section class="workspace-card import-workspace">
          <div class="card-heading"><div><span class="kicker">资料维护</span><h2>添加知识</h2></div></div>
          <label><span>标题</span><input v-model="docTitle" placeholder="就诊材料说明" /></label>
          <label><span>内容</span><textarea v-model="docContent" rows="7" placeholder="输入医院公开资料、材料清单或文字指引"></textarea></label>
          <div class="source-inputs"><label><span>来源说明（选填）</span><input v-model="docSource" placeholder="例如：医院公开就诊须知" /></label><label><span>来源标识（选填）</span><input v-model="docSourceId" placeholder="例如：visit-materials" /></label></div>
          <p class="management-note">未填写来源时，服务会标为上传资料。动态号源与个人预约记录请通过业务查询获取。</p>
          <p v-if="importError" class="management-error" role="alert">{{ importError }}</p><p v-if="importNotice" class="management-success" role="status">{{ importNotice }}</p>
          <div class="side-actions"><button @click="submitKnowledge" :disabled="importLoading || !docTitle.trim() || !docContent.trim()">{{ importLoading ? '导入中…' : '添加文档' }}</button><label class="upload-button" :class="{ 'is-disabled': importLoading }">上传文件<input type="file" :disabled="importLoading" accept=".txt,.md,.json" @change="handleUpload" /></label></div>
          <p class="management-note">支持 TXT、Markdown、JSON 文档数组，最大 10 MB。</p>
        </section>
      </div>
      <section class="workspace-card skills-workspace">
        <div class="card-heading"><div><span class="kicker">场景 Skills</span><h2>已加载能力</h2></div><button class="link-button" :disabled="skillsLoading" @click="reloadSkillSet">{{ skillsLoading ? '加载中…' : '重新加载' }}</button></div>
        <p class="management-note">修改场景文件后主动重载，后续请求使用新内容。工具权限与预约校验仍由服务执行。</p>
        <p v-if="skillsError" class="management-error" role="alert">{{ skillsError }}</p><p v-if="skillsNotice" class="management-success" role="status">{{ skillsNotice }}</p>
        <ul v-if="skillsData.errors?.length" class="management-warning" role="alert"><li v-for="(error, index) in skillsData.errors" :key="index">{{ error }}</li></ul>
        <div class="skill-table">
          <article v-for="skill in skillsData.skills" :key="skill.path || skill.name" class="skill-detail">
            <div class="skill-title"><strong>{{ skill.name }}</strong><span>{{ skill.enabled === false ? '未启用' : '已启用' }} · {{ skill.content_chars ?? '未返回' }} 字符</span></div><p>{{ skill.description || '未提供说明' }}</p>
            <dl class="request-facts"><div><dt>适用角色</dt><dd>{{ (skill.agents || []).map(agentLabel).join('、') || '全部角色' }}</dd></div><div><dt>匹配词</dt><dd>{{ skill.keywords?.join('、') || '持续生效' }}</dd></div><div><dt>文件</dt><dd>{{ skill.path || '未返回路径' }}</dd></div></dl>
          </article>
          <div v-if="!skillsData.skills.length && !skillsError && !skillsLoading" class="workspace-empty">当前没有已加载的 Skill。</div>
        </div>
      </section>
    </section>

    <section v-else class="page page-evaluation">
      <div class="page-heading"><div class="heading-copy"><span class="kicker">质量与业务验证</span><h1>MediPet 评测</h1><p>运行既有场景，核对实际样本、调用失败和 Judge 评分。运行会调用模型并生成待复核的候选报告。</p></div><button @click="runEvaluation" :disabled="evalLoading">{{ evalLoading ? '评测运行中…' : '运行评测' }}</button></div>
      <p v-if="evalError" class="management-error" role="alert">{{ evalError }}</p>
      <p v-if="evalLoading" class="management-note" role="status">正在执行场景与评分，请稍候；尚无本次结论。</p>
      <div v-if="evalData" class="evaluation-content">
        <section class="workspace-card baseline-status" :data-baseline-status="baselineAccepted ? 'accepted' : 'candidate'">
          <h2>{{ baselineAccepted ? '已接受基线' : '候选报告 · 尚未接受为基线' }}</h2>
          <dl class="request-facts"><div><dt>运行编号</dt><dd>{{ evalData.run_id || '未返回' }}</dd></div><div><dt>时间</dt><dd>{{ evalData.timestamp || '未返回' }}</dd></div><div><dt>候选文件</dt><dd>{{ evalData.candidate_path || '未返回保存路径' }}</dd></div><div><dt>复核人</dt><dd>{{ evalData.accepted_by || '尚未记录复核人' }}</dd></div><div><dt>模型 / 范围</dt><dd>{{ evalData.metadata?.model || '未返回模型' }} · {{ evalData.metadata?.scope || '未返回范围' }}</dd></div></dl>
        </section>
        <div class="evaluation-summary">
          <div class="score-hero"><span>通过率</span><strong>{{ metricPercent(evalData.pass_rate) }}</strong><small>{{ evalData.passed }} / {{ evalData.total }} 项结果通过</small></div>
          <div><span>Judge 失败</span><strong :class="evalData.judge_failures?.length ? 'danger' : ''">{{ evalData.judge_failures?.length ?? '未返回' }}</strong></div>
          <div><span>调用失败</span><strong :class="evalData.call_failures?.length ? 'danger' : ''">{{ evalData.call_failures?.length ?? '未返回' }}</strong></div>
          <div><span>跳过</span><strong>{{ evalData.skipped?.length ?? '未返回' }}</strong></div>
        </div>
        <section v-if="evaluationProblems.length" class="workspace-card evaluation-problems"><h2>失败与未执行样本</h2><div v-for="group in evaluationProblems" :key="group.label"><strong>{{ group.label }}</strong><p>{{ group.ids.join('、') }}</p></div></section>
        <div class="evaluation-layout">
          <section class="workspace-card"><div class="card-heading"><h2>实际平均评分</h2></div><p class="management-note">有效质量评分 {{ evalData.metadata?.valid_quality_count ?? '未返回' }} 项；Judge 失败不计入质量均分。</p><div class="score-list"><div v-for="(value, key) in evalData.avg_scores" :key="key"><span>{{ scoreLabel(key) }}</span><i><b :style="{ width: `${Math.max(0, Math.min(Number(value) * 100, 100))}%` }"></b></i><strong>{{ metricPercent(value) }}</strong></div></div><p v-if="!Object.keys(evalData.avg_scores || {}).length" class="management-note">本次没有有效平均评分。</p></section>
          <section class="workspace-card"><div class="card-heading"><h2>回归与建议</h2></div><p v-if="!evalData.regressions?.length" class="management-note">报告未列出回归项，不代表已接受为基线。</p><p v-for="(item, index) in evalData.regressions" :key="'regression-' + index" class="management-error">{{ item }}</p><div class="recommendations"><p v-for="(item, index) in evalData.recommendations" :key="index">{{ item }}</p></div></section>
        </div>
        <section class="workspace-card evaluation-cases"><div class="card-heading"><h2>全部实际样本结果</h2><label class="failed-filter"><input v-model="failedOnly" type="checkbox" />只看失败或跳过</label></div>
          <details v-for="result in visibleEvalResults" :key="result.test_id" class="evaluation-case" :data-result-status="result.metadata?.status || (result.passed ? 'passed' : 'failed')"><summary><strong>{{ result.test_id }}</strong><span>{{ result.metadata?.status === 'skipped' ? '跳过' : result.metadata?.judge_failed ? 'Judge 失败' : result.metadata?.call_failed ? '调用失败' : result.passed ? '通过' : '失败' }}</span></summary><p>{{ result.detail || '未提供详情' }}</p><dl class="request-facts"><div v-for="(value, key) in result.scores" :key="key"><dt>{{ scoreLabel(key) }}</dt><dd>{{ metricPercent(value) }}</dd></div></dl><pre>{{ formatJson(result.metadata) }}</pre></details>
          <p v-if="!visibleEvalResults.length" class="management-note">{{ failedOnly ? '没有符合筛选条件的样本。' : '报告未返回逐样本结果。' }}</p>
          <details class="raw-evaluation"><summary>完整报告与运行参数</summary><pre>{{ formatJson(evalData) }}</pre></details>
        </section>
      </div>
      <div v-else-if="!evalError && !evalLoading" class="evaluation-empty"><div class="empty-symbol">◎</div><h2>还没有评测结果</h2><p>点击右上角运行评测，结果不会自动成为已接受基线。</p></div>
    </section>
  </main>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import BusinessArtifacts from './components/BusinessArtifacts.vue'
import PatientVisitPanel from './components/PatientVisitPanel.vue'
import HealthArtifacts from './components/HealthArtifacts.vue'
import ReportUpload from './components/ReportUpload.vue'
import {
  addKnowledge,
  backendMeta,
  createInitialSettings,
  reloadSkills,
  requestChat,
  requestHealth,
  requestKnowledgeStats,
  requestMonitor,
  requestSearch,
  requestPatients,
  requestVisits,
  createVisit,
  updateVisit,
  requestVisitMessages,
  confirmAppointmentProposal,
  uploadReport,
  requestSkills,
  runEvaluation as requestEvaluation,
  saveSettings,
  uploadKnowledge
} from './lib/backends'

const settings = reactive(createInitialSettings())
const activeView = ref('chat')
const messages = ref([])
const draft = ref('')
const healthOk = ref(false)
const healthLabel = ref('未检查')
const statusText = ref('')
const knowledgeCount = ref('-')
const searchQuery = ref('儿童就诊需要哪些材料')
const searchResults = ref([])
const docTitle = ref('就诊材料补充说明')
const docContent = ref('')
const docSource = ref('')
const docSourceId = ref('')
const statsError = ref('')
const searchLoading = ref(false)
const searchError = ref('')
const searchReport = ref(null)
const importLoading = ref(false)
const importError = ref('')
const importNotice = ref('')
const skillsLoading = ref(false)
const skillsError = ref('')
const skillsNotice = ref('')
const monitorLoading = ref(false)
const monitorLoaded = ref(false)
const monitorError = ref('')
const evalLoading = ref(false)
const evalError = ref('')
const failedOnly = ref(false)
const messageList = ref(null)
const sidebarRef = ref(null)
const monitorData = ref({ agent_stats: {}, tool_stats: {}, active_alerts: [], suggestions: [] })
const skillsData = ref({ count: 0, skills: [], errors: [] })
const lastResponse = ref(null)
const lastTrace = ref(null)
const evalData = ref(null)
const toast = ref('')
let toastTimer
let sidebarObserver

const currentBackend = computed(() => backendMeta(settings.backend, settings))
const docsUrl = computed(() => `${currentBackend.value.baseUrl}/docs`)
const userInitial = computed(() => (settings.userId || 'U').slice(0, 1).toUpperCase())
const activeAlerts = computed(() => monitorData.value.active_alerts || [])
const agentCount = computed(() => Object.keys(monitorData.value.agent_stats || {}).length)
const totalRequests = computed(() => Object.values(monitorData.value.agent_stats || {}).reduce((sum, item) => sum + Number(item.total || 0), 0))

watch(() => settings.conversationId, persist)
onMounted(() => {
  refreshConsole()
  initializeWorkspace()
  updateSidebarHeight()
  if (typeof ResizeObserver !== 'undefined') {
    sidebarObserver = new ResizeObserver(updateSidebarHeight)
    if (sidebarRef.value) sidebarObserver.observe(sidebarRef.value)
  }
  window.addEventListener('resize', updateSidebarHeight)
})

onBeforeUnmount(() => {
  workspaceEpoch += 1
  clearTimeout(toastTimer)
  sidebarObserver?.disconnect?.()
  window.removeEventListener('resize', updateSidebarHeight)
})

function persist() { saveSettings(settings) }

function updateSidebarHeight() {
  const sidebar = sidebarRef.value
  if (!sidebar) return
  const rect = sidebar.getBoundingClientRect()
  const height = Math.max(320, Math.floor(rect.height))
  sidebar.style.setProperty('--sidebar-height', `${height}px`)
}

async function refreshConsole() {
  await Promise.allSettled([checkHealth(), loadStats(), loadMonitor(), loadSkills()])
}

async function checkHealth() {
  try {
    const data = await requestHealth(settings.backend, settings)
    healthOk.value = data.status === 'ok'
    healthLabel.value = data.status || 'ok'
    statusText.value = JSON.stringify(data, null, 2)
  } catch (error) {
    healthOk.value = false
    healthLabel.value = '不可用'
    statusText.value = error.message

  }
}

async function loadStats() {
  statsError.value = ''
  try {
    const data = await requestKnowledgeStats(settings.backend, settings)
    knowledgeCount.value = data.total_chunks ?? '-'
  } catch (error) { knowledgeCount.value = '-'; statsError.value = readableError(error) }
}
async function loadMonitor() {
  if (monitorLoading.value) return
  monitorLoading.value = true
  monitorError.value = ''
  try { monitorData.value = await requestMonitor(settings.backend, settings); monitorLoaded.value = true }
  catch (error) { monitorError.value = readableError(error) }
  finally { monitorLoading.value = false }
}
async function loadSkills() {
  skillsLoading.value = true
  skillsError.value = ''
  try { skillsData.value = await requestSkills(settings.backend, settings) }
  catch (error) { skillsError.value = readableError(error) }
  finally { skillsLoading.value = false }
}
async function reloadSkillSet() {
  if (skillsLoading.value) return
  skillsLoading.value = true
  skillsError.value = ''
  skillsNotice.value = ''
  try {
    skillsData.value = await reloadSkills(settings.backend, settings)
    if (skillsData.value.errors?.length) skillsError.value = `本次加载 ${skillsData.value.count} 份能力，另有 ${skillsData.value.errors.length} 项错误，请核对下方文件。`
    else skillsNotice.value = `已重新加载 ${skillsData.value.count} 份能力，后续请求使用本次加载内容。`
  } catch (error) { skillsError.value = readableError(error) }
  finally { skillsLoading.value = false }
}

const patients = ref([])
const visits = ref([])
const patientId = ref('')
const currentVisit = ref(null)
const showArchived = ref(false)
const workspaceLoading = ref(false)
const visitBusy = ref(false)
const workspaceError = ref('')
const workspaceNotice = ref('')
const chatError = ref('')
const operationReceipt = ref(null)
const rejectedProposals = ref({})
const pendingOperations = reactive({})
let workspaceEpoch = 0

const patientName = computed(() => patients.value.find(item => item.patient_id === patientId.value)?.name || '就诊空间')
const operationPending = computed(() => Boolean(pendingOperations[settings.conversationId]))
const chatPending = computed(() => pendingOperations[settings.conversationId]?.type === 'chat')
const reportPending = computed(() => pendingOperations[settings.conversationId]?.type === 'report')
const healthCardTypes = ['triage_guidance', 'medication_info', 'report_summary']
const confirmingId = computed(() => pendingOperations[settings.conversationId]?.proposalId || '')
const canChat = computed(() => Boolean(currentVisit.value && !currentVisit.value.archived && !workspaceLoading.value && !visitBusy.value))
const proposalStates = computed(() => {
  const states = {}
  for (const message of messages.value) {
    for (const artifact of message.artifacts || []) {
      if (artifact.type === 'appointment_proposal') states[artifact.data.proposal_id] = artifact.data.status
    }
    if (message.kind === 'operation_result' && message.proposal_id) states[message.proposal_id] = 'executed'
  }
  if (operationReceipt.value) states[operationReceipt.value.proposal_id] = 'executed'
  return { ...states, ...rejectedProposals.value }
})
const latestAppointments = computed(() => {
  const records = {}
  for (const message of messages.value) for (const artifact of message.artifacts || []) {
    if (artifact.type === 'appointment_record') records[artifact.data.appointment_id] = { ...artifact.data, messageId: message.message_id }
  }
  return records
})

function requestSettings(conversationId = settings.conversationId, selectedPatient = patientId.value) {
  return { ...settings, endpoints: { ...settings.endpoints }, conversationId, patientId: selectedPatient }
}
function isCurrent(epoch) { return workspaceEpoch === epoch }
function beginContext(selectedPatient = patientId.value) {
  workspaceEpoch += 1
  patientId.value = selectedPatient
  settings.patientId = selectedPatient
  settings.conversationId = ''
  currentVisit.value = null
  messages.value = []
  lastResponse.value = null
  lastTrace.value = null
  draft.value = ''
  chatError.value = ''
  operationReceipt.value = null
  rejectedProposals.value = {}
  workspaceError.value = ''
  workspaceNotice.value = ''
  workspaceLoading.value = true
  visitBusy.value = false
  return workspaceEpoch
}
function readableError(error) {
  const explanations = {
    proposal_expired: '这份方案已过期，请重新查询号源并准备方案。',
    proposal_superseded: '这份方案已被新方案替代，请核对最新的方案卡片。',
    identity_conflict: '就诊人或事项不一致，请重新选择事项后再试。',
    visit_archived: '事项已归档，请恢复后再继续。',
    selection_unavailable: '这组号源已不可选择，请重新查询。',
    no_slots: '没有符合条件的号源，可以换个日期或时段。',
    target_unavailable: '该号源或预约状态已变化，请重新查询。',
    conflict: '预约资料发生变化，请刷新记录后再试。',
    storage_unavailable: '服务暂时无法保存或读取记录，请稍后重试。'
  }
  if (error.kind === 'network') return '暂时无法连接服务，请检查连接后重试。'
  return explanations[error.code] || error.detail?.message || error.message || '操作未完成，请重试。'
}
function checkIdentity(value, snapshot) {
  if (!value || value.user_id !== snapshot.userId || value.conv_id !== snapshot.conversationId ||
      (snapshot.patientId && value.patient_id !== snapshot.patientId)) {
    throw Object.assign(new Error('返回记录与当前就诊人或事项不一致，请重新读取。'), { code: 'identity_conflict' })
  }
}
async function readHistory(snapshot, epoch) {
  const data = await requestVisitMessages(snapshot.backend, snapshot, snapshot.conversationId)
  if (!isCurrent(epoch)) return false
  checkIdentity(data.visit, snapshot)
  for (const item of data.items) checkIdentity(item, { ...snapshot, patientId: data.visit.patient_id })
  if (!patients.value.some(item => item.patient_id === data.visit.patient_id)) throw new Error('未找到该事项绑定的就诊人。')
  currentVisit.value = data.visit
  patientId.value = data.visit.patient_id
  settings.patientId = data.visit.patient_id
  settings.conversationId = data.visit.conv_id
  messages.value = data.items
  persist()
  await nextTick()
  if (isCurrent(epoch)) messageList.value?.scrollTo?.({ top: messageList.value.scrollHeight, behavior: 'smooth' })
  return true
}
async function readVisits(snapshot, epoch, archived = showArchived.value) {
  const data = await requestVisits(snapshot.backend, snapshot, snapshot.patientId, archived)
  if (isCurrent(epoch)) visits.value = data.items
  return data.items
}
async function initializeWorkspace() {
  const savedConversation = settings.conversationId
  const epoch = beginContext('')
  visits.value = []
  try {
    const snapshot = requestSettings('', '')
    const data = await requestPatients(snapshot.backend, snapshot)
    if (!isCurrent(epoch)) return
    patients.value = data.items
    if (savedConversation) {
      try {
        await readHistory({ ...snapshot, conversationId: savedConversation }, epoch)
        if (!isCurrent(epoch)) return
        showArchived.value = currentVisit.value.archived
        await readVisits(requestSettings(), epoch)
        return
      } catch (error) {
        if (!isCurrent(epoch)) return
        if (error.status !== 404) throw error
        workspaceNotice.value = '之前的事项已不可用，请选择或新建事项。'
      }
    }
    patientId.value = patients.value[0]?.patient_id || ''
    settings.patientId = patientId.value
    showArchived.value = false
    if (patientId.value) {
      const items = await readVisits(requestSettings(), epoch, false)
      if (isCurrent(epoch) && items.length) await readHistory(requestSettings(items[0].conv_id), epoch)
    }
  } catch (error) {
    if (isCurrent(epoch)) workspaceError.value = readableError(error)
  } finally { if (isCurrent(epoch)) workspaceLoading.value = false }
}
async function selectPatient(id) {
  if (!patients.value.some(item => item.patient_id === id)) return
  const epoch = beginContext(id)
  visits.value = []
  showArchived.value = false
  try {
    const items = await readVisits(requestSettings(), epoch, false)
    if (isCurrent(epoch) && items.length) await readHistory(requestSettings(items[0].conv_id, id), epoch)
  } catch (error) { if (isCurrent(epoch)) workspaceError.value = readableError(error) }
  finally { if (isCurrent(epoch)) workspaceLoading.value = false }
}
async function selectVisit(id) {
  const epoch = beginContext()
  try { await readHistory(requestSettings(id), epoch) }
  catch (error) { if (isCurrent(epoch)) workspaceError.value = readableError(error) }
  finally { if (isCurrent(epoch)) workspaceLoading.value = false }
}
function refreshHistory() { if (currentVisit.value) return selectVisit(currentVisit.value.conv_id) }
async function newVisit(id) {
  if (!id || workspaceLoading.value || visitBusy.value) return
  const epoch = beginContext(id)
  showArchived.value = false
  try {
    const snapshot = requestSettings('', id)
    const visit = await createVisit(snapshot.backend, snapshot, id)
    if (!isCurrent(epoch)) return
    await readHistory({ ...snapshot, conversationId: visit.conv_id }, epoch)
    await readVisits(snapshot, epoch, false)
  } catch (error) { if (isCurrent(epoch)) workspaceError.value = readableError(error) }
  finally { if (isCurrent(epoch)) workspaceLoading.value = false }
}
async function toggleArchived(archived) {
  const epoch = beginContext()
  visits.value = []
  showArchived.value = archived
  try {
    const items = await readVisits(requestSettings(), epoch, archived)
    if (isCurrent(epoch) && items.length) await readHistory(requestSettings(items[0].conv_id), epoch)
  } catch (error) { if (isCurrent(epoch)) workspaceError.value = readableError(error) }
  finally { if (isCurrent(epoch)) workspaceLoading.value = false }
}
async function changeVisit(id, changes) {
  if (visitBusy.value || workspaceLoading.value) return
  const epoch = workspaceEpoch
  const snapshot = requestSettings(id)
  visitBusy.value = true
  workspaceError.value = ''
  workspaceNotice.value = ''
  try {
    const visit = await updateVisit(snapshot.backend, snapshot, id, changes)
    if (!isCurrent(epoch)) return
    checkIdentity(visit, snapshot)
    if (id === currentVisit.value?.conv_id) await readHistory(snapshot, epoch)
    if (!isCurrent(epoch)) return
    if (changes.archived === false) showArchived.value = false
    await readVisits(snapshot, epoch)
    if (isCurrent(epoch)) workspaceNotice.value = changes.archived === true ? '事项已归档，完整记录继续保留。' : changes.archived === false ? '事项已恢复。' : '事项名称已保存。'
  } catch (error) { if (isCurrent(epoch)) workspaceError.value = readableError(error) }
  finally { if (isCurrent(epoch)) visitBusy.value = false }
}
function renameVisit({ convId, title }) { return changeVisit(convId, { title }) }
function archiveVisit(id) { return changeVisit(id, { archived: true }) }
function restoreVisit(id) { return changeVisit(id, { archived: false }) }

async function sendMessage(value) {
  const content = (typeof value === 'string' ? value : draft.value).trim()
  if (!content || !canChat.value || operationPending.value) return
  const snapshot = requestSettings()
  const epoch = workspaceEpoch
  pendingOperations[snapshot.conversationId] = { type: 'chat' }
  draft.value = ''
  chatError.value = ''
  operationReceipt.value = null
  try {
    const response = await requestChat(snapshot.backend, snapshot, content)
    if (!isCurrent(epoch)) return
    checkIdentity(response.visit, snapshot)
    if (await readHistory(snapshot, epoch)) {
      lastResponse.value = response
      lastTrace.value = response.raw.tool_traces || []
    }
    await loadMonitor()
  } catch (error) {
    if (isCurrent(epoch)) {
      chatError.value = readableError(error)
      draft.value = content
      // A failed request may still have persisted a user message; only the server can supply it.
      try { await readHistory(snapshot, epoch) } catch { /* Keep the visible error and prior authoritative history. */ }
    }
  } finally { delete pendingOperations[snapshot.conversationId] }
}
function usePrompt(prompt) { draft.value = prompt }
async function submitReport(file) {
  if (!file || !canChat.value || operationPending.value) return
  const snapshot = requestSettings(), epoch = workspaceEpoch
  pendingOperations[snapshot.conversationId] = { type: 'report' }
  chatError.value = ''
  try {
    const result = await uploadReport(snapshot.backend, snapshot, file)
    if (!isCurrent(epoch)) return
    checkIdentity(result, snapshot)
    await readHistory(snapshot, epoch)
  } catch (error) {
    if (isCurrent(epoch)) {
      chatError.value = readableError(error)
      try { await readHistory(snapshot, epoch) } catch { /* Keep the original upload error. */ }
    }
  } finally { delete pendingOperations[snapshot.conversationId] }
}
function selectSlot({ slotId }) { return sendMessage(`请为当前就诊人准备预约，号源编号：${slotId}`) }
function prepareCancellation(id) { return sendMessage(`请准备取消预约，预约编号：${id}`) }
async function confirmProposal(id) {
  if (!canChat.value || operationPending.value || proposalStates.value[id] !== 'pending') return
  const snapshot = requestSettings()
  const epoch = workspaceEpoch
  pendingOperations[snapshot.conversationId] = { type: 'confirm', proposalId: id }
  chatError.value = ''
  operationReceipt.value = null
  try {
    const result = await confirmAppointmentProposal(snapshot.backend, snapshot, id, snapshot.conversationId)
    if (!isCurrent(epoch)) return
    checkIdentity(result, snapshot)
    operationReceipt.value = result.receipt
    try { await readHistory(snapshot, epoch) }
    catch { if (isCurrent(epoch)) chatError.value = '办理已完成，但完整记录暂未刷新。请点击“刷新记录”，无需重新办理。' }
  } catch (error) {
    if (isCurrent(epoch)) {
      chatError.value = readableError(error)
      if (error.code === 'proposal_expired') rejectedProposals.value[id] = 'expired'
      if (error.code === 'proposal_superseded') rejectedProposals.value[id] = 'superseded'
    }
  }
  finally { delete pendingOperations[snapshot.conversationId] }
}
function cancellableRecords(message) {
  return (message.artifacts || []).filter(item => item.type === 'appointment_record')
    .map(item => item.data).filter(record => record.status === 'active' && latestAppointments.value[record.appointment_id]?.status === 'active' && latestAppointments.value[record.appointment_id]?.messageId === message.message_id)
}
function agentLabel(type) {
  const role = typeof type === 'string' ? type.replace(/_\d+$/, '') : type
  return { general: '医院信息', guidance: '就诊指引', appointment: '预约事务', escalation: '导诊联系', triage: '症状分诊', medication: '用药信息' }[role] || type || '就诊助手'
}
function messageLabel(item) {
  if (item.metadata?.processing === 'report_preprocessor') return '报告整理'
  if (item.kind === 'operation_result') return '业务办理回执'
  if (item.kind === 'confirmation_event') return '你的确认'
  return item.role === 'user' ? '你' : agentLabel(item.metadata?.primary_agent || item.metadata?.agent_type)
}
function messageTime(value) { return value ? new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Shanghai' }) : '' }

const baselineAccepted = computed(() => Boolean(evalData.value?.accepted_by?.trim() && evalData.value?.metadata?.baseline_status === 'accepted'))
const visibleEvalResults = computed(() => (evalData.value?.results || []).filter(result => !failedOnly.value || !result.passed || result.metadata?.status === 'skipped'))
const evaluationProblems = computed(() => [
  { label: 'Judge 失败', ids: evalData.value?.judge_failures || [] },
  { label: '调用失败', ids: evalData.value?.call_failures || [] },
  { label: '跳过', ids: evalData.value?.skipped || [] },
  { label: '业务断言失败', ids: evalData.value?.metadata?.business_failures || [] }
].filter(group => group.ids.length))
function metricPercent(value) { return value == null || !Number.isFinite(Number(value)) ? '未返回' : `${(Number(value) * 100).toFixed(1)}%` }
function scoreLabel(key) { return { relevance: '相关性', accuracy: '准确性', completeness: '完整性', helpfulness: '有用性', overall: '综合', intent_accuracy: '意图准确率' }[key] || key }
function retrievalIssues(result) {
  return [result.rewrite_error && `查询改写失败：${result.rewrite_error}`, result.rerank_error && `重排失败：${result.rerank_error}`,
    ...(result.recall_errors || []).map(error => `召回失败：${typeof error === 'string' ? error : formatJson(error)}`),
    result.partial && '仅部分召回成功；下方只展示有效结果。', result.fallback_used && '发生了降级；降级内容不作为检索来源。'].filter(Boolean)
}
function sourceNames(metadata = {}, artifacts = []) {
  const sources = [...(metadata.sources || [])]
  for (const trace of metadata.tool_traces || []) sources.push(...(trace.sources || []), ...(trace.result_summary?.sources || []))
  for (const artifact of artifacts || []) {
    if (artifact.data?.source) sources.push(artifact.data.source)
    for (const item of artifact.data?.items || []) if (item?.source) sources.push(item.source)
  }
  const labels = sources.map(source => typeof source === 'string' ? source : [source.title, source.source, source.source_id, source.doc_id, source.chunk_id].filter(Boolean).join(' · ')).filter(Boolean)
  return [...new Set(labels)].join('；') || '本次未返回来源'
}
async function searchKnowledge() {
  if (searchLoading.value || !searchQuery.value.trim()) return
  searchLoading.value = true
  searchError.value = ''
  searchResults.value = []
  searchReport.value = null
  try {
    const data = await requestSearch(settings.backend, settings, searchQuery.value.trim(), 5)
    searchReport.value = data
    if (data.success === false) searchError.value = data.error || '检索未完成，请稍后重试。'
    else searchResults.value = Array.isArray(data.results) ? data.results : []
  } catch (error) { searchError.value = readableError(error) }
  finally { searchLoading.value = false }
}
async function submitKnowledge() {
  if (importLoading.value || !docTitle.value.trim() || !docContent.value.trim()) return
  importLoading.value = true
  importError.value = ''; importNotice.value = ''
  try {
    const document = { title: docTitle.value.trim(), content: docContent.value.trim() }
    if (docSource.value.trim()) document.source = docSource.value.trim()
    if (docSourceId.value.trim()) document.source_id = docSourceId.value.trim()
    const data = await addKnowledge(settings.backend, settings, [document])
    importNotice.value = `${data.message || '导入完成'}；本次新增 ${data.added_chunks ?? '未返回'} 个片段。`
    await loadStats()
  } catch (error) { importError.value = readableError(error) }
  finally { importLoading.value = false }
}
async function handleUpload(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || importLoading.value) return
  importLoading.value = true
  importError.value = ''; importNotice.value = ''
  try {
    const data = await uploadKnowledge(settings.backend, settings, file)
    importNotice.value = `${data.message || '文件导入完成'}；本次新增 ${data.added_chunks ?? '未返回'} 个片段。`
    await loadStats()
  } catch (error) { importError.value = readableError(error) }
  finally { importLoading.value = false }
}
async function runEvaluation() {
  if (evalLoading.value) return
  evalLoading.value = true
  evalError.value = ''
  evalData.value = null
  failedOnly.value = false
  try { evalData.value = await requestEvaluation(settings.backend, settings) }
  catch (error) { evalError.value = readableError(error) }
  finally { evalLoading.value = false }
}

function formatPercent(value) {
  const number = Number(value || 0)
  return `${(number <= 1 ? number * 100 : number).toFixed(1)}%`
}

function formatJson(value) {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return String(value ?? '')
  }
}

function showToast(message) {
  toast.value = message
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = '' }, 2600)
}
</script>
