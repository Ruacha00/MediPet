"use client";

import { use, useEffect, useMemo, useRef, useState } from "react";
import { useChat } from "@ai-sdk/react";
import {
  ArrowUp,
  Archive,
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
  Pencil,
  RotateCcw,
  ShieldCheck,
  Square,
} from "lucide-react";

import { MarkdownText, MessagePartView } from "./message-parts";
import type { CapabilityStatus } from "./capabilities";
import {
  backendBaseUrl,
  demoParticipantId,
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
  archiveVisitMatter,
  createVisitMatter,
  loadVisitMatters,
  renameVisitMatter,
  restoreVisitMatter,
  type VisitMatterSummary,
} from "./visit-matters";

const suggestions = [
  { label: '整理症状', prompt: '请帮我整理这次就诊要描述的主要不适', note: '把症状和时间线说清楚' },
  { label: '准备提问', prompt: '初次门诊前，我应该准备向医生询问哪些问题？', note: '整理就诊前的问题清单' },
  { label: '了解流程', prompt: '请介绍一般门诊就诊前需要做哪些准备', note: '了解通用流程，不使用医院数据' },
];
const unavailableCapabilities = Promise.resolve({ hospitalDataAvailable: false });
const emptyVisitMatters = Promise.resolve<VisitMatterSummary[]>([]);

export function ChatShell({
  history,
  capabilityStatus,
  visitMatters,
  visitMattersFailed = false,
}: {
  history: Promise<ConversationHistoryRestoration>;
  capabilityStatus?: Promise<CapabilityStatus>;
  visitMatters?: Promise<VisitMatterSummary[]>;
  visitMattersFailed?: boolean;
}) {
  const restoredHistory = use(history);
  const capabilities = use(capabilityStatus ?? unavailableCapabilities);
  const restoredVisitMatters = use(visitMatters ?? emptyVisitMatters);
  const [agentStatus, setAgentStatus] = useState<string | null>(null);
  const [visitMatterError, setVisitMatterError] = useState<string | null>(null);
  const [visitMatterBusy, setVisitMatterBusy] = useState(false);
  const [historyFailed, setHistoryFailed] = useState(restoredHistory.failed);
  const [visitMatterListFailed, setVisitMatterListFailed] = useState(visitMattersFailed);
  const [availableVisitMatters, setAvailableVisitMatters] = useState(restoredVisitMatters);
  const [archivedVisitMatters, setArchivedVisitMatters] = useState<VisitMatterSummary[]>([]);
  const [archivedLoaded, setArchivedLoaded] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [selectedVisitMatterId, setSelectedVisitMatterId] = useState<string | null>(
    restoredVisitMatters[0]?.visit_matter_id ?? null,
  );
  const [editingVisitMatterId, setEditingVisitMatterId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [initialMessages, setInitialMessages] = useState(restoredHistory.messages);
  const [decisionStates, setDecisionStates] = useState<Record<string, ProposalDecisionState>>({});
  const threadEndRef = useRef<HTMLDivElement>(null);
  const chatTransport = useMemo(
    () => createChatTransport(selectedVisitMatterId ?? "no-active-visit", demoParticipantId),
    [selectedVisitMatterId],
  );
  const displayedVisitMatters = showArchived ? archivedVisitMatters : availableVisitMatters;
  const selectedVisitMatter = displayedVisitMatters.find(
    (item) => item.visit_matter_id === selectedVisitMatterId,
  );

  const { messages, sendMessage, status, stop, setMessages, error } = useChat<MediPetMessage>({
    id: selectedVisitMatterId ?? "no-active-visit",
    messages: initialMessages,
    transport: chatTransport,
    onData: (part) => {
      if (part.type === "data-agent-status") {
        setAgentStatus(part.data.label);
      }
    },
    onFinish: () => {
      setAgentStatus(null);
      if (selectedVisitMatterId && !showArchived) {
        setAvailableVisitMatters((current) => (
          moveVisitMatterFirst(current, selectedVisitMatterId)
        ));
      }
    },
    onError: () => setAgentStatus(null),
  });

  const active = status === "submitted" || status === "streaming";

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      threadEndRef.current?.scrollIntoView({ behavior: active ? "auto" : "smooth" });
    });
    return () => cancelAnimationFrame(frame);
  }, [messages, agentStatus, active]);

  async function submit(text: string) {
    const value = text.trim();
    if (!value || active || showArchived || !selectedVisitMatterId) return;
    await sendMessage({ text: value });
  }

  async function handleDecision(proposalId: string, decision: ProposalDecision) {
    if (!selectedVisitMatterId || showArchived) return;
    setAgentStatus(null);
    setDecisionStates((current) => ({ ...current, [proposalId]: "working" }));
    try {
      const updated = await decideProposal(
        proposalId,
        decision,
        selectedVisitMatterId,
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
    if (active || showArchived || !selectedVisitMatterId) return;
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
      setShowArchived(false);
      setInitialMessages([]);
      setSelectedVisitMatterId(created.visit_matter_id);
      setHistoryFailed(false);
      setDecisionStates({});
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
      || visitMatterId === selectedVisitMatterId
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
      setSelectedVisitMatterId(visitMatterId);
      setHistoryFailed(false);
      setDecisionStates({});
    } catch {
      setVisitMatterError("该就诊事项暂时无法打开，请稍后重试。");
    } finally {
      setVisitMatterBusy(false);
    }
  }

  async function handleShowArchived() {
    if (active || visitMatterBusy) return;
    if (showArchived) {
      setShowArchived(false);
      const next = availableVisitMatters[0];
      if (next) await selectVisitMatterAfterLifecycle(next.visit_matter_id);
      else clearSelectedVisitMatter();
      return;
    }
    setVisitMatterBusy(true);
    setVisitMatterError(null);
    try {
      const archived = archivedLoaded
        ? archivedVisitMatters
        : await loadVisitMatters(backendBaseUrl, demoParticipantId, true);
      setArchivedVisitMatters(archived);
      setArchivedLoaded(true);
      setShowArchived(true);
      if (archived[0]) await selectVisitMatterAfterLifecycle(archived[0].visit_matter_id);
      else clearSelectedVisitMatter();
    } catch (loadError) {
      setVisitMatterError(
        loadError instanceof Error ? loadError.message : "已归档历史加载失败，请稍后重试。",
      );
    } finally {
      setVisitMatterBusy(false);
    }
  }

  async function handleArchive(visitMatter: VisitMatterSummary) {
    if (active || visitMatterBusy) return;
    setVisitMatterBusy(true);
    setVisitMatterError(null);
    try {
      const archived = await archiveVisitMatter(
        backendBaseUrl,
        visitMatter.visit_matter_id,
        demoParticipantId,
      );
      const remaining = availableVisitMatters.filter(
        (item) => item.visit_matter_id !== visitMatter.visit_matter_id,
      );
      setAvailableVisitMatters(remaining);
      setArchivedVisitMatters((current) => [
        ...current.filter((item) => item.visit_matter_id !== archived.visit_matter_id),
        archived,
      ]);
      setArchivedLoaded(true);
      if (selectedVisitMatterId === visitMatter.visit_matter_id) {
        if (remaining[0]) await selectVisitMatterAfterLifecycle(remaining[0].visit_matter_id);
        else clearSelectedVisitMatter();
      }
      try {
        const [activeItems, archivedItems] = await Promise.all([
          loadVisitMatters(backendBaseUrl, demoParticipantId),
          loadVisitMatters(backendBaseUrl, demoParticipantId, true),
        ]);
        setAvailableVisitMatters(activeItems);
        setArchivedVisitMatters(archivedItems);
      } catch {
        setVisitMatterError("归档已完成，但历史记录列表刷新失败，请稍后重试。");
      }
    } catch (archiveError) {
      setVisitMatterError(
        archiveError instanceof Error ? archiveError.message : "归档失败，请稍后重试。",
      );
    } finally {
      setVisitMatterBusy(false);
    }
  }

  async function handleRestore(visitMatter: VisitMatterSummary) {
    if (visitMatterBusy) return;
    setVisitMatterBusy(true);
    setVisitMatterError(null);
    try {
      const restored = await restoreVisitMatter(
        backendBaseUrl,
        visitMatter.visit_matter_id,
        demoParticipantId,
      );
      setArchivedVisitMatters((current) => current.filter(
        (item) => item.visit_matter_id !== restored.visit_matter_id
      ));
      setAvailableVisitMatters((current) => [
        ...current.filter((item) => item.visit_matter_id !== restored.visit_matter_id),
        restored,
      ]);
      setArchivedLoaded(true);
      setShowArchived(false);
      await selectVisitMatterAfterLifecycle(restored.visit_matter_id);
      try {
        const [activeItems, archivedItems] = await Promise.all([
          loadVisitMatters(backendBaseUrl, demoParticipantId),
          loadVisitMatters(backendBaseUrl, demoParticipantId, true),
        ]);
        setAvailableVisitMatters(activeItems);
        setArchivedVisitMatters(archivedItems);
      } catch {
        setVisitMatterError("恢复已完成，但历史记录列表刷新失败，请稍后重试。");
      }
    } catch (restoreError) {
      setVisitMatterError(
        restoreError instanceof Error ? restoreError.message : "恢复失败，请稍后重试。",
      );
    } finally {
      setVisitMatterBusy(false);
    }
  }

  function startRenaming(visitMatter: VisitMatterSummary) {
    setEditingVisitMatterId(visitMatter.visit_matter_id);
    setEditingTitle(visitMatter.title);
  }

  async function handleRename(visitMatter: VisitMatterSummary) {
    const title = editingTitle.trim();
    if (!title || visitMatterBusy) return;
    setVisitMatterBusy(true);
    setVisitMatterError(null);
    try {
      const renamed = await renameVisitMatter(
        backendBaseUrl,
        visitMatter.visit_matter_id,
        demoParticipantId,
        title,
      );
      const replaceRenamed = (items: VisitMatterSummary[]) => items.map(
        (item) => item.visit_matter_id === renamed.visit_matter_id ? renamed : item,
      );
      setAvailableVisitMatters(replaceRenamed);
      setArchivedVisitMatters(replaceRenamed);
      setEditingVisitMatterId(null);
    } catch (renameError) {
      setVisitMatterError(
        renameError instanceof Error ? renameError.message : "重命名失败，请稍后重试。",
      );
    } finally {
      setVisitMatterBusy(false);
    }
  }

  async function selectVisitMatterAfterLifecycle(visitMatterId: string) {
    let nextMessages: MediPetMessage[] = [];
    let failed = false;
    try {
      nextMessages = await loadConversationHistory(
        backendBaseUrl,
        visitMatterId,
        demoParticipantId,
      );
    } catch {
      failed = true;
    }
    setInitialMessages(nextMessages);
    if (visitMatterId === selectedVisitMatterId) setMessages(nextMessages);
    setSelectedVisitMatterId(visitMatterId);
    setHistoryFailed(failed);
    setDecisionStates({});
  }

  async function handleRetryVisitMatters() {
    if (visitMatterBusy) return;
    setVisitMatterBusy(true);
    setVisitMatterError(null);
    try {
      const activeItems = await loadVisitMatters(backendBaseUrl, demoParticipantId);
      setAvailableVisitMatters(activeItems);
      setVisitMatterListFailed(false);
      if (activeItems[0]) await selectVisitMatterAfterLifecycle(activeItems[0].visit_matter_id);
      else clearSelectedVisitMatter();
    } catch (loadError) {
      setVisitMatterError(
        loadError instanceof Error ? loadError.message : "历史记录加载失败，请稍后重试。",
      );
    } finally {
      setVisitMatterBusy(false);
    }
  }

  function clearSelectedVisitMatter() {
    setInitialMessages([]);
    setSelectedVisitMatterId(null);
    setHistoryFailed(false);
    setDecisionStates({});
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

        <div className="rail-section-heading">
          <p className="rail-label">{showArchived ? "已归档" : "历史记录"}</p>
          <button
            className="archive-toggle"
            disabled={active || visitMatterBusy}
            onClick={handleShowArchived}
            type="button"
          >
            {showArchived ? "返回活动历史" : "查看已归档"}
          </button>
        </div>
        <nav className="visit-list" aria-label="历史记录列表">
          {displayedVisitMatters.map((visitMatter) => {
            const current = visitMatter.visit_matter_id === selectedVisitMatterId;
            const editing = editingVisitMatterId === visitMatter.visit_matter_id;
            return (
              <div className={`visit-entry ${current ? "active" : ""}`} key={visitMatter.visit_matter_id}>
                {editing ? (
                  <form
                    className="visit-rename"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void handleRename(visitMatter);
                    }}
                  >
                    <input
                      aria-label="新的历史记录名称"
                      autoFocus
                      maxLength={200}
                      onChange={(event) => setEditingTitle(event.target.value)}
                      value={editingTitle}
                    />
                    <button aria-label="保存名称" disabled={!editingTitle.trim()} type="submit">保存</button>
                    <button onClick={() => setEditingVisitMatterId(null)} type="button">取消</button>
                  </form>
                ) : (
                  <>
                    <button
                      aria-current={current ? "page" : undefined}
                      className="visit-item"
                      disabled={active || visitMatterBusy}
                      onClick={() => handleSelectVisitMatter(visitMatter.visit_matter_id)}
                    >
                      <span>{visitMatter.patient_display_name} · {visitMatter.title}</span>
                      <small>{showArchived ? "已归档 · 可查看" : visitStageLabel(visitMatter.visit_stage)}</small>
                    </button>
                    <div className="visit-actions">
                      <button
                        aria-label={`重命名 ${visitMatter.title}`}
                        disabled={active || visitMatterBusy}
                        onClick={() => startRenaming(visitMatter)}
                        title="重命名"
                        type="button"
                      ><Pencil size={13} /></button>
                      <button
                        aria-label={`${showArchived ? "恢复" : "归档"} ${visitMatter.title}`}
                        disabled={active || visitMatterBusy}
                        onClick={() => showArchived
                          ? void handleRestore(visitMatter)
                          : void handleArchive(visitMatter)}
                        title={showArchived ? "恢复" : "归档"}
                        type="button"
                      >{showArchived ? <RotateCcw size={13} /> : <Archive size={13} />}</button>
                    </div>
                  </>
                )}
              </div>
            );
          })}
          {displayedVisitMatters.length === 0 && (
            <p className="rail-empty">{showArchived ? "没有已归档记录" : "还没有历史记录"}</p>
          )}
        </nav>
        <div className="rail-footer">
          <ShieldCheck size={16} aria-hidden="true" />
          <p>一次就诊事项只对应一位患者。确认操作前请核对患者与预约信息。</p>
        </div>
      </aside>

      <section className="chat-panel panel" aria-label="MediPet 对话">
        <header className="chat-header">
          <div className="chat-heading">
            <h2 className="chat-title">{selectedVisitMatter?.title ?? "暂无活动就诊事项"}</h2>
            <p className="chat-subtitle">
              {showArchived
                ? "已归档 · 历史只读"
                : selectedVisitMatter
                  ? `独立就诊上下文 · ${visitStageLabel(selectedVisitMatter.visit_stage)}`
                  : "新建事项后即可开始门诊协助"}
            </p>
          </div>
          <span className="online-badge">开发环境</span>
          <div className="mobile-visit-controls">
            <select
              aria-label="切换就诊事项"
              disabled={active || visitMatterBusy || displayedVisitMatters.length === 0}
              onChange={(event) => handleSelectVisitMatter(event.target.value)}
              value={selectedVisitMatterId ?? ""}
            >
              {displayedVisitMatters.length === 0 && <option value="">暂无记录</option>}
              {displayedVisitMatters.map((visitMatter) => (
                <option key={visitMatter.visit_matter_id} value={visitMatter.visit_matter_id}>
                  {visitMatter.title}
                </option>
              ))}
            </select>
            <button
              aria-label={showArchived ? "移动端返回活动历史" : "移动端查看已归档"}
              disabled={active || visitMatterBusy}
              onClick={handleShowArchived}
              type="button"
            >
              {showArchived ? <RotateCcw size={17} /> : <Archive size={17} />}
            </button>
            {selectedVisitMatter && (
              <>
                <button
                  aria-label={`移动端重命名 ${selectedVisitMatter.title}`}
                  disabled={active || visitMatterBusy}
                  onClick={() => startRenaming(selectedVisitMatter)}
                  type="button"
                ><Pencil size={17} /></button>
                <button
                  aria-label={`移动端${showArchived ? "恢复" : "归档"} ${selectedVisitMatter.title}`}
                  disabled={active || visitMatterBusy}
                  onClick={() => showArchived
                    ? void handleRestore(selectedVisitMatter)
                    : void handleArchive(selectedVisitMatter)}
                  type="button"
                >{showArchived ? <RotateCcw size={17} /> : <Archive size={17} />}</button>
              </>
            )}
            <button
              aria-label="在移动端新建就诊事项"
              disabled={active || visitMatterBusy}
              onClick={handleCreateVisitMatter}
              type="button"
            >
              <CirclePlus size={17} aria-hidden="true" />
            </button>
          </div>
          {selectedVisitMatter && editingVisitMatterId === selectedVisitMatter.visit_matter_id && (
            <form
              className="mobile-rename"
              onSubmit={(event) => {
                event.preventDefault();
                void handleRename(selectedVisitMatter);
              }}
            >
              <input
                aria-label="移动端新的历史记录名称"
                maxLength={200}
                onChange={(event) => setEditingTitle(event.target.value)}
                value={editingTitle}
              />
              <button disabled={!editingTitle.trim()} type="submit">保存</button>
            </form>
          )}
          {visitMatterError && (
            <p className="visit-management-error" role="alert">{visitMatterError}</p>
          )}
        </header>

        <div className="thread" aria-live="polite">
          {historyFailed && (
            <div className="data-card urgent">历史对话暂时无法恢复，请稍后刷新重试。</div>
          )}
          {visitMatterListFailed && !selectedVisitMatter && !showArchived ? (
            <section className="empty-history-state">
              <div className="empty-history-mark"><Archive size={28} aria-hidden="true" /></div>
              <h1>历史记录加载失败</h1>
              <p>暂时无法连接历史记录服务，请稍后重试。</p>
              <button
                disabled={visitMatterBusy}
                onClick={() => void handleRetryVisitMatters()}
                type="button"
              >重新加载</button>
            </section>
          ) : !selectedVisitMatter ? (
            <section className="empty-history-state">
              <div className="empty-history-mark"><Archive size={28} aria-hidden="true" /></div>
              <h1>{showArchived ? "没有已归档记录" : "还没有历史记录"}</h1>
              <p>
                {showArchived
                  ? "归档后的就诊事项会保留在这里，随时可以恢复。"
                  : "新建一个就诊事项，开始独立保存这次门诊协助。"}
              </p>
              {!showArchived && (
                <button onClick={handleCreateVisitMatter} type="button">新建就诊事项</button>
              )}
            </section>
          ) : showArchived && messages.length === 0 ? (
            <section className="empty-history-state">
              <div className="empty-history-mark"><Archive size={28} aria-hidden="true" /></div>
              <h1>已归档历史</h1>
              <p>这条记录没有对话内容。恢复后才能继续咨询或处理待确认操作。</p>
            </section>
          ) : messages.length === 0 ? (
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
                            readOnly={showArchived}
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

        <ChatComposer
          active={active}
          disabled={!selectedVisitMatter || showArchived}
          key={selectedVisitMatterId ?? "empty"}
          onStop={stop}
          onSubmit={submit}
        />
      </section>

      <aside className="right-rail panel" aria-label="就诊上下文">
        <section className="context-card">
          <h2>本次就诊</h2>
          <div className="context-row">
            <span>患者</span><strong>{selectedVisitMatter?.patient_display_name ?? "尚未选择"}</strong>
          </div>
          <div className="context-row">
            <span>参与者</span>
            <strong>{selectedVisitMatter?.participant_display_name ?? "尚未选择"}</strong>
          </div>
          <div className="context-row"><span>授权状态</span><strong>本人操作</strong></div>
        </section>

        <section className="context-card">
          <h2>就诊阶段</h2>
          <div className="stage-track">
            <div className={`stage ${selectedVisitMatter?.visit_stage !== "in_visit" ? "active" : ""}`}>
              诊前准备
            </div>
            <div className={`stage ${selectedVisitMatter?.visit_stage === "in_visit" ? "active" : ""}`}>
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

function ChatComposer({
  active,
  disabled,
  onStop,
  onSubmit,
}: {
  active: boolean;
  disabled: boolean;
  onStop: () => void;
  onSubmit: (text: string) => Promise<void>;
}) {
  const [input, setInput] = useState("");
  const [showPreview, setShowPreview] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  function submitInput() {
    const value = input.trim();
    if (!value || active || disabled) return;
    setInput("");
    void onSubmit(value);
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
    <footer className="composer-wrap">
      <div className="composer-shell">
        <div className="composer-toolbar" aria-label="Markdown 格式工具">
          <span className="markdown-label">Markdown</span>
          <div className="format-actions">
            <button
              aria-label="加粗"
              disabled={active || disabled}
              onClick={() => applyMarkdown("**", "**", "重点内容")}
              title="加粗"
              type="button"
            ><Bold size={15} /></button>
            <button
              aria-label="行内代码"
              disabled={active || disabled}
              onClick={() => applyMarkdown("`", "`", "代码或配置")}
              title="行内代码"
              type="button"
            ><Code2 size={15} /></button>
            <button
              aria-label="无序列表"
              disabled={active || disabled}
              onClick={() => applyMarkdown("- ", "", "列表项")}
              title="无序列表"
              type="button"
            ><List size={15} /></button>
            <button
              aria-label="有序列表"
              disabled={active || disabled}
              onClick={() => applyMarkdown("1. ", "", "列表项")}
              title="有序列表"
              type="button"
            ><ListOrdered size={15} /></button>
          </div>
          <button
            aria-label={showPreview ? "关闭 Markdown 预览" : "打开 Markdown 预览"}
            aria-pressed={showPreview}
            className={`preview-toggle ${showPreview ? "active" : ""}`}
            disabled={disabled}
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
            submitInput();
          }}
        >
          <textarea
            aria-label="输入就诊需求"
            disabled={disabled}
            placeholder="描述主要不适，或用 Markdown 整理时间线与问题清单…"
            ref={composerRef}
            rows={1}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submitInput();
              }
            }}
          />
          <button
            className="send-button"
            type={active ? "button" : "submit"}
            aria-label={active ? "停止生成" : "发送消息"}
            disabled={disabled || (!active && !input.trim())}
            onClick={active ? onStop : undefined}
          >
            {active ? <Square size={16} fill="currentColor" /> : <ArrowUp size={19} />}
          </button>
        </form>
      </div>
      <p className="composer-note">
        {disabled ? "已归档记录仅供查看；恢复或新建事项后可继续对话。" : "MediPet 提供非诊断性就诊协助，不能替代医生判断。"}
      </p>
    </footer>
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
