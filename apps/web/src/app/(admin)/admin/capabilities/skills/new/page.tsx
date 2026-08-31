import { SkillForm } from "@/features/capability-admin/skill-form";
import { AdminLoadError } from "@/features/capability-admin/admin-load-error";
import { loadCapabilitySnapshot } from "@/features/capability-admin/server";

export default async function NewSkillPage() {
  try {
    await loadCapabilitySnapshot();
  } catch (error) {
    return <AdminLoadError error={error} />;
  }
  return <SkillForm />;
}
