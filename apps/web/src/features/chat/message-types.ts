import type { UIMessage } from "ai";

export type AgentStatusData = {
  label: string;
};

export type DepartmentCandidate = {
  name: string;
  reason: string;
};

export type DepartmentCandidatesData = {
  candidates: DepartmentCandidate[];
  uncertainty: string;
};

export type SlotOptionsData = {
  department: string;
  slots: Array<{
    id: string;
    doctor: string;
    date: string;
    time: string;
    fee: number;
  }>;
};

export type ActionProposalData = {
  proposalId: string;
  action?: "create" | "cancel";
  patient?: string;
  department?: string;
  doctor?: string;
  date?: string;
  time?: string;
  fee?: number;
  status: "pending" | "confirmed" | "rejected";
  receiptId?: string;
};

export type HospitalRouteData = {
  destination: string;
  steps: string[];
};

export type HandoffData = {
  priority: "human" | "emergency";
  title: string;
  description: string;
};

export type MediPetDataParts = {
  "agent-status": AgentStatusData;
  "department-candidates": DepartmentCandidatesData;
  "slot-options": SlotOptionsData;
  "action-proposal": ActionProposalData;
  "hospital-route": HospitalRouteData;
  handoff: HandoffData;
};

export type MediPetMessage = UIMessage<never, MediPetDataParts>;
export type MediPetMessagePart = MediPetMessage["parts"][number];
export type ProposalDecision = "confirm" | "reject";
export type ProposalDecisionState = ProposalDecision | "working" | null;
