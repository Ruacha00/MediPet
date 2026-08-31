import { notFound } from "next/navigation";

import { AdminLoadError } from "@/features/capability-admin/admin-load-error";
import { loadCapabilitySnapshot } from "@/features/capability-admin/server";
import { ToolDetail } from "@/features/capability-admin/tool-detail";
import type { CapabilitySnapshot } from "@/features/capability-admin/types";

export default async function ToolDetailPage({ params }: { params: Promise<{ toolId: string }> }) {
  const { toolId } = await params;
  let snapshot: CapabilitySnapshot;
  try {
    snapshot = await loadCapabilitySnapshot();
  } catch (error) {
    return <AdminLoadError error={error} />;
  }
  const tool = snapshot.tools.find((item) => item.tool_id === toolId);
  if (!tool) notFound();
  return <ToolDetail tool={tool} snapshot={snapshot} />;
}
