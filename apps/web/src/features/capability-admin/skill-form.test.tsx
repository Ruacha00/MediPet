import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SkillForm } from "./skill-form";

const router = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

describe("SkillForm", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("creates a draft through the same-origin capability boundary", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      void _input;
      void _init;
      return Response.json({ skill_id: "skill-created" }, { status: 201 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SkillForm />);

    fireEvent.change(screen.getByLabelText("Slug"), { target: { value: "visit-checklist" } });
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "就诊清单" } });
    fireEvent.change(screen.getByLabelText("描述"), { target: { value: "准备就诊材料" } });
    fireEvent.change(screen.getByLabelText("Skill 类型"), {
      target: { value: "instruction-only" },
    });
    fireEvent.change(screen.getByLabelText("Instructions Markdown"), {
      target: { value: "核对患者需要携带的材料。" },
    });
    fireEvent.change(screen.getByLabelText("变更说明"), { target: { value: "初始版本" } });
    fireEvent.click(screen.getByRole("button", { name: "创建草稿" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      operation: "create-skill",
      payload: {
        slug: "visit-checklist",
        name: "就诊清单",
        description: "准备就诊材料",
        instructions: "核对患者需要携带的材料。",
        change_note: "初始版本",
        skill_type: "instruction-only",
      },
    });
    expect(router.push).toHaveBeenCalledWith("/admin/capabilities/skills/skill-created");
  });

  it("prevents duplicate submission and renders a recoverable API error", async () => {
    let resolveRequest!: (response: Response) => void;
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => {
      void _input;
      void _init;
      return new Promise<Response>((resolve) => { resolveRequest = resolve; });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SkillForm />);
    fireEvent.change(screen.getByLabelText("Slug"), { target: { value: "visit-checklist" } });
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "就诊清单" } });
    fireEvent.change(screen.getByLabelText("描述"), { target: { value: "准备材料" } });
    fireEvent.change(screen.getByLabelText("Instructions Markdown"), { target: { value: "核对材料。" } });
    fireEvent.change(screen.getByLabelText("变更说明"), { target: { value: "初始版本" } });
    const submit = screen.getByRole("button", { name: "创建草稿" });

    fireEvent.click(submit);
    await waitFor(() => expect(screen.getByRole("button", { name: "正在创建" })).toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "正在创建" }));
    expect(fetchMock).toHaveBeenCalledOnce();
    resolveRequest(Response.json({ detail: "Skill slug 已存在" }, { status: 409 }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Skill slug 已存在");
    expect(screen.getByRole("button", { name: "创建草稿" })).toBeEnabled();
  });
});
