import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ActionProposalCard,
  DepartmentCandidatesCard,
  SlotOptionsCard,
} from "./message-parts";

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

  it("renders authoritative appointment details and the confirmed receipt", () => {
    const confirmation = {
      patient: { display_name: "演示患者" },
      hospital: { name: "明和虚构医院", timezone: "Asia/Shanghai" },
      department: { name: "儿科" },
      doctor: { name: "周安", title: "主治医师" },
      slot_id: "slot-1",
      starts_at: "2030-01-02T14:00:00+08:00",
      ends_at: "2030-01-02T14:30:00+08:00",
      fee_cents: 2000,
      currency: "CNY" as const,
    };
    const { rerender } = render(
      <ActionProposalCard
        data={{ proposalId: "proposal-1", status: "pending", confirmation }}
        decisionState={null}
        onDecision={() => undefined}
      />,
    );

    expect(screen.getByText("演示患者")).toBeInTheDocument();
    expect(screen.getByText("明和虚构医院")).toBeInTheDocument();
    expect(screen.getByText("周安 · 主治医师")).toBeInTheDocument();
    expect(screen.getByText("¥20.00")).toBeInTheDocument();
    expect(screen.queryByText("slot-1")).not.toBeInTheDocument();

    rerender(
      <ActionProposalCard
        data={{
          proposalId: "proposal-1",
          status: "confirmed",
          confirmation,
          receiptId: "receipt-1",
        }}
        decisionState={null}
        onDecision={() => undefined}
      />,
    );
    expect(screen.getByRole("heading", { name: "预约成功" })).toBeInTheDocument();
    expect(screen.getByText("receipt-1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认预约" })).not.toBeInTheDocument();
  });

  it("renders the server-rejected state without decision controls", () => {
    const rejectedCard = render(
      <ActionProposalCard
        data={{ proposalId: "proposal-1", status: "rejected" }}
        decisionState={null}
        onDecision={() => undefined}
      />,
    );
    const card = within(rejectedCard.container);

    expect(card.getByRole("heading", { name: "已拒绝预约" })).toBeInTheDocument();
    expect(card.queryByRole("button", { name: "确认操作" })).not.toBeInTheDocument();
    expect(card.queryByRole("button", { name: "拒绝操作" })).not.toBeInTheDocument();
  });
});

describe("SlotOptionsCard", () => {
  it("keeps the trusted slot id behind a participant-friendly choice", () => {
    const onSelect = vi.fn();
    const slot = {
      id: "slot-1",
      department: "儿科",
      doctor: "周安",
      doctorTitle: "主治医师",
      startsAt: "2030-01-02T14:00:00+08:00",
      endsAt: "2030-01-02T14:30:00+08:00",
      feeCents: 2000,
      currency: "CNY" as const,
    };
    render(<SlotOptionsCard data={{ slots: [slot] }} onSelect={onSelect} />);

    fireEvent.click(screen.getByRole("button", { name: "选择此号源" }));
    expect(onSelect).toHaveBeenCalledWith(slot);
    expect(screen.queryByText("slot-1")).not.toBeInTheDocument();
  });
});
