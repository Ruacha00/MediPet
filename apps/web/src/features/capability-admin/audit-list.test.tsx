import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AuditList } from "./audit-list";
import type { CapabilitySnapshot } from "./types";

const snapshot: CapabilitySnapshot = {
  skills: [], tools: [], bindings: [],
  skillAudits: [
    { action: "publish", skill_id: "skill-1", version: 2, actor: "development-admin", created_at: "2030-01-03T00:00:00Z", visit_matter_id: null, turn_id: null },
    { action: "runtime_select", skill_id: "skill-1", version: 2, actor: "runtime", created_at: "2030-01-04T00:00:00Z", visit_matter_id: "visit-123456789", turn_id: "turn-987654321" },
  ],
  toolAudits: [
    { action: "bind", tool_id: "hospital.list_slots", version: "1", actor: "development-admin", created_at: "2030-01-02T00:00:00Z", visit_matter_id: null, turn_id: null },
    { action: "invoke", tool_id: "hospital.list_slots", version: "1", actor: "runtime", created_at: "2030-01-05T00:00:00Z", visit_matter_id: "visit-secret-payload", turn_id: null },
  ],
};

describe("AuditList", () => {
  afterEach(cleanup);

  it("merges governance events and reveals de-identified runtime events on demand", () => {
    render(<AuditList snapshot={snapshot} />);
    let ledger = screen.getByRole("region", { name: "治理审计时间线" });
    expect(within(ledger).getByText("发布")).toBeInTheDocument();
    expect(within(ledger).getByText("绑定")).toBeInTheDocument();
    expect(within(ledger).queryByText("Tool 调用")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: "包含运行事件" }));
    ledger = screen.getByRole("region", { name: "治理审计时间线" });
    expect(within(ledger).getByText("Tool 调用")).toBeInTheDocument();
    expect(within(ledger).getByText("Skill 选择")).toBeInTheDocument();
    expect(screen.queryByText("visit-secret-payload")).not.toBeInTheDocument();
    expect(screen.getByText(/visit…load/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("对象类型"), { target: { value: "skill" } });
    expect(within(screen.getByRole("region", { name: "治理审计时间线" })).queryByText("Tool 调用")).not.toBeInTheDocument();
  });
});
