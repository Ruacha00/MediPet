import type { AdminCommand } from "./types";

export async function sendAdminCommand(command: AdminCommand, fallback: string) {
  const response = await fetch("/api/admin/capabilities", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(command),
  });
  const result = await response.json() as { detail?: string };
  if (!response.ok) throw new Error(result.detail ?? fallback);
  return result;
}
