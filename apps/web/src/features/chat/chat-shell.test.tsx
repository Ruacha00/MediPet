import { Suspense } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { ChatShell } from "./chat-shell";

vi.mock("@ai-sdk/react", () => ({
  useChat: (options: { messages?: unknown[] }) => ({
    messages: options.messages ?? [],
    sendMessage: vi.fn(),
    status: "ready",
    stop: vi.fn(),
    setMessages: vi.fn(),
    error: undefined,
  }),
}));

describe("ChatShell", () => {
  beforeAll(() => {
    Element.prototype.scrollIntoView = vi.fn();
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

    fireEvent.click(screen.getByRole("button", { name: "新建就诊事项" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "http://localhost:8000/v1/visit-matters",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(
      await within(screen.getByRole("navigation", { name: "就诊事项列表" })).findByRole(
        "button",
        { name: /新的就诊事项/ },
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
              },
              {
                visit_matter_id: "visit-matter-follow-up",
                title: "复诊准备",
                visit_stage: "pre_visit",
                patient_display_name: "演示患者",
                participant_display_name: "患者本人",
              },
            ])}
          />
        </Suspense>,
      );
    });

    const followUp = await screen.findByRole("button", { name: /复诊准备/ });
    fireEvent.click(followUp);

    expect(await screen.findByText("这是复诊事项")).toBeVisible();
  });
});
