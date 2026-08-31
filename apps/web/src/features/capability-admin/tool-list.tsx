"use client";

import { ArrowUpRight, CircleAlert, Link2 } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import type { CapabilitySnapshot, ToolVersion } from "./types";
import { EmptyState, PageIntro } from "./ui";

type BinaryFilter = "all" | "yes" | "no";

function matchesBinary(value: boolean, filter: BinaryFilter) {
  return filter === "all" || (filter === "yes" ? value : !value);
}

export function ToolList({ snapshot }: { snapshot: CapabilitySnapshot }) {
  const [query, setQuery] = useState("");
  const [effect, setEffect] = useState<"all" | ToolVersion["effect"]>("all");
  const [availability, setAvailability] = useState<"all" | "available" | "missing">("all");
  const [enabled, setEnabled] = useState<BinaryFilter>("all");
  const [approval, setApproval] = useState<BinaryFilter>("all");

  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return snapshot.tools.flatMap((tool) => {
      const matchingVersions = tool.versions.filter((version) => {
        const searchable = `${tool.tool_id} ${version.name} ${version.description}`.toLocaleLowerCase();
        return (!normalized || searchable.includes(normalized))
          && (effect === "all" || version.effect === effect)
          && (availability === "all" || version.available === (availability === "available"))
          && matchesBinary(version.enabled, enabled)
          && matchesBinary(version.approval_required, approval);
      });
      const displayedVersion = matchingVersions.at(-1);
      return displayedVersion ? [{ tool, displayedVersion }] : [];
    }).sort((left, right) => {
      const leftNeedsAttention = !left.displayedVersion.available;
      const rightNeedsAttention = !right.displayedVersion.available;
      return Number(rightNeedsAttention) - Number(leftNeedsAttention)
        || left.tool.tool_id.localeCompare(right.tool.tool_id);
    });
  }, [approval, availability, effect, enabled, query, snapshot.tools]);

  function usedByActiveSkill(toolId: string) {
    return snapshot.bindings.some((binding) => binding.tool_id === toolId && snapshot.skills.some((skill) => (
      skill.skill_id === binding.skill_id
      && skill.versions.some((version) => version.version === binding.skill_version && version.active)
    )));
  }
  const filtersActive = Boolean(query.trim()) || effect !== "all" || availability !== "all"
    || enabled !== "all" || approval !== "all";

  return (
    <>
      <PageIntro eyebrow="Registry / Tools" title="Tools" description="查看受信 Provider 契约，治理运行开关与审批策略。" />

      <section className="admin-filter-bar admin-tool-filters" aria-label="Tool 筛选">
        <label className="admin-search-field"><span>搜索 Tools</span><input type="search" value={query} placeholder="Tool ID、名称或描述" onChange={(event) => setQuery(event.target.value)} /></label>
        <label><span>Effect</span><select value={effect} onChange={(event) => setEffect(event.target.value as typeof effect)}><option value="all">全部</option><option value="read">Read</option><option value="write">Write</option></select></label>
        <label><span>可用性</span><select value={availability} onChange={(event) => setAvailability(event.target.value as typeof availability)}><option value="all">全部</option><option value="available">实现可用</option><option value="missing">缺少实现</option></select></label>
        <label><span>启用状态</span><select value={enabled} onChange={(event) => setEnabled(event.target.value as BinaryFilter)}><option value="all">全部</option><option value="yes">已启用</option><option value="no">已停用</option></select></label>
        <label><span>审批要求</span><select value={approval} onChange={(event) => setApproval(event.target.value as BinaryFilter)}><option value="all">全部</option><option value="yes">要求审批</option><option value="no">无需审批</option></select></label>
      </section>

      {filtered.length ? (
        <section className="admin-ledger admin-tool-ledger" aria-label="Tools">
          <div className="admin-ledger-head" aria-hidden="true"><span>Tool</span><span>契约</span><span>运行状态</span><span>阶段</span><span>绑定</span><span /></div>
          {filtered.map(({ tool, displayedVersion }) => {
            const latest = displayedVersion;
            const activeBinding = usedByActiveSkill(tool.tool_id);
            return (
              <Link className="admin-ledger-row" href={`/admin/capabilities/tools/${encodeURIComponent(tool.tool_id)}`} key={tool.tool_id} aria-label={`查看 Tool ${tool.tool_id}`}>
                <span className="admin-ledger-primary"><strong>{latest.name}</strong><code>{tool.tool_id}</code><small>{latest.description}</small></span>
                <span className="admin-tool-contract"><code>v{latest.version}</code><b>{latest.effect}</b><small>{filtersActive ? `筛选命中 · 共 ${tool.versions.length} 个版本` : tool.versions.map((version) => `v${version.version}`).join(" · ")}</small></span>
                <span className="admin-tool-flags">{latest.available ? <em>实现可用</em> : <em className="attention"><CircleAlert size={13} /> 缺少实现</em>}<em>{latest.enabled ? "已启用" : "已停用"}</em><em>{latest.approval_required ? "要求审批" : "无需审批"}</em></span>
                <span>{latest.allowed_stages.join(" / ") || "—"}</span>
                <span>{activeBinding ? <span className="admin-active-binding"><Link2 size={13} /> 被活动 Skill 使用</span> : <span className="admin-muted">无活动绑定</span>}</span>
                <ArrowUpRight aria-hidden="true" size={17} />
              </Link>
            );
          })}
        </section>
      ) : (
        <EmptyState title={snapshot.tools.length ? "没有符合条件的 Tool" : "Tool Registry 目前为空"}>
          {snapshot.tools.length ? "调整搜索或筛选条件后重试。" : "Tool 只能由受信 Provider 在部署时注册，治理台不能创建 Tool。"}
        </EmptyState>
      )}
    </>
  );
}
