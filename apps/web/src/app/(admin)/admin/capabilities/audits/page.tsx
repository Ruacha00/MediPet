import { AdminLoadError } from "@/features/capability-admin/admin-load-error";
import { AuditList } from "@/features/capability-admin/audit-list";
import { loadCapabilitySnapshot } from "@/features/capability-admin/server";
import type { CapabilitySnapshot } from "@/features/capability-admin/types";

export default async function AuditsPage() {
  let snapshot: CapabilitySnapshot;
  try {
    snapshot = await loadCapabilitySnapshot();
  } catch (error) {
    return <AdminLoadError error={error} />;
  }
  return <AuditList snapshot={snapshot} />;
}
