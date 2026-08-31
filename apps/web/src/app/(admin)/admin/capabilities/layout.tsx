import { notFound } from "next/navigation";

import { AdminShell } from "@/features/capability-admin/admin-shell";
import { capabilityAdminEnabled } from "@/features/capability-admin/server";

export const dynamic = "force-dynamic";

export default function CapabilityAdminLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  if (!capabilityAdminEnabled()) notFound();
  return <AdminShell>{children}</AdminShell>;
}
