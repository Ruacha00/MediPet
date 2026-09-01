export type VisitMatterSummary = {
  visit_matter_id: string;
  title: string;
  visit_stage: "pre_visit" | "in_visit";
  patient_display_name: string;
  participant_display_name: string;
  archived_at: string | null;
};

export async function loadVisitMatters(
  backendBaseUrl: string,
  participantId: string,
  archived = false,
): Promise<VisitMatterSummary[]> {
  const query = new URLSearchParams({ participant_id: participantId });
  if (archived) query.set("archived", "true");
  const response = await fetch(`${backendBaseUrl}/v1/visit-matters?${query}`);
  if (!response.ok) throw new Error("就诊事项列表加载失败");
  const result = await response.json() as { visit_matters?: VisitMatterSummary[] };
  return result.visit_matters ?? [];
}

export async function createVisitMatter(
  backendBaseUrl: string,
  participantId: string,
): Promise<VisitMatterSummary> {
  const response = await fetch(`${backendBaseUrl}/v1/visit-matters`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ participant_id: participantId }),
  });
  if (!response.ok) throw new Error("新建就诊事项失败，请稍后重试。");
  return await response.json() as VisitMatterSummary;
}

export async function renameVisitMatter(
  backendBaseUrl: string,
  visitMatterId: string,
  participantId: string,
  title: string,
): Promise<VisitMatterSummary> {
  return mutateVisitMatter(
    `${backendBaseUrl}/v1/visit-matters/${visitMatterId}/title`,
    "PATCH",
    { participant_id: participantId, title },
    "历史记录重命名失败，请稍后重试。",
  );
}

export async function archiveVisitMatter(
  backendBaseUrl: string,
  visitMatterId: string,
  participantId: string,
): Promise<VisitMatterSummary> {
  return mutateVisitMatter(
    `${backendBaseUrl}/v1/visit-matters/${visitMatterId}/archive`,
    "POST",
    { participant_id: participantId },
    "归档失败；如果回复仍在生成或操作仍待确认，请先处理完成。",
  );
}

export async function restoreVisitMatter(
  backendBaseUrl: string,
  visitMatterId: string,
  participantId: string,
): Promise<VisitMatterSummary> {
  return mutateVisitMatter(
    `${backendBaseUrl}/v1/visit-matters/${visitMatterId}/restore`,
    "POST",
    { participant_id: participantId },
    "恢复历史记录失败，请稍后重试。",
  );
}

async function mutateVisitMatter(
  url: string,
  method: "PATCH" | "POST",
  body: Record<string, string>,
  fallbackMessage: string,
): Promise<VisitMatterSummary> {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as {
      detail?: { message?: string } | string;
    } | null;
    const message = typeof payload?.detail === "string"
      ? payload.detail
      : payload?.detail?.message;
    throw new Error(message || fallbackMessage);
  }
  return await response.json() as VisitMatterSummary;
}
