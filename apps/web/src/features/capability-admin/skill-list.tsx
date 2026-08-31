"use client";

import { ArrowUpRight, FileArchive, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";

import type { CapabilitySnapshot, SkillStatus, SkillSummary, SkillType } from "./types";
import { EmptyState, formatAdminDate, PageIntro, StatusBadge } from "./ui";

const statusPriority: Record<SkillStatus, number> = {
  quarantined: 0,
  draft: 1,
  in_review: 2,
  published: 3,
  retired: 4,
};

function latestVersion(skill: SkillSummary) {
  return skill.versions.at(-1)!;
}

function activeVersion(skill: SkillSummary) {
  return skill.versions.find((version) => version.active);
}

export function SkillList({ snapshot }: { snapshot: CapabilitySnapshot }) {
  const router = useRouter();
  const fileInput = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<SkillStatus | "all">("all");
  const [skillType, setSkillType] = useState<SkillType | "all">("all");
  const [attention, setAttention] = useState<"all" | "blocked" | "active">("all");
  const [importing, setImporting] = useState(false);
  const [selectedArchive, setSelectedArchive] = useState<File | null>(null);
  const [error, setError] = useState("");

  const filtered = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase();
    return snapshot.skills
      .filter((skill) => {
        const latest = latestVersion(skill);
        const searchable = `${skill.name} ${skill.slug} ${skill.description}`.toLocaleLowerCase();
        if (normalizedQuery && !searchable.includes(normalizedQuery)) return false;
        if (status !== "all" && latest.status !== status) return false;
        if (skillType !== "all" && latest.governance.skill_type !== skillType) return false;
        if (attention === "blocked" && !(
          latest.publish_blockers.length || latest.quarantine_reasons.length
        )) return false;
        if (attention === "active" && !activeVersion(skill)) return false;
        return true;
      })
      .sort((left, right) => {
        const leftVersion = latestVersion(left);
        const rightVersion = latestVersion(right);
        return statusPriority[leftVersion.status] - statusPriority[rightVersion.status]
          || Date.parse(rightVersion.created_at) - Date.parse(leftVersion.created_at);
      });
  }, [attention, query, skillType, snapshot.skills, status]);

  async function importArchive(file: File) {
    setImporting(true);
    setError("");
    try {
      const response = await fetch("/api/admin/capabilities/import", {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: file,
      });
      const result = await response.json() as { skill_id?: string; detail?: string };
      if (!response.ok || !result.skill_id) throw new Error(result.detail ?? "Skill 导入失败");
      setSelectedArchive(null);
      if (fileInput.current) fileInput.current.value = "";
      router.push(`/admin/capabilities/skills/${result.skill_id}`);
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Skill 导入失败");
    } finally {
      setImporting(false);
    }
  }

  return (
    <>
      <PageIntro
        eyebrow="Registry / Skills"
        title="Skills"
        description="管理版本化指令、资源与发布生命周期。"
        actions={(
          <>
            <input
              ref={fileInput}
              className="admin-visually-hidden"
              type="file"
              accept=".zip,application/zip"
              aria-label="选择 Skill ZIP"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) setSelectedArchive(file);
              }}
            />
            <button
              className="admin-button admin-button-secondary"
              type="button"
              disabled={importing}
              onClick={() => fileInput.current?.click()}
            >
              <FileArchive size={16} /> 导入 ZIP
            </button>
            <Link className="admin-button admin-button-primary" href="/admin/capabilities/skills/new">
              <Plus size={16} /> 新建 Skill
            </Link>
          </>
        )}
      />

      {selectedArchive ? (
        <section className="admin-import-review" aria-label="确认 Skill ZIP 导入">
          <FileArchive size={20} aria-hidden="true" />
          <div><strong>{selectedArchive.name}</strong><small>{(selectedArchive.size / 1024).toFixed(1)} KB · 浏览器不会解包或执行内容</small></div>
          <button className="admin-button admin-button-quiet" type="button" disabled={importing} onClick={() => { setSelectedArchive(null); if (fileInput.current) fileInput.current.value = ""; }}>取消</button>
          <button className="admin-button admin-button-primary" type="button" disabled={importing} onClick={() => void importArchive(selectedArchive)}>{importing ? "正在导入" : "确认导入"}</button>
        </section>
      ) : null}

      <section className="admin-filter-bar" aria-label="Skill 筛选">
        <label className="admin-search-field">
          <span>搜索 Skills</span>
          <input
            type="search"
            value={query}
            placeholder="名称、slug 或描述"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label>
          <span>生命周期状态</span>
          <select value={status} onChange={(event) => setStatus(event.target.value as typeof status)}>
            <option value="all">全部状态</option>
            <option value="quarantined">已隔离</option>
            <option value="draft">草稿</option>
            <option value="in_review">审核中</option>
            <option value="published">已发布</option>
            <option value="retired">已退休</option>
          </select>
        </label>
        <label>
          <span>Skill 类型</span>
          <select
            value={skillType}
            onChange={(event) => setSkillType(event.target.value as typeof skillType)}
          >
            <option value="all">全部类型</option>
            <option value="instruction-only">Instruction only</option>
            <option value="tool-assisted">Tool assisted</option>
          </select>
        </label>
        <label>
          <span>关注项</span>
          <select value={attention} onChange={(event) => setAttention(event.target.value as typeof attention)}>
            <option value="all">全部</option>
            <option value="blocked">存在阻断</option>
            <option value="active">当前活动</option>
          </select>
        </label>
      </section>

      {error ? <p className="admin-error" role="alert">{error}</p> : null}

      {filtered.length ? (
        <section className="admin-ledger" aria-label="Skills">
          <div className="admin-ledger-head" aria-hidden="true">
            <span>Skill</span><span>类型</span><span>最新版本</span><span>活动版本</span><span>更新时间</span><span />
          </div>
          {filtered.map((skill) => {
            const latest = latestVersion(skill);
            const active = activeVersion(skill);
            const blockers = latest.publish_blockers.length + latest.quarantine_reasons.length;
            return (
              <Link
                className="admin-ledger-row"
                href={`/admin/capabilities/skills/${skill.skill_id}`}
                key={skill.skill_id}
                aria-label={`查看 Skill ${skill.name}`}
              >
                <span className="admin-ledger-primary">
                  <strong>{skill.name}</strong>
                  <code>{skill.slug}</code>
                  <small>{skill.description}</small>
                </span>
                <span>{latest.governance.skill_type === "tool-assisted" ? "Tool assisted" : "Instruction only"}</span>
                <span className="admin-ledger-version">
                  <code>v{latest.version}</code>
                  <StatusBadge status={latest.status} active={latest.active} />
                  {blockers ? <small className="admin-blocker-count">{blockers} 项阻断</small> : null}
                </span>
                <span>{active ? <code>v{active.version}</code> : <span className="admin-muted">无</span>}</span>
                <time dateTime={latest.created_at}>{formatAdminDate(latest.created_at)}</time>
                <ArrowUpRight aria-hidden="true" size={17} />
              </Link>
            );
          })}
        </section>
      ) : (
        <EmptyState title={snapshot.skills.length ? "没有符合条件的 Skill" : "Registry 目前为空"}>
          {snapshot.skills.length
            ? "调整搜索或筛选条件后重试。"
            : "创建一个 Skill，或导入 Agent Skills-compatible ZIP 包。"}
        </EmptyState>
      )}
    </>
  );
}
