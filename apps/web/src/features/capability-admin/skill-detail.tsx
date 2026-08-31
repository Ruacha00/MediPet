"use client";

import { ArrowLeft, Download, GitCompareArrows, Link2, PencilLine, ShieldCheck, Unlink } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type {
  AdminCommand,
  CapabilitySnapshot,
  SkillResource,
  SkillSummary,
  SkillVersion,
  ToolBinding,
} from "./types";
import { sendAdminCommand } from "./client";
import { ConfirmationDialog, type AdminConfirmation } from "./confirmation-dialog";
import { formatAdminDate, PageIntro, StatusBadge } from "./ui";

type DetailTab = "overview" | "instructions" | "resources" | "bindings" | "compare";
type Confirmation = (AdminConfirmation & {
  command: AdminCommand;
}) | null;

function versionBindings(snapshot: CapabilitySnapshot, version: SkillVersion) {
  return snapshot.bindings.filter((binding) => (
    binding.skill_id === version.skill_id && binding.skill_version === version.version
  ));
}

export function SkillDetail({
  skill,
  snapshot,
}: {
  skill: SkillSummary;
  snapshot: CapabilitySnapshot;
}) {
  const router = useRouter();
  const latest = skill.versions.at(-1)!;
  const [selectedNumber, setSelectedNumber] = useState(latest.version);
  const [tab, setTab] = useState<DetailTab>("overview");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmation, setConfirmation] = useState<Confirmation>(null);
  const [editing, setEditing] = useState(false);
  const [instructions, setInstructions] = useState(latest.instructions);
  const [changeNote, setChangeNote] = useState("");
  const [bindingChoice, setBindingChoice] = useState("");
  const [resourcePreview, setResourcePreview] = useState<{ path: string; content: string } | null>(null);

  const selected = skill.versions.find((version) => version.version === selectedNumber) ?? latest;
  const selectedIndex = skill.versions.findIndex((version) => version.version === selected.version);
  const previous = selectedIndex > 0 ? skill.versions[selectedIndex - 1] : null;
  const bindings = versionBindings(snapshot, selected);
  async function mutate(command: AdminCommand, success: string) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await sendAdminCommand(command, "治理操作失败");
      setNotice(success);
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "治理操作失败");
    } finally {
      setBusy(false);
      setConfirmation(null);
    }
  }

  function transition(action: "submit-review" | "publish" | "retire" | "activate") {
    const command: AdminCommand = {
      operation: "transition-skill",
      skillId: skill.skill_id,
      version: selected.version,
      action,
    };
    if (action === "submit-review") {
      void mutate(command, `v${selected.version} 已提交审核`);
      return;
    }
    const descriptions = {
      publish: "发布后将成为当前活动版本，原活动版本会退休。",
      retire: "退休后该版本不再向新的对话提供能力。",
      activate: "激活后将成为当前活动版本，现有活动版本会退休。",
    };
    const labels = { publish: "发布", retire: "退休", activate: "激活" };
    const targetStates = {
      publish: "已发布并成为当前活动版本",
      retire: "已退休且不再活动",
      activate: "已发布并成为当前活动版本",
    };
    setConfirmation({
      title: `确认${labels[action]} Skill`,
      message: `${skill.name} · v${selected.version}。当前状态：${selected.status}；目标状态：${targetStates[action]}。${descriptions[action]}`,
      confirmLabel: `确认${labels[action]}`,
      command,
    });
  }

  async function saveDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await mutate({
      operation: "edit-skill",
      skillId: skill.skill_id,
      payload: { instructions, change_note: changeNote },
    }, "新草稿已创建");
    setEditing(false);
    setChangeNote("");
  }

  async function bindTool() {
    const [toolId, toolVersion] = bindingChoice.split("\u0000");
    if (!toolId || !toolVersion) return;
    await mutate({
      operation: "bind-tool",
      skillId: skill.skill_id,
      skillVersion: selected.version,
      toolId,
      toolVersion,
    }, "Tool 已绑定");
    setBindingChoice("");
  }

  async function unbindTool(binding: ToolBinding) {
    await mutate({
      operation: "unbind-tool",
      skillId: skill.skill_id,
      skillVersion: selected.version,
      toolId: binding.tool_id,
      toolVersion: binding.tool_version,
    }, "Tool 绑定已移除");
  }

  async function previewResource(resource: SkillResource) {
    setError("");
    const query = new URLSearchParams({
      skillId: skill.skill_id,
      version: String(selected.version),
      path: resource.path,
    });
    try {
      const response = await fetch(`/api/admin/capabilities/resource?${query}`);
      const result = await response.json() as { path?: string; content?: string; detail?: string };
      if (!response.ok || typeof result.content !== "string") {
        throw new Error(result.detail ?? "Skill 资源预览失败");
      }
      setResourcePreview({ path: result.path ?? resource.path, content: result.content });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Skill 资源预览失败");
    }
  }

  return (
    <>
      <PageIntro
        eyebrow={`Registry / Skills / ${skill.slug}`}
        title={skill.name}
        description={skill.description}
        actions={(
          <>
            <Link className="admin-text-link" href="/admin/capabilities/skills"><ArrowLeft size={15} /> 返回 Skills</Link>
            <a
              className="admin-button admin-button-secondary"
              href={`/api/admin/capabilities/export?skillId=${encodeURIComponent(skill.skill_id)}&version=${selected.version}`}
            ><Download size={16} /> 导出 v{selected.version}</a>
          </>
        )}
      />

      <div className="admin-detail-layout">
        <aside className="admin-version-spine" aria-label="Skill 版本">
          <p>Version ledger</p>
          {skill.versions.toReversed().map((version) => (
            <button
              type="button"
              className={version.version === selected.version ? "active" : ""}
              key={version.version}
              onClick={() => {
                setSelectedNumber(version.version);
                setTab("overview");
                setResourcePreview(null);
              }}
            >
              <span className="admin-version-node" aria-hidden="true" />
              <strong>v{version.version}</strong>
              <StatusBadge status={version.status} active={version.active} />
              <time dateTime={version.created_at}>{formatAdminDate(version.created_at)}</time>
            </button>
          ))}
        </aside>

        <main className="admin-detail-main">
          <section className="admin-object-header">
            <div>
              <div className="admin-object-title"><code>{skill.slug}</code><StatusBadge status={selected.status} active={selected.active} /></div>
              <p>已选择版本 <strong>v{selected.version}</strong> · {selected.change_note}</p>
            </div>
            <div className="admin-object-actions">
              {selected.status === "draft" ? <button className="admin-button admin-button-secondary" type="button" disabled={busy} onClick={() => transition("submit-review")}><ShieldCheck size={16} /> 提交审核</button> : null}
              {selected.status === "in_review" ? <button className="admin-button admin-button-primary" type="button" disabled={busy} onClick={() => transition("publish")}>发布 v{selected.version}</button> : null}
              {selected.status === "published" && selected.active ? <button className="admin-button admin-button-danger" type="button" disabled={busy} onClick={() => transition("retire")}>退休 v{selected.version}</button> : null}
              {selected.status === "retired" ? <button className="admin-button admin-button-primary" type="button" disabled={busy} onClick={() => transition("activate")}>激活 v{selected.version}</button> : null}
              {selected.version === latest.version && selected.status !== "quarantined" ? <button className="admin-button admin-button-quiet" type="button" onClick={() => setEditing((value) => !value)}><PencilLine size={15} /> 创建新草稿</button> : null}
            </div>
          </section>

          {selected.quarantine_reasons.length ? <section className="admin-blocker-panel"><strong>该版本已隔离</strong>{selected.quarantine_reasons.map((reason) => <p key={reason}>{reason}</p>)}</section> : null}
          {selected.publish_blockers.length ? <section className="admin-blocker-panel"><strong>发布阻断项</strong>{selected.publish_blockers.map((reason) => <p key={reason}>{reason}</p>)}</section> : null}
          {notice ? <p className="admin-notice" role="status">{notice}</p> : null}
          {error ? <p className="admin-error" role="alert">{error}</p> : null}

          {editing ? (
            <form className="admin-inline-editor" onSubmit={saveDraft}>
              <h2>基于最新版本创建草稿</h2>
              <p>现有 API 只允许修改指令和变更说明；名称与描述保持不变。</p>
              <label><span>Instructions Markdown</span><textarea rows={14} required value={instructions} onChange={(event) => setInstructions(event.target.value)} /></label>
              <label><span>变更说明</span><input required value={changeNote} onChange={(event) => setChangeNote(event.target.value)} /></label>
              <div><button className="admin-button admin-button-quiet" type="button" onClick={() => setEditing(false)}>取消</button><button className="admin-button admin-button-primary" type="submit" disabled={busy}>保存新草稿</button></div>
            </form>
          ) : null}

          <nav className="admin-detail-tabs" aria-label="Skill 详情">
            {([
              ["overview", "概览"],
              ["instructions", "指令"],
              ["resources", `资源 ${selected.resources.length}`],
              ["bindings", `Tool 绑定 ${bindings.length}`],
              ["compare", "比较"],
            ] as Array<[DetailTab, string]>).map(([value, label]) => (
              <button key={value} type="button" aria-current={tab === value ? "page" : undefined} onClick={() => setTab(value)}>{label}</button>
            ))}
          </nav>

          {tab === "overview" ? <Overview version={selected} /> : null}
          {tab === "instructions" ? <Instructions version={selected} /> : null}
          {tab === "resources" ? (
            <Resources
              version={selected}
              preview={resourcePreview}
              onPreview={(resource) => void previewResource(resource)}
            />
          ) : null}
          {tab === "bindings" ? (
            <Bindings
              bindings={bindings}
              snapshot={snapshot}
              version={selected}
              bindingChoice={bindingChoice}
              busy={busy}
              onChoice={setBindingChoice}
              onBind={() => void bindTool()}
              onUnbind={(binding) => void unbindTool(binding)}
            />
          ) : null}
          {tab === "compare" ? <Comparison current={selected} previous={previous} snapshot={snapshot} /> : null}
        </main>
      </div>

      {confirmation ? <ConfirmationDialog confirmation={confirmation} busy={busy} onCancel={() => setConfirmation(null)} onConfirm={() => void mutate(confirmation.command, "治理状态已更新")} /> : null}
    </>
  );
}

function Overview({ version }: { version: SkillVersion }) {
  return (
    <section className="admin-detail-section" aria-label="Skill 概览">
      <dl className="admin-definition-grid">
        <div><dt>版本</dt><dd><code>v{version.version}</code></dd></div>
        <div><dt>类型</dt><dd>{version.governance.skill_type ?? "instruction-only"}</dd></div>
        <div><dt>风险等级</dt><dd>{String(version.governance.risk_level ?? "standard")}</dd></div>
        <div><dt>审批数</dt><dd>{String(version.governance.required_approvals ?? 0)}</dd></div>
        <div><dt>创建时间</dt><dd>{formatAdminDate(version.created_at)}</dd></div>
        <div><dt>活动状态</dt><dd>{version.active ? "当前活动" : "非活动"}</dd></div>
      </dl>
      <article className="admin-change-note"><span>Change note</span><p>{version.change_note}</p></article>
    </section>
  );
}

function Instructions({ version }: { version: SkillVersion }) {
  return (
    <section className="admin-instruction-grid" aria-label="Skill 指令">
      <div><p className="admin-preview-label">Markdown source</p><pre>{version.instructions}</pre></div>
      <article className="admin-markdown-preview"><p className="admin-preview-label">Rendered</p><ReactMarkdown remarkPlugins={[remarkGfm]}>{version.instructions}</ReactMarkdown></article>
    </section>
  );
}

function Resources({
  version,
  preview,
  onPreview,
}: {
  version: SkillVersion;
  preview: { path: string; content: string } | null;
  onPreview: (resource: SkillResource) => void;
}) {
  return (
    <section className="admin-detail-section" aria-label="Skill 资源">
      {version.resources.length ? version.resources.map((resource) => (
        <div className="admin-resource-row" key={resource.path}>
          <div><code>{resource.path}</code><small>{resource.media_type} · {resource.size} bytes</small></div>
          <button className="admin-button admin-button-quiet" type="button" disabled={version.status === "quarantined"} onClick={() => onPreview(resource)}>预览</button>
        </div>
      )) : <p className="admin-muted">该版本没有附加资源。</p>}
      {preview ? <article className="admin-resource-preview"><p className="admin-preview-label">{preview.path}</p><pre>{preview.content}</pre></article> : null}
    </section>
  );
}

function Bindings({
  bindings,
  snapshot,
  version,
  bindingChoice,
  busy,
  onChoice,
  onBind,
  onUnbind,
}: {
  bindings: ToolBinding[];
  snapshot: CapabilitySnapshot;
  version: SkillVersion;
  bindingChoice: string;
  busy: boolean;
  onChoice: (value: string) => void;
  onBind: () => void;
  onUnbind: (binding: ToolBinding) => void;
}) {
  const type = version.governance.skill_type ?? "instruction-only";
  const selectedTool = bindingChoice
    ? snapshot.tools.flatMap((tool) => tool.versions).find((tool) => (
      `${tool.tool_id}\u0000${tool.version}` === bindingChoice
    ))
    : null;
  return (
    <section className="admin-detail-section" aria-label="Tool 绑定">
      {bindings.map((binding) => {
        const tool = snapshot.tools.find((item) => item.tool_id === binding.tool_id)?.versions
          .find((item) => item.version === binding.tool_version);
        return (
          <div className="admin-binding-row" key={`${binding.tool_id}@${binding.tool_version}`}>
            <Link2 aria-hidden="true" size={17} />
            <div><strong>{tool?.name ?? binding.tool_id}</strong><code>{binding.tool_id}@{binding.tool_version}</code><small>{tool?.effect ?? "unknown"} · {tool?.available ? "已部署" : "缺少实现"} · {tool?.enabled ? "已启用" : "已停用"}</small></div>
            {version.status === "draft" ? <button className="admin-button admin-button-quiet" type="button" disabled={busy} aria-label={`解绑 Tool ${binding.tool_id}`} onClick={() => onUnbind(binding)}><Unlink size={15} /> 解绑</button> : <span className="admin-lock-label">已冻结</span>}
          </div>
        );
      })}
      {!bindings.length ? <p className="admin-muted">该版本尚未绑定 Tool。</p> : null}
      {type === "instruction-only" ? <p className="admin-boundary-note">Instruction-only Skill 不需要 Tool 绑定。</p> : null}
      {type === "tool-assisted" && version.status === "draft" ? (
        <div className="admin-binding-editor">
          <label><span>选择已部署 Tool 版本</span><select value={bindingChoice} onChange={(event) => onChoice(event.target.value)}><option value="">请选择</option>{snapshot.tools.flatMap((tool) => tool.versions.filter((item) => item.available).map((item) => <option key={`${item.tool_id}@${item.version}`} value={`${item.tool_id}\u0000${item.version}`}>{item.name} · {item.tool_id}@{item.version} · {item.enabled ? "已启用" : "已停用"}</option>))}</select></label>
          <button className="admin-button admin-button-primary" type="button" disabled={!bindingChoice || busy} onClick={onBind}><Link2 size={15} /> 绑定 Tool</button>
          {selectedTool ? <div className="admin-binding-candidate"><strong>{selectedTool.effect === "write" ? "Write" : "Read"}</strong><span>阶段：{selectedTool.allowed_stages.join(" / ") || "未限定"}</span><span>{selectedTool.approval_required ? "要求审批" : "无需审批"}</span><span>{selectedTool.enabled ? "已启用" : "已停用（发布前需启用）"}</span></div> : null}
        </div>
      ) : null}
    </section>
  );
}

function Comparison({
  current,
  previous,
  snapshot,
}: {
  current: SkillVersion;
  previous: SkillVersion | null;
  snapshot: CapabilitySnapshot;
}) {
  return (
    <section className="admin-comparison" aria-label="版本比较">
      <header><GitCompareArrows aria-hidden="true" /><div><h2>版本比较</h2><p>{previous ? `v${previous.version} → v${current.version}` : "首个版本没有上一版本"}</p></div></header>
      {previous ? (
        <div className="admin-comparison-grid">
          <CompareColumn label={`上一版本 · v${previous.version}`} version={previous} bindings={versionBindings(snapshot, previous)} />
          <CompareColumn label={`当前版本 · v${current.version}`} version={current} bindings={versionBindings(snapshot, current)} />
        </div>
      ) : null}
    </section>
  );
}

function CompareColumn({
  label,
  version,
  bindings,
}: {
  label: string;
  version: SkillVersion;
  bindings: ToolBinding[];
}) {
  return (
    <article>
      <p className="admin-preview-label">{label}</p>
      <h3>{version.name}</h3>
      <p>{version.description}</p>
      <pre>{version.instructions}</pre>
      <dl><dt>类型</dt><dd>{version.governance.skill_type ?? "instruction-only"}</dd><dt>资源</dt><dd>{version.resources.map((resource) => `${resource.path} (${resource.media_type}, ${resource.size} bytes)`).join("、") || "无"}</dd><dt>Tool 绑定</dt><dd>{bindings.map((binding) => `${binding.tool_id}@${binding.tool_version}`).join("、") || "无"}</dd><dt>变更说明</dt><dd>{version.change_note}</dd></dl>
    </article>
  );
}
