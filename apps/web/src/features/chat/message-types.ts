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

export type SlotOption = {
  id: string;
  department: string;
  doctor: string;
  doctorTitle: string;
  startsAt: string;
  endsAt: string;
  feeCents: number;
  currency: "CNY";
};

export type SlotOptionsData = {
  slots: SlotOption[];
};

export type AppointmentConfirmation = {
  patient: { display_name: string };
  hospital: { name: string; timezone: string };
  department: { name: string };
  doctor: { name: string; title: string };
  slot_id: string;
  starts_at: string;
  ends_at: string;
  fee_cents: number;
  currency: "CNY";
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
  confirmation?: AppointmentConfirmation;
  action?: "create" | "cancel";
  patient?: string;
  department?: string;
  doctor?: string;
  date?: string;
  time?: string;
  fee?: number;
  status: "pending" | "confirmed" | "rejected" | "expired";
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
