"use client";

import { Activity, FileClock } from "lucide-react";
import { useMemo, useState } from "react";

import type { CapabilitySnapshot } from "./types";
import { EmptyState, formatAdminDate, PageIntro } from "./ui";

type UnifiedAudit = {
  kind: "skill" | "tool";
  action: string;
  objectId: string;
  version: string;
  actor: string;
  createdAt: string;
  visitMatterId: string | null;
  turnId: string | null;
};

const runtimeActions = new Set(["runtime_select", "invoke", "reject_invoke"]);
const actionLabels: Record<string, string> = {
  create: "创建", edit: "编辑", import: "导入", export: "导出", submit_review: "提交审核",
  publish: "发布", retire: "退休", activate: "激活", sync: "同步", enable: "启用", disable: "停用",
  configure: "配置", configure_approval: "配置审批", bind: "绑定", unbind: "解绑", reject_bind: "拒绝绑定",
  reject_unbind: "拒绝解绑", runtime_select: "Skill 选择", invoke: "Tool 调用", reject_invoke: "拒绝调用",
};

function deidentify(value: string) {
  if (value.length <= 9) return `${value.slice(0, 2)}…${value.slice(-2)}`;
  return `${value.slice(0, 5)}…${value.slice(-4)}`;
}

export function AuditList({ snapshot }: { snapshot: CapabilitySnapshot }) {
  const [includeRuntime, setIncludeRuntime] = useState(false);
  const [kind, setKind] = useState<"all" | "skill" | "tool">("all");
  const [action, setAction] = useState("all");
  const [objectQuery, setObjectQuery] = useState("");

  const allAudits = useMemo<UnifiedAudit[]>(() => [
    ...snapshot.skillAudits.map((item) => ({
      kind: "skill" as const, action: item.action, objectId: item.skill_id ?? "—",
      version: item.version === null ? "—" : String(item.version), actor: item.actor,
      createdAt: item.created_at, visitMatterId: item.visit_matter_id, turnId: item.turn_id,
    })),
    ...snapshot.toolAudits.map((item) => ({
      kind: "tool" as const, action: item.action, objectId: item.tool_id ?? "—",
      version: item.version ?? "—", actor: item.actor, createdAt: item.created_at,
      visitMatterId: item.visit_matter_id, turnId: item.turn_id,
    })),
  ].sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt)), [snapshot]);

  const actions = useMemo(() => Array.from(new Set(allAudits
    .filter((item) => includeRuntime || !runtimeActions.has(item.action))
    .map((item) => item.action))).sort(), [allAudits, includeRuntime]);

  const filtered = allAudits.filter((item) => (
    (includeRuntime || !runtimeActions.has(item.action))
    && (kind === "all" || item.kind === kind)
    && (action === "all" || item.action === action)
    && (!objectQuery.trim() || item.objectId.toLocaleLowerCase().includes(objectQuery.trim().toLocaleLowerCase()))
  ));

  return (
    <>
      <PageIntro eyebrow="Registry / Change log" title="变更记录" description="按时间合并 Skill 与 Tool 治理事实；运行事件默认收起。" />
      <section className="admin-filter-bar admin-audit-filters" aria-label="审计筛选">
        <label><span>对象类型</span><select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)}><option value="all">全部对象</option><option value="skill">Skill</option><option value="tool">Tool</option></select></label>
        <label><span>动作</span><select value={action} onChange={(event) => setAction(event.target.value)}><option value="all">全部动作</option>{actions.map((value) => <option key={value} value={value}>{actionLabels[value] ?? value}</option>)}</select></label>
        <label className="admin-search-field"><span>对象 ID</span><input value={objectQuery} placeholder="slug 或 Tool ID" onChange={(event) => setObjectQuery(event.target.value)} /></label>
        <label className="admin-runtime-toggle"><input type="checkbox" checked={includeRuntime} onChange={(event) => { setIncludeRuntime(event.target.checked); setAction("all"); }} /><span><Activity size={15} /> 包含运行事件</span></label>
      </section>

      {filtered.length ? (
        <section className="admin-audit-ledger" aria-label="治理审计时间线">
          {filtered.map((item, index) => (
            <article className="admin-audit-row" key={`${item.kind}-${item.createdAt}-${index}`}>
              <span className="admin-audit-mark" aria-hidden="true"><FileClock size={16} /></span>
              <time dateTime={item.createdAt}>{formatAdminDate(item.createdAt)}</time>
              <span className={`admin-object-kind ${item.kind}`}>{item.kind === "skill" ? "Skill" : "Tool"}</span>
              <strong>{actionLabels[item.action] ?? item.action}</strong>
              <code>{item.objectId}@{item.kind === "skill" ? "v" : ""}{item.version}</code>
              <span className="admin-audit-actor">{item.actor}</span>
              {item.visitMatterId || item.turnId ? <small>{item.visitMatterId ? deidentify(item.visitMatterId) : null}{item.visitMatterId && item.turnId ? " · " : null}{item.turnId ? deidentify(item.turnId) : null}</small> : <small>治理事件</small>}
            </article>
          ))}
        </section>
      ) : <EmptyState title={allAudits.length ? "没有符合条件的变更" : "尚无治理记录"}>调整筛选条件，或完成一次 Skill / Tool 治理操作。</EmptyState>}
    </>
  );
}
