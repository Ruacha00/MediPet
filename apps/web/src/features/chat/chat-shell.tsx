"use client";

import { use, useEffect, useRef, useState } from "react";
import { useChat } from "@ai-sdk/react";
import {
  ArrowUp,
  Bot,
  Building2,
  CirclePlus,
  ClipboardCheck,
  HeartPulse,
  ShieldCheck,
  Square,
} from "lucide-react";

import { MessagePartView } from "./message-parts";
import type { CapabilityStatus } from "./capabilities";
import type { ConversationHistoryRestoration } from "./history";
import type {
  ActionProposalData,
  ConversationMessageState,
  MediPetMessage,
  ProposalDecision,
  ProposalDecisionState,
  SlotOption,
} from "./message-types";
import {
  chatTransport,
  decideProposal,
} from "./transport";

const suggestions = [
  { label: '整理症状', prompt: '请帮我整理这次就诊要描述的主要不适', note: '把症状和时间线说清楚' },
  { label: '准备提问', prompt: '初次门诊前，我应该准备向医生询问哪些问题？', note: '整理就诊前的问题清单' },
  { label: '了解流程', prompt: '请介绍一般门诊就诊前需要做哪些准备', note: '了解通用流程，不使用医院数据' },
];
const unavailableCapabilities = Promise.resolve({ hospitalDataAvailable: false });

export function ChatShell({
  history,
  capabilityStatus,
}: {
  history: Promise<ConversationHistoryRestoration>;
  capabilityStatus?: Promise<CapabilityStatus>;
}) {
  const restoredHistory = use(history);
  const capabilities = use(capabilityStatus ?? unavailableCapabilities);
  const [input, setInput] = useState("");
  const [agentStatus, setAgentStatus] = useState<string | null>(null);
  const [decisionStates, setDecisionStates] = useState<Record<string, ProposalDecisionState>>({});
  const threadEndRef = useRef<HTMLDivElement>(null);

  const { messages, sendMessage, status, stop, setMessages, error } = useChat<MediPetMessage>({
    messages: restoredHistory.messages,
    transport: chatTransport,
    onData: (part) => {
      if (part.type === "data-agent-status") {
        setAgentStatus(part.data.label);
      }
    },
    onFinish: () => setAgentStatus(null),
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
      const updated = await decideProposal(proposalId, decision);
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

  return (
    <main className="app-shell">
      <aside className="left-rail panel" aria-label="就诊事项">
        <div className="brand">
          <span className="brand-mark"><HeartPulse size={23} aria-hidden="true" /></span>
          <span><strong>MediPet</strong><small>门诊就诊助手</small></span>
        </div>

        <button className="new-visit" onClick={() => setMessages([])}>
          <CirclePlus size={17} aria-hidden="true" /> 新建就诊事项
        </button>

        <p className="rail-label">当前事项</p>
        <nav className="visit-list">
          <button className="visit-item active">
            <span>演示患者 · 初次咨询</span>
            <small>刚刚更新</small>
          </button>
        </nav>

        <div className="rail-footer">
          <ShieldCheck size={16} aria-hidden="true" />
          <p>一次就诊事项只对应一位患者。确认操作前请核对患者与预约信息。</p>
        </div>
      </aside>

      <section className="chat-panel panel" aria-label="MediPet 对话">
        <header className="chat-header">
          <div>
            <h2 className="chat-title">门诊协助</h2>
            <p className="chat-subtitle">模型连接验证 · 诊前阶段</p>
          </div>
          <span className="online-badge">开发环境</span>
        </header>

        <div className="thread" aria-live="polite">
          {restoredHistory.failed && (
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
          <form
            className="composer"
            onSubmit={(event) => {
              event.preventDefault();
              submit(input);
            }}
          >
            <textarea
              aria-label="输入就诊需求"
              placeholder="描述主要不适，或询问一般门诊准备…"
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
          <p className="composer-note">MediPet 提供非诊断性就诊协助，不能替代医生判断。</p>
        </footer>
      </section>

      <aside className="right-rail panel" aria-label="就诊上下文">
        <section className="context-card">
          <h2>本次就诊</h2>
          <div className="context-row"><span>患者</span><strong>演示患者</strong></div>
          <div className="context-row"><span>参与者</span><strong>患者本人</strong></div>
          <div className="context-row"><span>授权状态</span><strong>本人操作</strong></div>
        </section>

        <section className="context-card">
          <h2>就诊阶段</h2>
          <div className="stage-track">
            <div className="stage active">诊前准备</div>
            <div className="stage">诊中协助</div>
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
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
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
