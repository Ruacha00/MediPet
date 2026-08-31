import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET, POST } from "@/app/api/admin/capabilities/route";
import { GET as exportSkill } from "@/app/api/admin/capabilities/export/route";
import { POST as importSkill } from "@/app/api/admin/capabilities/import/route";
import { GET as previewResource } from "@/app/api/admin/capabilities/resource/route";

describe("capability admin route", () => {
  beforeEach(() => {
    vi.stubEnv("MEDIPET_ENVIRONMENT", "development");
    vi.stubEnv("MEDIPET_INTERNAL_API_URL", "http://api.test");
    vi.stubEnv("MEDIPET_MANAGEMENT_TOKEN", "server-only-token");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("loads a complete snapshot without exposing the management token", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const path = new URL(String(input)).pathname;
      expect(new Headers(init?.headers).get("authorization")).toBe(
        "Bearer server-only-token",
      );
      const payloads: Record<string, object> = {
        "/v1/admin/skills": {
          skills: [{
            skill_id: "skill-1",
            slug: "visit-preparation",
            name: "就诊准备",
            description: "准备门诊就诊",
            versions: [{
              skill_id: "skill-1",
              version: 1,
              slug: "visit-preparation",
              name: "就诊准备",
              description: "准备门诊就诊",
              instructions: "核对材料。",
              change_note: "初始版本",
              status: "draft",
              active: false,
              created_at: "2030-01-01T00:00:00Z",
              resources: [],
              governance: { skill_type: "tool-assisted" },
              quarantine_reasons: [],
              publish_blockers: [],
            }],
          }],
        },
        "/v1/admin/tools": { tools: [] },
        "/v1/admin/skill-audits": { audits: [] },
        "/v1/admin/tool-audits": { audits: [] },
        "/v1/admin/skills/skill-1/versions/1/tool-bindings": {
          bindings: [{
            skill_id: "skill-1",
            skill_version: 1,
            tool_id: "hospital.search_slots",
            tool_version: "1",
          }],
        },
      };
      return Response.json(payloads[path] ?? {}, { status: payloads[path] ? 200 : 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(new Request("http://web.test/api/admin/capabilities"));
    const body = await response.text();

    expect(response.status).toBe(200);
    expect(JSON.parse(body).bindings).toEqual([{
      skill_id: "skill-1",
      skill_version: 1,
      tool_id: "hospital.search_slots",
      tool_version: "1",
    }]);
    expect(body).not.toContain("server-only-token");
  });

  it("does not expose the development console in production", async () => {
    vi.stubEnv("MEDIPET_ENVIRONMENT", "production");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const responses = await Promise.all([
      GET(new Request("http://web.test/api/admin/capabilities")),
      POST(new Request("http://web.test/api/admin/capabilities", { method: "POST", body: "{" })),
      importSkill(new Request("http://web.test/api/admin/capabilities/import", { method: "POST" })),
      exportSkill(new Request("http://web.test/api/admin/capabilities/export")),
      previewResource(new Request("http://web.test/api/admin/capabilities/resource")),
    ]);

    expect(responses.map((response) => response.status)).toEqual([404, 404, 404, 404, 404]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps the console disabled when the environment is not explicitly known", async () => {
    vi.stubEnv("MEDIPET_ENVIRONMENT", "");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(new Request("http://web.test/api/admin/capabilities"));

    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed with a safe message when server credentials are missing", async () => {
    vi.stubEnv("MEDIPET_MANAGEMENT_TOKEN", "");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(new Request("http://web.test/api/admin/capabilities"));

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ detail: "开发管理认证未配置或不一致" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects commands outside the explicit management whitelist", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const request = new Request("http://web.test/api/admin/capabilities", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operation: "run-arbitrary-code" }),
    });

    const response = await POST(request);

    expect(response.status).toBe(422);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("re-reads the Tool and submits both configuration fields", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const path = new URL(String(input)).pathname;
      if (path === "/v1/admin/tools" && !init?.method) {
        return Response.json({ tools: [{ tool_id: "hospital.read_record", versions: [{
          tool_id: "hospital.read_record", version: "3", name: "read_record", description: "读取摘要",
          input_schema: {}, output_schema: {}, confirmation_schema: null, effect: "read",
          allowed_stages: ["in_visit"], provider_approval_required: false, enabled: false,
          available: true, approval_required: true,
        }] }] });
      }
      expect(init?.method).toBe("PATCH");
      expect(JSON.parse(String(init?.body))).toEqual({ enabled: true, approval_required: true });
      return Response.json({ enabled: true, approval_required: true });
    });
    vi.stubGlobal("fetch", fetchMock);
    const request = new Request("http://web.test/api/admin/capabilities", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operation: "set-tool-enabled", toolId: "hospital.read_record", toolVersion: "3", enabled: true }),
    });

    const response = await POST(request);

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
