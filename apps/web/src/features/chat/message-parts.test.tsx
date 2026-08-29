import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DepartmentCandidatesCard } from "./message-parts";

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
