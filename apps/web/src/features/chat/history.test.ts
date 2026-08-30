import { afterEach, describe, expect, it, vi } from "vitest";

import { loadConversationHistory, toMediPetMessages } from "./history";

describe("toMediPetMessages", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("restores completed and interrupted messages without marking partial text completed", () => {
    const messages = toMediPetMessages({
      visit_matter_id: "visit-matter-demo",
      messages: [
        {
          id: "participant-message",
          role: "user",
          state: "completed",
          parts: [{ type: "text", text: "请继续" }],
          created_at: "2026-08-30T08:00:00Z",
          updated_at: "2026-08-30T08:00:00Z",
        },
        {
          id: "cancelled-message",
          role: "assistant",
          state: "cancelled",
          parts: [{ type: "text", text: "半截回答" }],
          created_at: "2026-08-30T08:00:01Z",
          updated_at: "2026-08-30T08:00:02Z",
        },
      ],
    });

    expect(messages).toHaveLength(2);
    expect(messages[0].metadata?.state).toBe("completed");
    expect(messages[1].parts).toEqual([
      { type: "text", text: "半截回答", state: "done" },
    ]);
    expect(messages[1].metadata?.state).toBe("cancelled");
  });

  it("loads the selected visit matter and participant on refresh", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        visit_matter_id: "visit-matter-demo",
        messages: [],
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    await loadConversationHistory(
      "http://localhost:8000",
      "visit-matter-demo",
      "participant-demo",
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/v1/visit-matters/visit-matter-demo/messages?participant_id=participant-demo",
    );
  });
});
