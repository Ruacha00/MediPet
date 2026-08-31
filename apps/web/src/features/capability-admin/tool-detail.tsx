"use client";

import { ArrowLeft, Braces, Link2, Power, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import type { AdminCommand, CapabilitySnapshot, ToolSummary, ToolVersion } from "./types";
import { sendAdminCommand } from "./client";
import { ConfirmationDialog, type AdminConfirmation } from "./confirmation-dialog";
import { PageIntro } from "./ui";

type Confirmation = (AdminConfirmation & {
  command: AdminCommand;
}) | null;

export function ToolDetail({
  tool,
  snapshot,
}: {
  tool: ToolSummary;
  snapshot: CapabilitySnapshot;
}) {
  const router = useRouter();
  const latest = tool.versions.at(-1)!;
  const [selectedVersion, setSelectedVersion] = useState(latest.version);
  const [confirmation, setConfirmation] = useState<Confirmation>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const selected = tool.versions.find((version) => version.version === selectedVersion) ?? latest;

  const boundSkills = useMemo(() => snapshot.bindings
    .filter((binding) => binding.tool_id === tool.tool_id && binding.tool_version === selected.version)
    .map((binding) => {
      const skill = snapshot.skills.find((item) => item.skill_id === binding.skill_id);
      const version = skill?.versions.find((item) => item.version === binding.skill_version);
      return { binding, skill, version };
    }), [selected.version, snapshot, tool.tool_id]);

  async function mutate(command: AdminCommand) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await sendAdminCommand(command, "Tool 治理操作失败");
      setNotice("Tool 治理状态已更新");
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Tool 治理操作失败");
    } finally {
      setBusy(false);
      setConfirmation(null);
    }
  }

  function confirmEnabled(enabled: boolean) {
    const action = enabled ? "启用" : "停用";
    const activeBindings = boundSkills.filter(({ version }) => version?.active);
    const affected = activeBindings.length
      ? `受影响的当前活动 Skill：${activeBindings.map(({ skill, binding }) => `${skill?.name ?? binding.skill_id} v${binding.skill_version}`).join("、")}。`
      : "当前没有活动 Skill 绑定此版本。";
    setConfirmation({
      title: `确认${action} Tool`,
      message: `${tool.tool_id}@${selected.version}。当前状态：${selected.enabled ? "已启用" : "已停用"}；目标状态：${enabled ? "已启用" : "已停用"}。${affected}`,
      confirmLabel: `确认${action}`,
      command: {
        operation: "set-tool-enabled",
        toolId: tool.tool_id,
        toolVersion: selected.version,
        enabled,
      },
    });
  }

  function confirmApproval(approvalRequired: boolean) {
    const activeBindings = boundSkills.filter(({ version }) => version?.active);
    const affected = activeBindings.length
      ? `受影响的当前活动 Skill：${activeBindings.map(({ skill, binding }) => `${skill?.name ?? binding.skill_id} v${binding.skill_version}`).join("、")}。`
      : "当前没有活动 Skill 绑定此版本。";
    setConfirmation({
      title: "确认修改审批策略",
      message: `${tool.tool_id}@${selected.version}。当前状态：${selected.approval_required ? "要求审批" : "无需审批"}；目标状态：${approvalRequired ? "要求审批" : "无需审批"}。${affected}`,
      confirmLabel: "确认修改",
      command: {
        operation: "set-tool-approval",
        toolId: tool.tool_id,
        toolVersion: selected.version,
        approvalRequired,
      },
    });
  }

  return (
    <>
      <PageIntro
        eyebrow={`Registry / Tools / ${tool.tool_id}`}
        title={selected.name}
        description={selected.description}
        actions={<Link className="admin-text-link" href="/admin/capabilities/tools"><ArrowLeft size={15} /> 返回 Tools</Link>}
      />

      <div className="admin-detail-layout admin-tool-detail-layout">
        <aside className="admin-version-spine" aria-label="Tool 版本">
          <p>Contract versions</p>
          {tool.versions.toReversed().map((version) => (
            <button
              type="button"
              className={version.version === selected.version ? "active" : ""}
              key={version.version}
              onClick={() => setSelectedVersion(version.version)}
            >
              <span className="admin-version-node" aria-hidden="true" />
              <strong>v{version.version}</strong>
              <span className={`admin-tool-state ${version.enabled ? "enabled" : "disabled"}`}>{version.enabled ? "已启用" : "已停用"}</span>
              <small>{version.available ? "实现可用" : "缺少实现"}</small>
            </button>
          ))}
        </aside>

        <main className="admin-detail-main">
          <section className="admin-object-header">
            <div>
              <div className="admin-object-title"><code>{tool.tool_id}@{selected.version}</code></div>
              <p>{selected.effect === "write" ? "写操作" : "只读操作"} · {selected.allowed_stages.join(" / ") || "未限定阶段"}</p>
            </div>
            <div className="admin-object-actions">
              <button
                className={selected.enabled ? "admin-button admin-button-danger" : "admin-button admin-button-primary"}
                type="button"
                disabled={busy || (!selected.available && !selected.enabled)}
                onClick={() => confirmEnabled(!selected.enabled)}
              ><Power size={15} /> {selected.enabled ? "停用" : "启用"} v{selected.version}</button>
            </div>
          </section>

          {!selected.available ? <p className="admin-blocker-panel"><strong>缺少运行实现</strong>在 Provider 注册该 Tool 之前不能启用。</p> : null}
          {notice ? <p className="admin-notice" role="status">{notice}</p> : null}
          {error ? <p className="admin-error" role="alert">{error}</p> : null}

          <section className="admin-detail-section" aria-label="Tool 治理设置">
            <div className="admin-section-heading"><ShieldCheck size={19} /><div><h2>运行治理</h2><p>契约由受信 Provider 提供，只允许调整运行开关和可变审批策略。</p></div></div>
            <dl className="admin-definition-grid">
              <div><dt>Effect</dt><dd>{selected.effect}</dd></div>
              <div><dt>可用性</dt><dd>{selected.available ? "已部署" : "缺少实现"}</dd></div>
              <div><dt>运行状态</dt><dd>{selected.enabled ? "已启用" : "已停用"}</dd></div>
              <div><dt>Provider 强制审批</dt><dd>{selected.provider_approval_required ? "是" : "否"}</dd></div>
            </dl>
            <label className="admin-switch-row">
              <span><strong>要求审批</strong><small>{selected.effect === "write" || selected.provider_approval_required ? "写操作或 Provider 强制策略不可关闭" : "调用前要求用户明确确认"}</small></span>
              <input
                type="checkbox"
                aria-label="要求审批"
                checked={selected.approval_required}
                disabled={busy || selected.effect === "write" || selected.provider_approval_required}
                onChange={(event) => confirmApproval(event.target.checked)}
              />
            </label>
          </section>

          <BoundSkills boundSkills={boundSkills} />
          <Contract version={selected} />
        </main>
      </div>

      {confirmation ? <ConfirmationDialog confirmation={confirmation} busy={busy} onCancel={() => setConfirmation(null)} onConfirm={() => void mutate(confirmation.command)} /> : null}
    </>
  );
}

function BoundSkills({
  boundSkills,
}: {
  boundSkills: Array<{
    binding: CapabilitySnapshot["bindings"][number];
    skill: CapabilitySnapshot["skills"][number] | undefined;
    version: CapabilitySnapshot["skills"][number]["versions"][number] | undefined;
  }>;
}) {
  return (
    <section className="admin-detail-section" aria-label="绑定此 Tool 的 Skill">
      <div className="admin-section-heading"><Link2 size={19} /><div><h2>绑定关系</h2><p>启停会影响以下 Skill 版本的实际可用性。</p></div></div>
      {boundSkills.length ? boundSkills.map(({ binding, skill, version }) => (
        <Link className="admin-binding-row" key={`${binding.skill_id}@${binding.skill_version}`} href={`/admin/capabilities/skills/${encodeURIComponent(binding.skill_id)}`}>
          <Link2 size={16} aria-hidden="true" />
          <div><strong>{skill?.name ?? binding.skill_id}</strong><code>{skill?.slug ?? binding.skill_id}@v{binding.skill_version}</code><small>{version?.status ?? "unknown"}{version?.active ? " · 当前活动" : ""}</small></div>
        </Link>
      )) : <p className="admin-muted">当前没有 Skill 绑定此 Tool 版本。</p>}
    </section>
  );
}

function Contract({ version }: { version: ToolVersion }) {
  return (
    <section className="admin-detail-section" aria-label="受信 Tool 契约">
      <div className="admin-section-heading"><Braces size={19} /><div><h2>受信契约</h2><p>只读展示。契约变更必须来自 Provider 注册流程。</p></div></div>
      <div className="admin-contract-grid">
        <Schema title="Input schema" value={version.input_schema} />
        <Schema title="Output schema" value={version.output_schema} />
        <Schema title="Confirmation schema" value={version.confirmation_schema ?? {}} />
      </div>
    </section>
  );
}

function Schema({ title, value }: { title: string; value: Record<string, unknown> }) {
  const formatted = JSON.stringify(value, null, 2);
  return (
    <details open>
      <summary><span className="admin-preview-label">{title}</span></summary>
      <button className="admin-schema-copy" type="button" onClick={() => { if (navigator.clipboard) void navigator.clipboard.writeText(formatted); }}>复制 JSON</button>
      <pre>{formatted}</pre>
    </details>
  );
}
