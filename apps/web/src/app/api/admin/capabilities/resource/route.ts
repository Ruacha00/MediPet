import { adminErrorResponse, authorizeCapabilityAdmin, previewSkillResource } from
  "@/features/capability-admin/server";

export async function GET(request: Request) {
  try {
    await authorizeCapabilityAdmin(request, "view-resource");
    const url = new URL(request.url);
    const skillId = url.searchParams.get("skillId") ?? "";
    const version = Number(url.searchParams.get("version"));
    const path = url.searchParams.get("path") ?? "";
    if (!skillId || !path || !Number.isInteger(version) || version <= 0) {
      return Response.json({ detail: "Skill 资源参数无效" }, { status: 422 });
    }
    const response = await previewSkillResource(request, skillId, version, path);
    return new Response(response.body, {
      status: response.status,
      headers: { "Content-Type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch (error) {
    return adminErrorResponse(error);
  }
}
