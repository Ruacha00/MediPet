export type VisitMatterSummary = {
  visit_matter_id: string;
  title: string;
  visit_stage: "pre_visit" | "in_visit";
  patient_display_name: string;
  participant_display_name: string;
};

export async function loadVisitMatters(
  backendBaseUrl: string,
  participantId: string,
): Promise<VisitMatterSummary[]> {
  const query = new URLSearchParams({ participant_id: participantId });
  const response = await fetch(`${backendBaseUrl}/v1/visit-matters?${query}`);
  if (!response.ok) throw new Error("就诊事项列表加载失败");
  const result = await response.json() as { visit_matters?: VisitMatterSummary[] };
  return result.visit_matters ?? [];
}

export async function createVisitMatter(
  backendBaseUrl: string,
  participantId: string,
  title = "新的就诊事项",
): Promise<VisitMatterSummary> {
  const response = await fetch(`${backendBaseUrl}/v1/visit-matters`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ participant_id: participantId, title }),
  });
  if (!response.ok) throw new Error("新建就诊事项失败，请稍后重试。");
  return await response.json() as VisitMatterSummary;
}
