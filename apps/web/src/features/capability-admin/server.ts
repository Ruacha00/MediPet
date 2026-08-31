import type {
  AdminCommand,
  CapabilitySnapshot,
  SkillAudit,
  SkillSummary,
  ToolAudit,
  ToolBinding,
  ToolSummary,
  ToolVersion,
} from "./types";

export type AdminPrincipal = {
  id: "development-admin";
  displayName: "开发管理员";
};

export class CapabilityAdminError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export function capabilityAdminEnabled() {
  const environment = process.env.MEDIPET_ENVIRONMENT?.trim().toLowerCase();
  return environment === "development" || environment === "test";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasStrings(value: Record<string, unknown>, fields: string[]) {
  return fields.every((field) => typeof value[field] === "string");
}

export function parseAdminCommand(value: unknown): AdminCommand {
  if (!isRecord(value) || typeof value.operation !== "string") {
    throw new CapabilityAdminError(422, "无效的治理命令");
  }
  const reject = () => { throw new CapabilityAdminError(422, "无效的治理命令"); };
  switch (value.operation) {
    case "create-skill":
      if (!isRecord(value.payload)
        || !hasStrings(value.payload, ["slug", "name", "description", "instructions", "change_note"])
        || !["instruction-only", "tool-assisted"].includes(String(value.payload.skill_type))) reject();
      break;
    case "edit-skill":
      if (typeof value.skillId !== "string" || !isRecord(value.payload)
        || !hasStrings(value.payload, ["instructions", "change_note"])) reject();
      break;
    case "transition-skill":
      if (typeof value.skillId !== "string" || !Number.isInteger(value.version)
        || !["submit-review", "publish", "retire", "activate"].includes(String(value.action))) reject();
      break;
    case "bind-tool":
    case "unbind-tool":
      if (!hasStrings(value, ["skillId", "toolId", "toolVersion"])
        || !Number.isInteger(value.skillVersion)) reject();
      break;
    case "set-tool-enabled":
      if (!hasStrings(value, ["toolId", "toolVersion"]) || typeof value.enabled !== "boolean") reject();
      break;
    case "set-tool-approval":
      if (!hasStrings(value, ["toolId", "toolVersion"]) || typeof value.approvalRequired !== "boolean") reject();
      break;
    default:
      reject();
  }
  return value as AdminCommand;
}

export async function authorizeCapabilityAdmin(
  _request: Request | null,
  _action: string,
): Promise<AdminPrincipal> {
  void _request;
  void _action;
  if (!capabilityAdminEnabled()) {
    throw new CapabilityAdminError(404, "页面不存在");
  }
  return { id: "development-admin", displayName: "开发管理员" };
}

async function managementFetch(path: string, init?: RequestInit) {
  const token = process.env.MEDIPET_MANAGEMENT_TOKEN?.trim();
  if (!token) {
    throw new CapabilityAdminError(503, "开发管理认证未配置或不一致");
  }
  const baseUrl = process.env.MEDIPET_INTERNAL_API_URL ?? "http://localhost:8000";
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    cache: "no-store",
    headers,
  });
  if (!response.ok) {
    let detail = "能力治理请求失败";
    try {
      const body = await response.json() as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.trim()) detail = body.detail;
    } catch {
      // Keep the stable safe fallback.
    }
    if (response.status === 401) {
      throw new CapabilityAdminError(503, "开发管理认证未配置或不一致");
    }
    throw new CapabilityAdminError(response.status, detail);
  }
  return response;
}

async function managementJson<T>(path: string, init?: RequestInit): Promise<T> {
  return await (await managementFetch(path, init)).json() as T;
}

export async function loadCapabilitySnapshot(
  request: Request | null = null,
): Promise<CapabilitySnapshot> {
  await authorizeCapabilityAdmin(request, "view");
  const [skillResult, toolResult, skillAuditResult, toolAuditResult] = await Promise.all([
    managementJson<{ skills: SkillSummary[] }>("/v1/admin/skills"),
    managementJson<{ tools: ToolSummary[] }>("/v1/admin/tools"),
    managementJson<{ audits: SkillAudit[] }>("/v1/admin/skill-audits"),
    managementJson<{ audits: ToolAudit[] }>("/v1/admin/tool-audits"),
  ]);
  const bindingResults = await Promise.all(
    skillResult.skills.flatMap((skill) => skill.versions.map(async (version) => {
      const path = `/v1/admin/skills/${encodeURIComponent(skill.skill_id)}`
        + `/versions/${version.version}/tool-bindings`;
      return await managementJson<{ bindings: ToolBinding[] }>(path);
    })),
  );
  return {
    skills: skillResult.skills,
    tools: toolResult.tools,
    bindings: bindingResults.flatMap((result) => result.bindings),
    skillAudits: skillAuditResult.audits,
    toolAudits: toolAuditResult.audits,
  };
}

function jsonRequest(payload: unknown, method = "POST"): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

function findToolVersion(tools: ToolSummary[], toolId: string, version: string): ToolVersion {
  const selected = tools.find((tool) => tool.tool_id === toolId)?.versions
    .find((toolVersion) => toolVersion.version === version);
  if (!selected) throw new CapabilityAdminError(404, "Tool 版本不存在");
  return selected;
}

export async function executeAdminCommand(request: Request, command: AdminCommand) {
  await authorizeCapabilityAdmin(request, command.operation);
  switch (command.operation) {
    case "create-skill":
      return await managementJson("/v1/admin/skills", jsonRequest(command.payload));
    case "edit-skill":
      return await managementJson(
        `/v1/admin/skills/${encodeURIComponent(command.skillId)}`,
        jsonRequest(command.payload, "PATCH"),
      );
    case "transition-skill":
      return await managementJson(
        `/v1/admin/skills/${encodeURIComponent(command.skillId)}`
          + `/versions/${command.version}/${command.action}`,
        { method: "POST" },
      );
    case "bind-tool":
      return await managementJson(
        `/v1/admin/skills/${encodeURIComponent(command.skillId)}`
          + `/versions/${command.skillVersion}/tool-bindings`,
        jsonRequest({ tool_id: command.toolId, tool_version: command.toolVersion }),
      );
    case "unbind-tool":
      return await managementJson(
        `/v1/admin/skills/${encodeURIComponent(command.skillId)}`
          + `/versions/${command.skillVersion}/tool-bindings/`
          + `${encodeURIComponent(command.toolId)}/versions/`
          + encodeURIComponent(command.toolVersion),
        { method: "DELETE" },
      );
    case "set-tool-enabled":
    case "set-tool-approval": { // Both fields are required by the FastAPI contract.
      const toolResult = await managementJson<{ tools: ToolSummary[] }>("/v1/admin/tools");
      const current = findToolVersion(
        toolResult.tools,
        command.toolId,
        command.toolVersion,
      );
      return await managementJson(
        `/v1/admin/tools/${encodeURIComponent(command.toolId)}`
          + `/versions/${encodeURIComponent(command.toolVersion)}`,
        jsonRequest({
          enabled: command.operation === "set-tool-enabled"
            ? command.enabled
            : current.enabled,
          approval_required: command.operation === "set-tool-approval"
            ? command.approvalRequired
            : current.approval_required,
        }, "PATCH"),
      );
    }
  }
}

export async function importSkill(request: Request, payload: ArrayBuffer) {
  await authorizeCapabilityAdmin(request, "import-skill");
  return await managementFetch("/v1/admin/skills/import", {
    method: "POST",
    headers: { "Content-Type": "application/zip" },
    body: payload,
  });
}

export async function exportSkill(request: Request, skillId: string, version: number) {
  await authorizeCapabilityAdmin(request, "export-skill");
  return await managementFetch(
    `/v1/admin/skills/${encodeURIComponent(skillId)}/versions/${version}/export`,
  );
}

export async function previewSkillResource(
  request: Request,
  skillId: string,
  version: number,
  path: string,
) {
  await authorizeCapabilityAdmin(request, "view-resource");
  return await managementFetch(
    `/v1/admin/skills/${encodeURIComponent(skillId)}/versions/${version}/resources/`
      + path.split("/").map(encodeURIComponent).join("/"),
  );
}

export function adminErrorResponse(error: unknown) {
  if (error instanceof CapabilityAdminError) {
    return Response.json({ detail: error.message }, { status: error.status });
  }
  if (error instanceof SyntaxError) {
    return Response.json({ detail: "无效的治理命令" }, { status: 422 });
  }
  return Response.json({ detail: "能力治理服务暂时不可用" }, { status: 503 });
}
