import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ActionProposalCard, DepartmentCandidatesCard } from "./message-parts";

describe("DepartmentCandidatesCard", () => {
  it("labels guidance as non-diagnostic", () => {
    render(
      <DepartmentCandidatesCard
        data={{
          uncertainty: "中",
          candidates: [{ name: "全科医学科", reason: "信息不足时先行评估" }],
        }}
      />,
    );

    expect(screen.getByText("全科医学科")).toBeInTheDocument();
    expect(screen.getByText(/不代表疾病诊断/)).toBeInTheDocument();
  });
});

describe("ActionProposalCard", () => {
  it("renders the exact generic Tool operation instead of appointment placeholders", () => {
    render(
      <ActionProposalCard
        data={{
          proposalId: "proposal-1",
          toolName: "send_follow_up",
          toolVersion: "3",
          arguments: { channel: "sms", template: "visit-reminder" },
          expiresAt: "2030-01-01T00:00:00Z",
          status: "pending",
        }}
        decisionState={null}
        onDecision={() => undefined}
      />,
    );

    expect(screen.getByText("send_follow_up · v3")).toBeInTheDocument();
    expect(screen.getByText(/"channel": "sms"/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认操作" })).toBeInTheDocument();
    expect(screen.queryByText("确认挂号")).not.toBeInTheDocument();
  });
});
