import { notFound } from "next/navigation";

import { AdminLoadError } from "@/features/capability-admin/admin-load-error";
import { loadCapabilitySnapshot } from "@/features/capability-admin/server";
import { SkillDetail } from "@/features/capability-admin/skill-detail";
import type { CapabilitySnapshot } from "@/features/capability-admin/types";

export default async function SkillDetailPage({ params }: { params: Promise<{ skillId: string }> }) {
  const { skillId } = await params;
  let snapshot: CapabilitySnapshot;
  try {
    snapshot = await loadCapabilitySnapshot();
  } catch (error) {
    return <AdminLoadError error={error} />;
  }
  const skill = snapshot.skills.find((item) => item.skill_id === skillId);
  if (!skill) notFound();
  return <SkillDetail skill={skill} snapshot={snapshot} />;
}
