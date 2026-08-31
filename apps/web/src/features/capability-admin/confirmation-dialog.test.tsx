import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useState } from "react";

import { ConfirmationDialog } from "./confirmation-dialog";

function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <><button type="button" onClick={() => setOpen(true)}>打开确认</button>{open ? <ConfirmationDialog confirmation={{ title: "确认发布 Skill", message: "当前：审核中；目标：已发布。", confirmLabel: "确认发布" }} busy={false} onCancel={() => setOpen(false)} onConfirm={() => setOpen(false)} /> : null}</>
  );
}

describe("ConfirmationDialog", () => {
  afterEach(cleanup);

  it("closes with Escape and restores focus to the trigger", () => {
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "打开确认" });
    trigger.focus();
    fireEvent.click(trigger);
    expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
