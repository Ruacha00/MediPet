import { Suspense } from "react";
import { act, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { ChatShell } from "./chat-shell";

vi.mock("@ai-sdk/react", () => ({
  useChat: () => ({
    messages: [],
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
});
