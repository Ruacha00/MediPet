import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SkillList } from "./skill-list";
import type { CapabilitySnapshot } from "./types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

const snapshot: CapabilitySnapshot = {
  skills: [
    {
      skill_id: "skill-draft",
      slug: "visit-preparation",
      name: "就诊准备",
      description: "帮助准备门诊材料",
      versions: [{
        skill_id: "skill-draft",
        version: 2,
        slug: "visit-preparation",
        name: "就诊准备",
        description: "帮助准备门诊材料",
        instructions: "核对材料。",
        change_note: "补充材料",
        status: "draft",
        active: false,
        created_at: "2030-01-02T00:00:00Z",
        resources: [],
        governance: { skill_type: "instruction-only" },
        quarantine_reasons: [],
        publish_blockers: [],
      }],
    },
    {
      skill_id: "skill-quarantine",
      slug: "unsafe-import",
      name: "隔离导入",
      description: "包含脚本",
      versions: [{
        skill_id: "skill-quarantine",
        version: 1,
        slug: "unsafe-import",
        name: "隔离导入",
        description: "包含脚本",
        instructions: "不要执行。",
        change_note: "导入",
        status: "quarantined",
        active: false,
        created_at: "2030-01-01T00:00:00Z",
        resources: [],
        governance: { skill_type: "tool-assisted" },
        quarantine_reasons: ["包包含可执行内容"],
        publish_blockers: [],
      }],
    },
  ],
  tools: [],
  bindings: [],
  skillAudits: [],
  toolAudits: [],
};

describe("SkillList", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("searches and filters Skills without losing governance state", () => {
    render(<SkillList snapshot={snapshot} />);

    const rows = screen.getAllByRole("link", { name: /查看 Skill/ });
    expect(rows[0]).toHaveAccessibleName(/隔离导入/);
    expect(rows[1]).toHaveAccessibleName(/就诊准备/);

    fireEvent.change(screen.getByRole("searchbox", { name: "搜索 Skills" }), {
      target: { value: "材料" },
    });
    expect(screen.getByRole("link", { name: /就诊准备/ })).toBeVisible();
    expect(screen.queryByRole("link", { name: /隔离导入/ })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("生命周期状态"), {
      target: { value: "quarantined" },
    });
    expect(screen.getByText("没有符合条件的 Skill")).toBeVisible();
  });

  it("shows ZIP metadata before sending the archive unchanged", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      void _input;
      void _init;
      return Response.json({ skill_id: "imported" });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SkillList snapshot={snapshot} />);
    const archive = new File([new Uint8Array(2048)], "safe-skill.zip", { type: "application/zip" });

    fireEvent.change(screen.getByLabelText("选择 Skill ZIP"), { target: { files: [archive] } });

    expect(screen.getByText("safe-skill.zip")).toBeInTheDocument();
    expect(screen.getByText(/2\.0 KB/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(fetchMock.mock.calls[0][1]?.body).toBe(archive);
  });
});
