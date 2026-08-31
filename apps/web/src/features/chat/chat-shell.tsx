"use client";

import { use, useEffect, useMemo, useRef, useState } from "react";
import { useChat } from "@ai-sdk/react";
import {
  ArrowUp,
  Bold,
  Bot,
  Building2,
  CirclePlus,
  ClipboardCheck,
  Code2,
  Eye,
  EyeOff,
  HeartPulse,
  List,
  ListOrdered,
  ShieldCheck,
  Square,
} from "lucide-react";

import { MarkdownText, MessagePartView } from "./message-parts";
import type { CapabilityStatus } from "./capabilities";
import {
  backendBaseUrl,
  demoParticipantId,
  demoVisitMatterId,
} from "./chat-config";
import {
  loadConversationHistory,
  type ConversationHistoryRestoration,
} from "./history";
import type {
  ActionProposalData,
  ConversationMessageState,
  MediPetMessage,
  ProposalDecision,
  ProposalDecisionState,
  SlotOption,
} from "./message-types";
import {
  createChatTransport,
  decideProposal,
} from "./transport";
import {
  createVisitMatter,
  type VisitMatterSummary,
} from "./visit-matters";

const suggestions = [
  { label: '整理症状', prompt: '请帮我整理这次就诊要描述的主要不适', note: '把症状和时间线说清楚' },
  { label: '准备提问', prompt: '初次门诊前，我应该准备向医生询问哪些问题？', note: '整理就诊前的问题清单' },
  { label: '了解流程', prompt: '请介绍一般门诊就诊前需要做哪些准备', note: '了解通用流程，不使用医院数据' },
];
const unavailableCapabilities = Promise.resolve({ hospitalDataAvailable: false });
const fallbackVisitMatterList: VisitMatterSummary[] = [
  {
    visit_matter_id: demoVisitMatterId,
    title: "初次咨询",
    visit_stage: "pre_visit",
    patient_display_name: "演示患者",
    participant_display_name: "患者本人",
  },
];
const fallbackVisitMatters = Promise.resolve(fallbackVisitMatterList);

export function ChatShell({
  history,
  capabilityStatus,
  visitMatters,
}: {
  history: Promise<ConversationHistoryRestoration>;
  capabilityStatus?: Promise<CapabilityStatus>;
  visitMatters?: Promise<VisitMatterSummary[]>;
}) {
  const restoredHistory = use(history);
  const capabilities = use(capabilityStatus ?? unavailableCapabilities);
  const restoredVisitMatters = use(visitMatters ?? fallbackVisitMatters);
  const [input, setInput] = useState("");
  const [showPreview, setShowPreview] = useState(false);
  const [agentStatus, setAgentStatus] = useState<string | null>(null);
  const [visitMatterError, setVisitMatterError] = useState<string | null>(null);
  const [visitMatterBusy, setVisitMatterBusy] = useState(false);
  const [historyFailed, setHistoryFailed] = useState(restoredHistory.failed);
  const [availableVisitMatters, setAvailableVisitMatters] = useState(
    restoredVisitMatters.length > 0 ? restoredVisitMatters : fallbackVisitMatterList,
  );
  const [activeVisitMatterId, setActiveVisitMatterId] = useState(
    availableVisitMatters.some((item) => item.visit_matter_id === demoVisitMatterId)
      ? demoVisitMatterId
      : availableVisitMatters[0].visit_matter_id,
  );
  const [initialMessages, setInitialMessages] = useState(restoredHistory.messages);
  const [decisionStates, setDecisionStates] = useState<Record<string, ProposalDecisionState>>({});
  const threadEndRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const chatTransport = useMemo(
    () => createChatTransport(activeVisitMatterId, demoParticipantId),
    [activeVisitMatterId],
  );
  const activeVisitMatter = availableVisitMatters.find(
    (item) => item.visit_matter_id === activeVisitMatterId,
  );

  const { messages, sendMessage, status, stop, setMessages, error } = useChat<MediPetMessage>({
    id: activeVisitMatterId,
    messages: initialMessages,
    transport: chatTransport,
    onData: (part) => {
      if (part.type === "data-agent-status") {
        setAgentStatus(part.data.label);
      }
    },
    onFinish: () => {
      setAgentStatus(null);
      setAvailableVisitMatters((current) => moveVisitMatterFirst(current, activeVisitMatterId));
    },
    onError: () => setAgentStatus(null),
  });

  const active = status === "submitted" || status === "streaming";

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, agentStatus]);

  async function submit(text: string) {
    const value = text.trim();
    if (!value || active) return;
    setInput("");
    await sendMessage({ text: value });
  }

  async function handleDecision(proposalId: string, decision: ProposalDecision) {
    setAgentStatus(null);
    setDecisionStates((current) => ({ ...current, [proposalId]: "working" }));
    try {
      const updated = await decideProposal(
        proposalId,
        decision,
        activeVisitMatterId,
        demoParticipantId,
      );
      if (updated) {
        setMessages((current) => replaceProposalData(current, proposalId, updated));
      }
      setDecisionStates((current) => ({ ...current, [proposalId]: null }));
    } catch (decisionError) {
      setDecisionStates((current) => ({ ...current, [proposalId]: null }));
      setAgentStatus(
        decisionError instanceof Error
          ? decisionError.message
          : "操作方案处理失败，请稍后重试。",
      );
    }
  }

  async function handleSelectSlot(slot: SlotOption) {
    if (active) return;
    await sendMessage(
      {
        text: `我选择 ${slot.department} ${slot.doctor} 医生在 ${formatSlotTime(slot.startsAt)} 的号源`,
      },
      { body: { selected_slot_id: slot.id } },
    );
  }

  async function handleCreateVisitMatter() {
    if (active || visitMatterBusy) return;
    setVisitMatterBusy(true);
    setVisitMatterError(null);
    try {
      const created = await createVisitMatter(
        backendBaseUrl,
        demoParticipantId,
      );
      setAvailableVisitMatters((current) => [
        created,
        ...current.filter((item) => item.visit_matter_id !== created.visit_matter_id),
      ]);
      setInitialMessages([]);
      setActiveVisitMatterId(created.visit_matter_id);
      setHistoryFailed(false);
      setDecisionStates({});
      setInput("");
    } catch (creationError) {
      setVisitMatterError(
        creationError instanceof Error
          ? creationError.message
          : "新建就诊事项失败，请稍后重试。",
      );
    } finally {
      setVisitMatterBusy(false);
    }
  }

  async function handleSelectVisitMatter(visitMatterId: string) {
    if (
      active
      || visitMatterBusy
      || visitMatterId === activeVisitMatterId
    ) return;
    setVisitMatterBusy(true);
    setVisitMatterError(null);
    try {
      const nextMessages = await loadConversationHistory(
        backendBaseUrl,
        visitMatterId,
        demoParticipantId,
      );
      setInitialMessages(nextMessages);
      setActiveVisitMatterId(visitMatterId);
      setHistoryFailed(false);
      setDecisionStates({});
      setInput("");
    } catch {
      setVisitMatterError("该就诊事项暂时无法打开，请稍后重试。");
    } finally {
      setVisitMatterBusy(false);
    }
  }

  function applyMarkdown(before: string, after: string, placeholder: string) {
    const textarea = composerRef.current;
    const selectionStart = textarea?.selectionStart ?? input.length;
    const selectionEnd = textarea?.selectionEnd ?? input.length;
    const selection = input.slice(selectionStart, selectionEnd) || placeholder;
    const nextInput = [
      input.slice(0, selectionStart),
      before,
      selection,
      after,
      input.slice(selectionEnd),
    ].join("");
    setInput(nextInput);
    requestAnimationFrame(() => {
      const nextSelectionStart = selectionStart + before.length;
      textarea?.focus();
      textarea?.setSelectionRange(nextSelectionStart, nextSelectionStart + selection.length);
    });
  }

  return (
    <main className="app-shell">
      <aside className="left-rail panel" aria-label="就诊事项">
        <div className="brand">
          <span className="brand-mark"><HeartPulse size={23} aria-hidden="true" /></span>
          <span><strong>MediPet</strong><small>门诊就诊助手</small></span>
        </div>

        <button
          className="new-visit"
          disabled={active || visitMatterBusy}
          onClick={handleCreateVisitMatter}
        >
          <CirclePlus size={17} aria-hidden="true" />
          {visitMatterBusy ? "正在准备…" : "新建就诊事项"}
        </button>

        <p className="rail-label">就诊事项</p>
        <nav className="visit-list" aria-label="就诊事项列表">
          {availableVisitMatters.map((visitMatter) => {
            const current = visitMatter.visit_matter_id === activeVisitMatterId;
            return (
              <button
                aria-current={current ? "page" : undefined}
                className={`visit-item ${current ? "active" : ""}`}
                disabled={active || visitMatterBusy}
                key={visitMatter.visit_matter_id}
                onClick={() => handleSelectVisitMatter(visitMatter.visit_matter_id)}
              >
                <span>{visitMatter.patient_display_name} · {visitMatter.title}</span>
                <small>{visitStageLabel(visitMatter.visit_stage)}</small>
              </button>
            );
          })}
        </nav>
        {visitMatterError && <p className="rail-error" role="alert">{visitMatterError}</p>}

        <div className="rail-footer">
          <ShieldCheck size={16} aria-hidden="true" />
          <p>一次就诊事项只对应一位患者。确认操作前请核对患者与预约信息。</p>
        </div>
      </aside>

      <section className="chat-panel panel" aria-label="MediPet 对话">
        <header className="chat-header">
          <div className="chat-heading">
            <h2 className="chat-title">{activeVisitMatter?.title ?? "门诊协助"}</h2>
            <p className="chat-subtitle">
              独立就诊上下文 · {visitStageLabel(activeVisitMatter?.visit_stage ?? "pre_visit")}
            </p>
          </div>
          <span className="online-badge">开发环境</span>
          <div className="mobile-visit-controls">
            <select
              aria-label="切换就诊事项"
              disabled={active || visitMatterBusy}
              onChange={(event) => handleSelectVisitMatter(event.target.value)}
              value={activeVisitMatterId}
            >
              {availableVisitMatters.map((visitMatter) => (
                <option key={visitMatter.visit_matter_id} value={visitMatter.visit_matter_id}>
                  {visitMatter.title}
                </option>
              ))}
            </select>
            <button
              aria-label="在移动端新建就诊事项"
              disabled={active || visitMatterBusy}
              onClick={handleCreateVisitMatter}
              type="button"
            >
              <CirclePlus size={17} aria-hidden="true" />
            </button>
          </div>
        </header>

        <div className="thread" aria-live="polite">
          {historyFailed && (
            <div className="data-card urgent">历史对话暂时无法恢复，请稍后刷新重试。</div>
          )}
          {messages.length === 0 ? (
            <section className="welcome">
              <div className="welcome-orbit"><Bot size={34} aria-hidden="true" /></div>
              <h1>把复杂的门诊流程，变成一次从容的对话。</h1>
              <p>
                告诉我患者这次就诊最想解决的问题。我可以协助整理症状陈述、
                准备就诊问题和了解一般门诊流程。
              </p>
              <div className="suggestions">
                {suggestions.map((suggestion) => (
                  <button
                    className="suggestion"
                    key={suggestion.label}
                    onClick={() => submit(suggestion.prompt)}
                  >
                    <strong>{suggestion.label}</strong>
                    <span>{suggestion.note}</span>
                  </button>
                ))}
              </div>
            </section>
          ) : (
            <div className="message-list">
              {messages.map((message) => {
                const participant = message.role === "user";
                return (
                  <article
                    className={`message-row ${participant ? "participant" : "assistant"}`}
                    key={message.id}
                  >
                    {!participant && <span className="avatar"><Bot size={18} /></span>}
                    <div className="message-stack">
                      {message.parts.map((part, index) => {
                        const proposalId =
                          part.type === "data-action-proposal" ? part.data.proposalId : null;
                        return (
                          <MessagePartView
                            key={`${message.id}-${part.type}-${index}`}
                            part={part}
                            decisionState={proposalId ? decisionStates[proposalId] ?? null : null}
                            onDecision={handleDecision}
                            onSelectSlot={handleSelectSlot}
                          />
                        );
                      })}
                      <MessageLifecycleNote state={message.metadata?.state} />
                    </div>
                  </article>
                );
              })}
              {agentStatus && <div className="status-line">{agentStatus}</div>}
              {error && <div className="data-card urgent">连接暂时中断，请检查后端服务后重试。</div>}
              <div ref={threadEndRef} />
            </div>
          )}
        </div>

        <footer className="composer-wrap">
          <div className="composer-shell">
            <div className="composer-toolbar" aria-label="Markdown 格式工具">
              <span className="markdown-label">Markdown</span>
              <div className="format-actions">
                <button
                  aria-label="加粗"
                  disabled={active}
                  onClick={() => applyMarkdown("**", "**", "重点内容")}
                  title="加粗"
                  type="button"
                ><Bold size={15} /></button>
                <button
                  aria-label="行内代码"
                  disabled={active}
                  onClick={() => applyMarkdown("`", "`", "代码或配置")}
                  title="行内代码"
                  type="button"
                ><Code2 size={15} /></button>
                <button
                  aria-label="无序列表"
                  disabled={active}
                  onClick={() => applyMarkdown("- ", "", "列表项")}
                  title="无序列表"
                  type="button"
                ><List size={15} /></button>
                <button
                  aria-label="有序列表"
                  disabled={active}
                  onClick={() => applyMarkdown("1. ", "", "列表项")}
                  title="有序列表"
                  type="button"
                ><ListOrdered size={15} /></button>
              </div>
              <button
                aria-label={showPreview ? "关闭 Markdown 预览" : "打开 Markdown 预览"}
                aria-pressed={showPreview}
                className={`preview-toggle ${showPreview ? "active" : ""}`}
                onClick={() => setShowPreview((current) => !current)}
                type="button"
              >
                {showPreview ? <EyeOff size={15} /> : <Eye size={15} />}
                {showPreview ? "继续编辑" : "预览"}
              </button>
            </div>
            {showPreview && input.trim() && (
              <div className="composer-preview-pane">
                <small>发送效果预览</small>
                <MarkdownText className="composer-preview" text={input} />
              </div>
            )}
            <form
              className="composer"
              onSubmit={(event) => {
                event.preventDefault();
                submit(input);
              }}
            >
              <textarea
                aria-label="输入就诊需求"
                placeholder="描述主要不适，或用 Markdown 整理时间线与问题清单…"
                ref={composerRef}
                rows={1}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    submit(input);
                  }
                }}
              />
              <button
                className="send-button"
                type={active ? "button" : "submit"}
                aria-label={active ? "停止生成" : "发送消息"}
                disabled={!active && !input.trim()}
                onClick={active ? () => stop() : undefined}
              >
                {active ? <Square size={16} fill="currentColor" /> : <ArrowUp size={19} />}
              </button>
            </form>
          </div>
          <p className="composer-note">MediPet 提供非诊断性就诊协助，不能替代医生判断。</p>
        </footer>
      </section>

      <aside className="right-rail panel" aria-label="就诊上下文">
        <section className="context-card">
          <h2>本次就诊</h2>
          <div className="context-row">
            <span>患者</span><strong>{activeVisitMatter?.patient_display_name ?? "演示患者"}</strong>
          </div>
          <div className="context-row">
            <span>参与者</span>
            <strong>{activeVisitMatter?.participant_display_name ?? "患者本人"}</strong>
          </div>
          <div className="context-row"><span>授权状态</span><strong>本人操作</strong></div>
        </section>

        <section className="context-card">
          <h2>就诊阶段</h2>
          <div className="stage-track">
            <div className={`stage ${activeVisitMatter?.visit_stage !== "in_visit" ? "active" : ""}`}>
              诊前准备
            </div>
            <div className={`stage ${activeVisitMatter?.visit_stage === "in_visit" ? "active" : ""}`}>
              诊中协助
            </div>
          </div>
        </section>

        <section className="context-card">
          <h2>可用协助</h2>
          <div className="context-row"><span><Building2 size={14} /> 对话协助</span><strong>可用</strong></div>
          <div className="context-row">
            <span><ClipboardCheck size={14} /> 医院数据</span>
            <strong>{capabilities.hospitalDataAvailable ? "已连接" : "未配置"}</strong>
          </div>
        </section>
      </aside>
    </main>
  );
}

function replaceProposalData(
  messages: MediPetMessage[],
  proposalId: string,
  data: ActionProposalData,
) {
  return messages.map((message) => ({
    ...message,
    parts: message.parts.map((part) => (
      part.type === "data-action-proposal" && part.data.proposalId === proposalId
        ? { ...part, data }
        : part
    )),
  }));
}

function formatSlotTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function moveVisitMatterFirst(
  visitMatters: VisitMatterSummary[],
  visitMatterId: string,
) {
  const activeVisitMatter = visitMatters.find(
    (visitMatter) => visitMatter.visit_matter_id === visitMatterId,
  );
  return activeVisitMatter
    ? [
        activeVisitMatter,
        ...visitMatters.filter(
          (visitMatter) => visitMatter.visit_matter_id !== visitMatterId,
        ),
      ]
    : visitMatters;
}

function visitStageLabel(visitStage: VisitMatterSummary["visit_stage"]) {
  return visitStage === "in_visit" ? "诊中协助" : "诊前准备";
}

function MessageLifecycleNote({
  state,
}: {
  state: ConversationMessageState | undefined;
}) {
  const labels: Partial<Record<ConversationMessageState, string>> = {
    pending: "上次生成尚未开始",
    streaming: "上次生成未完成",
    failed: "生成失败",
    cancelled: "已停止生成",
  };
  const label = state ? labels[state] : undefined;
  return label ? <small className="message-lifecycle">{label}</small> : null;
}
