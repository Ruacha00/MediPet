import { Suspense } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatShell } from "./chat-shell";

const chatMock = vi.hoisted(() => ({ status: "ready" }));

vi.mock("@ai-sdk/react", () => ({
  useChat: (options: { messages?: unknown[] }) => ({
    messages: options.messages ?? [],
    sendMessage: vi.fn(),
    status: chatMock.status,
    stop: vi.fn(),
    setMessages: vi.fn(),
    error: undefined,
  }),
}));

describe("ChatShell", () => {
  beforeAll(() => {
    Element.prototype.scrollIntoView = vi.fn();
  });

  beforeEach(() => {
    chatMock.status = "ready";
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows history restoration failure when no messages were recovered", async () => {
    await act(async () => {
      render(
        <Suspense fallback={<p>正在加载</p>}>
          <ChatShell history={Promise.resolve({ messages: [], failed: true })} />
        </Suspense>,
      );
    });

    expect(
      await screen.findByText("历史对话暂时无法恢复，请稍后刷新重试。"),
    ).toBeVisible();
  });

  it("shows a true empty history state without inserting a demo visit matter", async () => {
    await act(async () => {
      render(
        <Suspense fallback={<p>正在加载</p>}>
          <ChatShell
            history={Promise.resolve({ messages: [], failed: false })}
            visitMatters={Promise.resolve([])}
          />
        </Suspense>,
      );
    });

    expect(await screen.findByRole("heading", { name: "还没有历史记录" })).toBeVisible();
    expect(screen.queryByText("初次咨询")).not.toBeInTheDocument();
    expect(
      within(screen.getByRole("complementary", { name: "就诊事项" }))
        .getByRole("button", { name: "新建就诊事项" }),
    ).toBeEnabled();
  });

  it("archives the current history and switches to the next active item", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/visit-matter-current/archive")) {
        return {
          ok: true,
          json: async () => ({
            visit_matter_id: "visit-matter-current",
            title: "初次咨询",
            visit_stage: "pre_visit",
            patient_display_name: "演示患者",
            participant_display_name: "患者本人",
            archived_at: "2030-01-01T00:00:00Z",
          }),
        };
      }
      if (url.includes("archived=true")) {
        return {
          ok: true,
          json: async () => ({
            visit_matters: [{
              visit_matter_id: "visit-matter-current",
              title: "初次咨询",
              visit_stage: "pre_visit",
              patient_display_name: "演示患者",
              participant_display_name: "患者本人",
              archived_at: "2030-01-01T00:00:00Z",
            }],
          }),
        };
      }
      if (url.includes("/v1/visit-matters?")) {
        return {
          ok: true,
          json: async () => ({
            visit_matters: [{
              visit_matter_id: "visit-matter-next",
              title: "复诊准备",
              visit_stage: "pre_visit",
              patient_display_name: "演示患者",
              participant_display_name: "患者本人",
              archived_at: null,
            }],
          }),
        };
      }
      if (url.includes("/visit-matter-next/messages")) {
        return {
          ok: true,
          json: async () => ({
            visit_matter_id: "visit-matter-next",
            messages: [{
              id: "message-next",
              role: "user",
              state: "completed",
              parts: [{ type: "text", text: "下一条历史" }],
              created_at: "2030-01-01T00:00:00Z",
              updated_at: "2030-01-01T00:00:00Z",
            }],
          }),
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => {
      render(
        <Suspense fallback={<p>正在加载</p>}>
          <ChatShell
            history={Promise.resolve({ messages: [], failed: false })}
            visitMatters={Promise.resolve([
              {
                visit_matter_id: "visit-matter-current",
                title: "初次咨询",
                visit_stage: "pre_visit",
                patient_display_name: "演示患者",
                participant_display_name: "患者本人",
                archived_at: null,
              },
              {
                visit_matter_id: "visit-matter-next",
                title: "复诊准备",
                visit_stage: "pre_visit",
                patient_display_name: "演示患者",
                participant_display_name: "患者本人",
                archived_at: null,
              },
            ])}
          />
        </Suspense>,
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "归档 初次咨询" }));

    expect(await screen.findByText("下一条历史")).toBeVisible();
    expect(screen.getByRole("heading", { name: "复诊准备" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /初次咨询/ })).not.toBeInTheDocument();
  });

  it("renames and restores an archived history item", async () => {
    let restored = false;
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("archived=true")) {
        return {
          ok: true,
          json: async () => ({
            visit_matters: restored ? [] : [{
              visit_matter_id: "visit-matter-archived",
              title: "旧记录",
              visit_stage: "pre_visit",
              patient_display_name: "演示患者",
              participant_display_name: "患者本人",
              archived_at: "2030-01-01T00:00:00Z",
            }],
          }),
        };
      }
      if (url.includes("/visit-matter-archived/messages")) {
        return {
          ok: true,
          json: async () => ({ visit_matter_id: "visit-matter-archived", messages: [] }),
        };
      }
      if (url.endsWith("/visit-matter-archived/title")) {
        expect(init?.method).toBe("PATCH");
        return {
          ok: true,
          json: async () => ({
            visit_matter_id: "visit-matter-archived",
            title: "复诊资料",
            visit_stage: "pre_visit",
            patient_display_name: "演示患者",
            participant_display_name: "患者本人",
            archived_at: "2030-01-01T00:00:00Z",
          }),
        };
      }
      if (url.endsWith("/visit-matter-archived/restore")) {
        restored = true;
        return {
          ok: true,
          json: async () => ({
            visit_matter_id: "visit-matter-archived",
            title: "复诊资料",
            visit_stage: "pre_visit",
            patient_display_name: "演示患者",
            participant_display_name: "患者本人",
            archived_at: null,
          }),
        };
      }
      if (url.includes("/v1/visit-matters?")) {
        return {
          ok: true,
          json: async () => ({
            visit_matters: restored ? [{
              visit_matter_id: "visit-matter-archived",
              title: "复诊资料",
              visit_stage: "pre_visit",
              patient_display_name: "演示患者",
              participant_display_name: "患者本人",
              archived_at: null,
            }] : [],
          }),
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => {
      render(
        <Suspense fallback={<p>正在加载</p>}>
          <ChatShell
            history={Promise.resolve({ messages: [], failed: false })}
            visitMatters={Promise.resolve([])}
          />
        </Suspense>,
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "查看已归档" }));
    fireEvent.click(await screen.findByRole("button", { name: "重命名 旧记录" }));
    fireEvent.change(screen.getByRole("textbox", { name: "新的历史记录名称" }), {
      target: { value: "复诊资料" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
    fireEvent.click(await screen.findByRole("button", { name: "恢复 复诊资料" }));
    expect(await screen.findByRole("heading", { name: "复诊资料" })).toBeVisible();
    expect(screen.getByText("独立就诊上下文 · 诊前准备")).toBeVisible();
  });

  it("creates a distinct visit matter instead of clearing the current context", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/v1/visit-matters")) {
        return {
          ok: true,
          json: async () => ({
            visit_matter_id: "visit-matter-new",
            title: "新的就诊事项",
            visit_stage: "pre_visit",
            patient_display_name: "演示患者",
            participant_display_name: "患者本人",
          }),
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => {
      render(
        <Suspense fallback={<p>正在加载</p>}>
          <ChatShell history={Promise.resolve({ messages: [], failed: false })} />
        </Suspense>,
      );
    });

    fireEvent.click(
      within(screen.getByRole("complementary", { name: "就诊事项" }))
        .getByRole("button", { name: "新建就诊事项" }),
    );

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "http://localhost:8000/v1/visit-matters",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(
      await within(screen.getByRole("navigation", { name: "历史记录列表" })).findByRole(
        "button",
        { name: /演示患者 · 新的就诊事项/ },
      ),
    ).toHaveAttribute("aria-current", "page");
  });

  it("loads another visit matter without retaining the current messages", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/visit-matter-follow-up/messages")) {
        return {
          ok: true,
          json: async () => ({
            visit_matter_id: "visit-matter-follow-up",
            messages: [
              {
                id: "message-follow-up",
                role: "user",
                state: "completed",
                parts: [{ type: "text", text: "这是复诊事项" }],
                created_at: "2030-01-01T00:00:00Z",
                updated_at: "2030-01-01T00:00:00Z",
              },
            ],
          }),
        };
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => {
      render(
        <Suspense fallback={<p>正在加载</p>}>
          <ChatShell
            history={Promise.resolve({ messages: [], failed: false })}
            visitMatters={Promise.resolve([
              {
                visit_matter_id: "visit-matter-demo",
                title: "初次咨询",
                visit_stage: "pre_visit",
                patient_display_name: "演示患者",
                participant_display_name: "患者本人",
                archived_at: null,
              },
              {
                visit_matter_id: "visit-matter-follow-up",
                title: "复诊准备",
                visit_stage: "pre_visit",
                patient_display_name: "演示患者",
                participant_display_name: "患者本人",
                archived_at: null,
              },
            ])}
          />
        </Suspense>,
      );
    });

    const followUp = await screen.findByRole("button", { name: /演示患者 · 复诊准备/ });
    fireEvent.click(followUp);

    expect(await screen.findByText("这是复诊事项")).toBeVisible();
  });

  it("loads the restored item's history and keeps the server activity order", async () => {
    const activeAfterRestore = [
      {
        visit_matter_id: "visit-newer",
        title: "较新的活动记录",
        visit_stage: "pre_visit" as const,
        patient_display_name: "演示患者",
        participant_display_name: "患者本人",
        archived_at: null,
      },
      {
        visit_matter_id: "visit-older",
        title: "较早的归档记录",
        visit_stage: "pre_visit" as const,
        patient_display_name: "演示患者",
        participant_display_name: "患者本人",
        archived_at: null,
      },
    ];
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("archived=true")) {
        return {
          ok: true,
          json: async () => ({
            visit_matters: url.includes("after-restore") ? [] : [{
              ...activeAfterRestore[1],
              archived_at: "2030-01-01T00:00:00Z",
            }],
          }),
        };
      }
      if (url.endsWith("/visit-older/restore")) {
        return { ok: true, json: async () => activeAfterRestore[1] };
      }
      if (url.includes("/visit-older/messages")) {
        return {
          ok: true,
          json: async () => ({
            visit_matter_id: "visit-older",
            messages: [{
              id: "older-message",
              role: "user",
              state: "completed",
              parts: [{ type: "text", text: "这是被恢复事项自己的历史" }],
              created_at: "2030-01-01T00:00:00Z",
              updated_at: "2030-01-01T00:00:00Z",
            }],
          }),
        };
      }
      if (url.includes("/v1/visit-matters?")) {
        return { ok: true, json: async () => ({ visit_matters: activeAfterRestore }) };
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => {
      render(
        <Suspense fallback={<p>正在加载</p>}>
          <ChatShell
            history={Promise.resolve({ messages: [], failed: false })}
            visitMatters={Promise.resolve([activeAfterRestore[0]])}
          />
        </Suspense>,
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "查看已归档" }));
    fireEvent.click(await screen.findByRole("button", { name: "恢复 较早的归档记录" }));

    expect(await screen.findByText("这是被恢复事项自己的历史")).toBeVisible();
    const activeItems = within(screen.getByRole("navigation", { name: "历史记录列表" }))
      .getAllByRole("button", { name: /演示患者 ·/ });
    expect(activeItems.map((item) => item.textContent)).toEqual([
      expect.stringContaining("较新的活动记录"),
      expect.stringContaining("较早的归档记录"),
    ]);
  });

  it("keeps the next visit selected when its history reload fails after archive", async () => {
    const current = {
      visit_matter_id: "visit-current",
      title: "当前记录",
      visit_stage: "pre_visit" as const,
      patient_display_name: "演示患者",
      participant_display_name: "患者本人",
      archived_at: null,
    };
    const next = { ...current, visit_matter_id: "visit-next", title: "下一条记录" };
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/visit-current/archive")) {
        return { ok: true, json: async () => ({ ...current, archived_at: "2030-01-01T00:00:00Z" }) };
      }
      if (url.includes("archived=true")) {
        return { ok: true, json: async () => ({ visit_matters: [{ ...current, archived_at: "2030-01-01T00:00:00Z" }] }) };
      }
      if (url.includes("/v1/visit-matters?")) {
        return { ok: true, json: async () => ({ visit_matters: [next] }) };
      }
      if (url.includes("/visit-next/messages")) {
        return { ok: false, json: async () => ({ detail: "unavailable" }) };
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => {
      render(
        <Suspense fallback={<p>正在加载</p>}>
          <ChatShell
            history={Promise.resolve({ messages: [], failed: false })}
            visitMatters={Promise.resolve([current, next])}
          />
        </Suspense>,
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "归档 当前记录" }));

    expect(await screen.findByRole("heading", { name: "下一条记录" })).toBeVisible();
    expect(screen.getByText("历史对话暂时无法恢复，请稍后刷新重试。")).toBeVisible();
  });

  it("keeps an archived item out of the active UI when list refresh fails", async () => {
    const current = {
      visit_matter_id: "visit-current",
      title: "待归档记录",
      visit_stage: "pre_visit" as const,
      patient_display_name: "演示患者",
      participant_display_name: "患者本人",
      archived_at: null,
    };
    const next = { ...current, visit_matter_id: "visit-next", title: "保留的活动记录" };
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith("/visit-current/archive")) {
        return { ok: true, json: async () => ({ ...current, archived_at: "2030-01-01T00:00:00Z" }) };
      }
      if (url.includes("/visit-next/messages")) {
        return { ok: true, json: async () => ({ visit_matter_id: "visit-next", messages: [] }) };
      }
      if (url.includes("/v1/visit-matters?")) {
        return { ok: false, json: async () => ({ detail: "unavailable" }) };
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    await act(async () => {
      render(
        <Suspense fallback={<p>正在加载</p>}>
          <ChatShell
            history={Promise.resolve({ messages: [], failed: false })}
            visitMatters={Promise.resolve([current, next])}
          />
        </Suspense>,
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "归档 待归档记录" }));

    expect(await screen.findByRole("heading", { name: "保留的活动记录" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /演示患者 · 待归档记录/ })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("归档已完成，但历史记录列表刷新失败");
  });

  it("distinguishes an initial list failure from a genuinely empty history", async () => {
    await act(async () => {
      render(
        <Suspense fallback={<p>正在加载</p>}>
          <ChatShell
            history={Promise.resolve({ messages: [], failed: false })}
            visitMatters={Promise.resolve([])}
            visitMattersFailed
          />
        </Suspense>,
      );
    });

    expect(await screen.findByRole("heading", { name: "历史记录加载失败" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "还没有历史记录" })).not.toBeInTheDocument();
  });

  it("avoids smooth scrolling while a response is streaming", async () => {
    chatMock.status = "streaming";

    await act(async () => {
      render(
        <Suspense fallback={<p>正在加载</p>}>
          <ChatShell
            history={Promise.resolve({
              failed: false,
              messages: [{
                id: "message-streaming",
                role: "assistant",
                parts: [{ type: "text", text: "正在生成" }],
              }],
            })}
            visitMatters={Promise.resolve([{
              visit_matter_id: "visit-matter-demo",
              title: "初次咨询",
              visit_stage: "pre_visit",
              patient_display_name: "演示患者",
              participant_display_name: "患者本人",
              archived_at: null,
            }])}
          />
        </Suspense>,
      );
    });

    await waitFor(() => {
      expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ behavior: "auto" });
    });
  });
});
