import { DefaultChatTransport } from "ai";

import {
  backendBaseUrl,
  demoParticipantId,
  demoVisitMatterId,
} from "./chat-config";
import type { MediPetMessage, ProposalDecision } from "./message-types";

export const chatTransport = new DefaultChatTransport<MediPetMessage>({
  api: `${backendBaseUrl}/v1/chat/turns`,
  body: {
    visit_matter_id: demoVisitMatterId,
    participant_id: demoParticipantId,
  },
});

export async function decideProposal(proposalId: string, decision: ProposalDecision) {
  const response = await fetch(
    `${backendBaseUrl}/v1/action-proposals/${proposalId}/decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        participant_id: demoParticipantId,
        visit_matter_id: demoVisitMatterId,
        idempotency_key: `decision-${proposalId}-${decision}`,
      }),
    },
  );

  if (!response.ok) {
    throw new Error("操作方案处理失败，请稍后重试。");
  }

  const result = await response.json() as {
    events?: Array<{ kind?: string; data?: { message?: string } }>;
  };
  const failure = result.events?.find((event) => event.kind === "failed");
  if (failure) {
    throw new Error(failure.data?.message ?? "操作方案处理失败，请稍后重试。");
  }
  return result;
}
