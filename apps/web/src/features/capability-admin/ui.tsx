import type { ReactNode } from "react";

import type { SkillStatus } from "./types";

const statusLabels: Record<SkillStatus, string> = {
  draft: "草稿",
  in_review: "审核中",
  published: "已发布",
  retired: "已退休",
  quarantined: "已隔离",
};

export function StatusBadge({ status, active = false }: { status: SkillStatus; active?: boolean }) {
  return (
    <span className={`admin-status admin-status-${status}`}>
      {statusLabels[status]}{active ? " · 当前活动" : ""}
    </span>
  );
}

export function PageIntro({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="admin-page-intro">
      <div>
        <p className="admin-eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions ? <div className="admin-page-actions">{actions}</div> : null}
    </header>
  );
}

export function EmptyState({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="admin-empty">
      <span aria-hidden="true">◇</span>
      <h2>{title}</h2>
      <p>{children}</p>
    </section>
  );
}

export function formatAdminDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
