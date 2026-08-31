import { adminErrorResponse, authorizeCapabilityAdmin, importSkill } from "@/features/capability-admin/server";

export async function POST(request: Request) {
  try {
    await authorizeCapabilityAdmin(request, "import-skill");
    const contentType = request.headers.get("content-type")?.split(";", 1)[0];
    if (contentType !== "application/zip") {
      return Response.json({ detail: "Skill 导入仅接受 ZIP 文件" }, { status: 415 });
    }
    const response = await importSkill(request, await request.arrayBuffer());
    return new Response(response.body, {
      status: response.status,
      headers: { "Content-Type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch (error) {
    return adminErrorResponse(error);
  }
}
