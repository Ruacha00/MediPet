import { AdminLoadError } from "@/features/capability-admin/admin-load-error";
import { loadCapabilitySnapshot } from "@/features/capability-admin/server";
import { ToolList } from "@/features/capability-admin/tool-list";
import type { CapabilitySnapshot } from "@/features/capability-admin/types";

export default async function ToolsPage() {
  let snapshot: CapabilitySnapshot;
  try {
    snapshot = await loadCapabilitySnapshot();
  } catch (error) {
    return <AdminLoadError error={error} />;
  }
  return <ToolList snapshot={snapshot} />;
}
