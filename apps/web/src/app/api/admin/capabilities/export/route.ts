import { adminErrorResponse, authorizeCapabilityAdmin, exportSkill } from "@/features/capability-admin/server";

export async function GET(request: Request) {
  try {
    await authorizeCapabilityAdmin(request, "export-skill");
    const url = new URL(request.url);
    const skillId = url.searchParams.get("skillId") ?? "";
    const version = Number(url.searchParams.get("version"));
    if (!skillId || !Number.isInteger(version) || version <= 0) {
      return Response.json({ detail: "Skill 导出参数无效" }, { status: 422 });
    }
    const response = await exportSkill(request, skillId, version);
    const headers = new Headers();
    headers.set("Content-Type", response.headers.get("content-type") ?? "application/zip");
    const disposition = response.headers.get("content-disposition");
    if (disposition) headers.set("Content-Disposition", disposition);
    return new Response(response.body, { status: response.status, headers });
  } catch (error) {
    return adminErrorResponse(error);
  }
}
