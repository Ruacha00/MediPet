import { afterEach, describe, expect, it, vi } from "vitest";

import { decideProposal } from "./transport";

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

    await expect(decideProposal("proposal-1", "confirm")).rejects.toThrow(
      "待确认操作已过期",
    );
  });
});
