import type { UIMessage } from "ai";

export type AgentStatusData = {
  label: string;
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

export type HospitalWayfindingData = {
  origin: { id: string; name: string };
  destination: { id: string; name: string };
  mode: "standard" | "accessible";
  steps: string[];
  notice: string | null;
  dataVersion: string;
};

export type HospitalWayfindingUnavailableData = {
  reason:
    | "selection_required"
    | "origin_not_found"
    | "destination_not_found"
    | "accessible_unavailable"
    | "unavailable";
  message: string;
};

export type HandoffData = {
  priority: "human" | "emergency";
  title: string;
  description: string;
};

export type MediPetDataParts = {
  "agent-status": AgentStatusData;
  "slot-options": SlotOptionsData;
  "action-proposal": ActionProposalData;
  "hospital-wayfinding": HospitalWayfindingData;
  "hospital-wayfinding-unavailable": HospitalWayfindingUnavailableData;
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
