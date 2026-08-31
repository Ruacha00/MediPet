import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ToolList } from "./tool-list";
import type { CapabilitySnapshot } from "./types";

const snapshot: CapabilitySnapshot = {
  skills: [{
    skill_id: "skill-active",
    slug: "appointment-assistance",
    name: "预约协助",
    description: "协助预约",
    versions: [{
      skill_id: "skill-active", version: 1, slug: "appointment-assistance", name: "预约协助",
      description: "协助预约", instructions: "确认后创建。", change_note: "初始", status: "published",
      active: true, created_at: "2030-01-01T00:00:00Z", resources: [], governance: { skill_type: "tool-assisted" },
      quarantine_reasons: [], publish_blockers: [],
    }],
  }],
  tools: [
    { tool_id: "hospital.create_appointment", versions: [{
      tool_id: "hospital.create_appointment", version: "1", name: "create_appointment",
      description: "创建预约", input_schema: {}, output_schema: {}, confirmation_schema: {}, effect: "write",
      allowed_stages: ["pre_visit"], provider_approval_required: true, available: true, enabled: true,
      approval_required: true,
    }, {
      tool_id: "hospital.create_appointment", version: "2", name: "create_appointment",
      description: "创建预约新版", input_schema: {}, output_schema: {}, confirmation_schema: {}, effect: "write",
      allowed_stages: ["pre_visit"], provider_approval_required: true, available: true, enabled: false,
      approval_required: true,
    }] },
    { tool_id: "hospital.list_slots", versions: [{
      tool_id: "hospital.list_slots", version: "2", name: "list_slots", description: "查询号源",
      input_schema: {}, output_schema: {}, confirmation_schema: null, effect: "read", allowed_stages: ["pre_visit"],
      provider_approval_required: false, available: false, enabled: false, approval_required: false,
    }] },
  ],
  bindings: [{ skill_id: "skill-active", skill_version: 1, tool_id: "hospital.create_appointment", tool_version: "1" }],
  skillAudits: [],
  toolAudits: [],
};

describe("ToolList", () => {
  afterEach(cleanup);

  it("searches and filters Tool Registry while marking active bindings", () => {
    render(<ToolList snapshot={snapshot} />);
    expect(screen.getByText("被活动 Skill 使用")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("启用状态"), { target: { value: "yes" } });
    const matching = screen.getByRole("link", { name: "查看 Tool hospital.create_appointment" });
    expect(within(matching).getByText("v1")).toBeInTheDocument();
    expect(within(matching).queryByText("v2")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("启用状态"), { target: { value: "all" } });

    fireEvent.change(screen.getByLabelText("可用性"), { target: { value: "missing" } });
    expect(screen.getByText("hospital.list_slots")).toBeInTheDocument();
    expect(screen.queryByText("hospital.create_appointment")).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("searchbox", { name: "搜索 Tools" }), { target: { value: "unmatched" } });
    expect(screen.getByText("没有符合条件的 Tool")).toBeInTheDocument();
  });
});
