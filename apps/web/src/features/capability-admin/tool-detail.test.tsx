import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToolDetail } from "./tool-detail";
import type { CapabilitySnapshot, ToolSummary } from "./types";

const router = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const tool: ToolSummary = {
  tool_id: "hospital.create_appointment",
  versions: [{
    tool_id: "hospital.create_appointment",
    version: "1",
    name: "create_appointment",
    description: "确认后创建预约挂号",
    input_schema: { type: "object", required: ["slot_id"] },
    output_schema: { type: "object" },
    confirmation_schema: { type: "object" },
    effect: "write",
    allowed_stages: ["pre_visit"],
    provider_approval_required: true,
    enabled: false,
    available: true,
    approval_required: true,
  }],
};

const snapshot: CapabilitySnapshot = {
  tools: [tool],
  skills: [{
    skill_id: "skill-1",
    slug: "appointment-assistance",
    name: "预约协助",
    description: "协助预约",
    versions: [{
      skill_id: "skill-1",
      version: 1,
      slug: "appointment-assistance",
      name: "预约协助",
      description: "协助预约",
      instructions: "创建预约前确认。",
      change_note: "初始版本",
      status: "published",
      active: true,
      created_at: "2030-01-01T00:00:00Z",
      resources: [],
      governance: { skill_type: "tool-assisted" },
      quarantine_reasons: [],
      publish_blockers: [],
    }],
  }],
  bindings: [{
    skill_id: "skill-1",
    skill_version: 1,
    tool_id: "hospital.create_appointment",
    tool_version: "1",
  }],
  skillAudits: [],
  toolAudits: [],
};

describe("ToolDetail", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("confirms runtime enablement and keeps write approval locked", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      void _input;
      void _init;
      return Response.json({ enabled: true });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ToolDetail tool={tool} snapshot={snapshot} />);

    expect(screen.getByRole("checkbox", { name: "要求审批" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "启用 v1" }));
    expect(fetchMock).not.toHaveBeenCalled();
    const dialog = screen.getByRole("alertdialog", { name: "确认启用 Tool" });
    expect(dialog).toHaveTextContent("预约协助");
    fireEvent.click(within(dialog).getByRole("button", { name: "确认启用" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      operation: "set-tool-enabled",
      toolId: "hospital.create_appointment",
      toolVersion: "1",
      enabled: true,
    });
    expect(router.refresh).toHaveBeenCalled();
  });
});
