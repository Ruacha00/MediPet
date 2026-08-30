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
  visitMatterId?: string;
  participantId?: string;
  toolId?: string;
  toolName?: string;
  toolVersion?: string;
  arguments?: Record<string, unknown>;
  profileVersion?: string;
  visitStage?: string;
  idempotencyKey?: string;
  expiresAt?: string;
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

export type ConversationMessageState =
  | "pending"
  | "streaming"
  | "completed"
  | "failed"
  | "cancelled";

export type MediPetMessageMetadata = {
  state: ConversationMessageState;
};

export type MediPetMessage = UIMessage<MediPetMessageMetadata, MediPetDataParts>;
export type MediPetMessagePart = MediPetMessage["parts"][number];
export type ProposalDecision = "confirm" | "reject";
export type ProposalDecisionState = ProposalDecision | "working" | null;
