import { AdminLoadError } from "@/features/capability-admin/admin-load-error";
import { loadCapabilitySnapshot } from "@/features/capability-admin/server";
import { SkillList } from "@/features/capability-admin/skill-list";
import type { CapabilitySnapshot } from "@/features/capability-admin/types";

export default async function SkillsPage() {
  let snapshot: CapabilitySnapshot;
  try {
    snapshot = await loadCapabilitySnapshot();
  } catch (error) {
    return <AdminLoadError error={error} />;
  }
  return <SkillList snapshot={snapshot} />;
}
