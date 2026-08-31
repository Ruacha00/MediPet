import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SkillDetail } from "./skill-detail";
import type { CapabilitySnapshot, SkillSummary } from "./types";

const router = vi.hoisted(() => ({ refresh: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const skill: SkillSummary = {
  skill_id: "skill-1",
  slug: "visit-preparation",
  name: "就诊准备",
  description: "帮助准备门诊材料",
  versions: [
    {
      skill_id: "skill-1",
      version: 1,
      slug: "visit-preparation",
      name: "就诊准备",
      description: "帮助准备门诊材料",
      instructions: "核对身份证。",
      change_note: "初始版本",
      status: "retired",
      active: false,
      created_at: "2030-01-01T00:00:00Z",
      resources: [],
      governance: { skill_type: "tool-assisted" },
      quarantine_reasons: [],
      publish_blockers: [],
    },
    {
      skill_id: "skill-1",
      version: 2,
      slug: "visit-preparation",
      name: "就诊准备",
      description: "帮助准备门诊材料",
      instructions: "核对身份证和既往材料。",
      change_note: "补充既往材料",
      status: "in_review",
      active: false,
      created_at: "2030-01-02T00:00:00Z",
      resources: [{ path: "references/checklist.md", media_type: "text/markdown", size: 20 }],
      governance: { skill_type: "tool-assisted" },
      quarantine_reasons: [],
      publish_blockers: [],
    },
  ],
};

const snapshot: CapabilitySnapshot = {
  skills: [skill],
  tools: [{
    tool_id: "hospital.search_slots",
    versions: [{
      tool_id: "hospital.search_slots",
      version: "1",
      name: "search_slots",
      description: "查询号源",
      input_schema: {},
      output_schema: {},
      confirmation_schema: null,
      effect: "read",
      allowed_stages: ["pre_visit"],
      provider_approval_required: false,
      enabled: true,
      available: true,
      approval_required: false,
    }],
  }],
  bindings: [{
    skill_id: "skill-1",
    skill_version: 2,
    tool_id: "hospital.search_slots",
    tool_version: "1",
  }],
  skillAudits: [],
  toolAudits: [],
};

describe("SkillDetail", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("compares the reviewed version and confirms publication before mutation", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      void _input;
      void _init;
      return Response.json({ status: "published" });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SkillDetail skill={skill} snapshot={snapshot} />);

    fireEvent.click(screen.getByRole("button", { name: "比较" }));
    const comparison = screen.getByRole("region", { name: "版本比较" });
    expect(within(comparison).getByText("核对身份证。")).toBeVisible();
    expect(within(comparison).getByText("核对身份证和既往材料。")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "发布 v2" }));
    expect(fetchMock).not.toHaveBeenCalled();
    const dialog = screen.getByRole("alertdialog", { name: "确认发布 Skill" });
    expect(dialog).toHaveTextContent("发布后将成为当前活动版本");
    fireEvent.click(within(dialog).getByRole("button", { name: "确认发布" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      operation: "transition-skill",
      skillId: "skill-1",
      version: 2,
      action: "publish",
    });
    expect(router.refresh).toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "解绑 Tool" })).not.toBeInTheDocument();
  });
});
