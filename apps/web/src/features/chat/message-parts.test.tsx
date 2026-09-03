import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ActionProposalCard,
  MessagePartView,
  SlotOptionsCard,
} from "./message-parts";

afterEach(cleanup);

describe("MessagePartView", () => {
  it("disables pending action decisions in read-only history", () => {
    render(
      <MessagePartView
        part={{
          type: "data-action-proposal",
          data: {
            proposalId: "proposal-read-only",
            status: "pending",
            arguments: {},
          },
        }}
        decisionState={null}
        onDecision={vi.fn()}
        onSelectSlot={vi.fn()}
        readOnly
      />,
    );

    expect(screen.getByRole("button", { name: "确认操作" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "拒绝操作" })).toBeDisabled();
  });

  it("disables slot selection in read-only history", () => {
    render(
      <MessagePartView
        part={{
          type: "data-slot-options",
          data: {
            slots: [{
              id: "slot-read-only",
              department: "儿科",
              doctor: "周安",
              doctorTitle: "主治医师",
              startsAt: "2030-01-02T14:00:00+08:00",
              endsAt: "2030-01-02T14:30:00+08:00",
              feeCents: 2000,
              currency: "CNY",
            }],
          },
        }}
        decisionState={null}
        onDecision={vi.fn()}
        onSelectSlot={vi.fn()}
        readOnly
      />,
    );

    expect(screen.getByRole("button", { name: "选择此号源" })).toBeDisabled();
  });

  it("renders GitHub-flavored Markdown as structured message content", () => {
    render(
      <MessagePartView
        part={{
          type: "text",
          text: [
            "## 就诊准备",
            "",
            "- 携带病历",
            "- 记录用药",
            "",
            "配置 `MEDIPET_API_URL`，再查看 [帮助](https://example.com/help)。",
          ].join("\n"),
        }}
        decisionState={null}
        onDecision={() => undefined}
        onSelectSlot={() => undefined}
      />,
    );

    expect(screen.getByRole("heading", { name: "就诊准备", level: 2 })).toBeVisible();
    expect(screen.getByRole("list")).toBeVisible();
    expect(screen.getByText("MEDIPET_API_URL", { selector: "code" })).toBeVisible();
    expect(screen.getByRole("link", { name: "帮助" })).toHaveAttribute(
      "href",
      "https://example.com/help",
    );
  });

  it("renders authoritative text wayfinding without exposing a map model", () => {
    render(
      <MessagePartView
        part={{
          type: "data-hospital-wayfinding",
          data: {
            origin: { id: "origin-main-entrance", name: "门诊楼一层主入口" },
            destination: { id: "location-pediatrics", name: "儿科门诊" },
            mode: "accessible",
            steps: [
              "沿右侧无障碍通道前行至电梯厅。",
              "乘电梯到二层后按儿科门诊指示牌左转。",
            ],
            notice: "如需协助，请询问大厅服务台。",
            dataVersion: "minghe-wayfinding-2026-09-01",
          },
        }}
        decisionState={null}
        onDecision={vi.fn()}
        onSelectSlot={vi.fn()}
      />,
    );

    const card = screen.getByRole("region", { name: "院内方位指引" });
    expect(within(card).getByText("门诊楼一层主入口")).toBeVisible();
    expect(within(card).getByText("儿科门诊")).toBeVisible();
    expect(within(card).getByText("无障碍指引")).toBeVisible();
    expect(within(card).getByRole("list")).toHaveAttribute("role", "list");
    expect(within(card).getAllByRole("listitem")).toHaveLength(2);
    expect(within(card).getByText(/如需协助，请询问大厅服务台。/)).toBeVisible();
    expect(within(card).queryByText("origin-main-entrance")).not.toBeInTheDocument();
    expect(within(card).queryByText("minghe-wayfinding-2026-09-01")).not.toBeInTheDocument();
  });

  it("renders repeated authoritative steps without duplicate React keys", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <MessagePartView
        part={{
          type: "data-hospital-wayfinding",
          data: {
            origin: { id: "origin-1", name: "门诊入口" },
            destination: { id: "location-1", name: "收费处" },
            mode: "standard",
            steps: ["继续直行。", "继续直行。"],
            notice: null,
            dataVersion: "v1",
          },
        }}
        decisionState={null}
        onDecision={vi.fn()}
        onSelectSlot={vi.fn()}
      />,
    );

    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(consoleError.mock.calls.flat().join(" ")).not.toContain("same key");
    consoleError.mockRestore();
  });

  it("renders a truthful application-owned unavailable result", () => {
    render(
      <MessagePartView
        part={{
          type: "data-hospital-wayfinding-unavailable",
          data: {
            reason: "accessible_unavailable",
            message: "该起点和目的地没有无障碍方位指引，请询问院内工作人员。",
          },
        }}
        decisionState={null}
        onDecision={vi.fn()}
        onSelectSlot={vi.fn()}
      />,
    );

    expect(screen.getByRole("region", { name: "院内方位指引不可用" })).toHaveTextContent(
      "该起点和目的地没有无障碍方位指引，请询问院内工作人员。",
    );
    expect(screen.getByRole("heading", { name: /暂无可用方位指引/ })).toBeInTheDocument();
  });

  it("asks for confirmation only when the structured selection is missing", () => {
    render(
      <MessagePartView
        part={{
          type: "data-hospital-wayfinding-unavailable",
          data: {
            reason: "selection_required",
            message: "请明确说明当前起点、目的地以及普通或无障碍模式。",
          },
        }}
        decisionState={null}
        onDecision={vi.fn()}
        onSelectSlot={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: /需要确认方位信息/ })).toBeInTheDocument();
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
    const slotCard = render(
      <SlotOptionsCard data={{ slots: [slot] }} onSelect={onSelect} />,
    );

    expect(within(slotCard.container).getByText("1月2日周三 14:00")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "选择此号源" }));
    expect(onSelect).toHaveBeenCalledWith(slot);
    expect(screen.queryByText("slot-1")).not.toBeInTheDocument();
  });
});
