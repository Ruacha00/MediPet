import { afterEach, describe, expect, it, vi } from "vitest";

import { createChatTransport, decideProposal } from "./transport";

describe("chatTransport", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the participant message id as the stable turn idempotency key", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("data: [DONE]\n\n"));
    vi.stubGlobal("fetch", fetchMock);
    const chatTransport = createChatTransport("visit-matter-2", "participant-demo");
    const messages = [
      {
        id: "participant-turn-1",
        role: "user" as const,
        metadata: { state: "completed" as const },
        parts: [{ type: "text" as const, text: "我选择第一个号源" }],
      },
    ];

    await chatTransport.sendMessages({
      trigger: "submit-message",
      chatId: "visit-chat",
      messageId: undefined,
      messages,
      abortSignal: undefined,
      body: { selected_slot_id: "slot-1" },
    });
    await chatTransport.sendMessages({
      trigger: "submit-message",
      chatId: "visit-chat",
      messageId: undefined,
      messages,
      abortSignal: undefined,
      body: { selected_slot_id: "slot-1" },
    });

    const bodies = fetchMock.mock.calls.map((call) =>
      JSON.parse(String(call[1]?.body)) as Record<string, unknown>
    );
    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toMatchObject({
      idempotency_key: "participant-turn-1",
      visit_matter_id: "visit-matter-2",
      selected_slot_id: "slot-1",
    });
    expect(bodies[1].idempotency_key).toBe(bodies[0].idempotency_key);
  });
});

describe("decideProposal", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("rejects an HTTP 200 response containing a safe failed decision event", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            proposal_id: "proposal-1",
            decision: "confirm",
            events: [{ kind: "failed", data: { message: "待确认操作已过期" } }],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(
      decideProposal("proposal-1", "confirm", "visit-matter-2", "participant-demo"),
    ).rejects.toThrow("待确认操作已过期");
  });

  it("returns the server-updated proposal state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            proposal_id: "proposal-1",
            decision: "confirm",
            events: [
              {
                kind: "data",
                data: {
                  type: "data-action-proposal",
                  data: {
                    proposalId: "proposal-1",
                    status: "confirmed",
                    receiptId: "receipt-1",
                  },
                },
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(
      decideProposal("proposal-1", "confirm", "visit-matter-2", "participant-demo"),
    ).resolves.toMatchObject({
        proposalId: "proposal-1",
        status: "confirmed",
        receiptId: "receipt-1",
      });
  });
});
