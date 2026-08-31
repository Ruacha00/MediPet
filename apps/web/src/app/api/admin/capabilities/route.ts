import { adminErrorResponse, authorizeCapabilityAdmin, executeAdminCommand, loadCapabilitySnapshot, parseAdminCommand } from
  "@/features/capability-admin/server";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    return Response.json(await loadCapabilitySnapshot(request));
  } catch (error) {
    return adminErrorResponse(error);
  }
}

export async function POST(request: Request) {
  try {
    await authorizeCapabilityAdmin(request, "command");
    const command = parseAdminCommand(await request.json());
    return Response.json(await executeAdminCommand(request, command));
  } catch (error) {
    return adminErrorResponse(error);
  }
}
