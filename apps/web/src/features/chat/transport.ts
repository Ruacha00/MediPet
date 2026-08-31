import { DefaultChatTransport } from "ai";

import { backendBaseUrl } from "./chat-config";
import type {
  ActionProposalData,
  MediPetMessage,
  ProposalDecision,
} from "./message-types";

export function createChatTransport(visitMatterId: string, participantId: string) {
  return new DefaultChatTransport<MediPetMessage>({
    api: `${backendBaseUrl}/v1/chat/turns`,
    body: {
      visit_matter_id: visitMatterId,
      participant_id: participantId,
    },
    prepareSendMessagesRequest({ id, messages, body, trigger, messageId }) {
      const participantMessage = [...messages]
        .reverse()
        .find((message) => message.role === "user");
      return {
        body: {
          ...body,
          id,
          messages,
          trigger,
          messageId,
          idempotency_key: participantMessage?.id,
        },
      };
    },
  });
}

export async function decideProposal(
  proposalId: string,
  decision: ProposalDecision,
  visitMatterId: string,
  participantId: string,
) {
  const response = await fetch(
    `${backendBaseUrl}/v1/action-proposals/${proposalId}/decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        participant_id: participantId,
        visit_matter_id: visitMatterId,
        idempotency_key: `decision-${proposalId}-${decision}`,
      }),
    },
  );

  if (!response.ok) {
    throw new Error("操作方案处理失败，请稍后重试。");
  }

  const result = await response.json() as {
    events?: Array<{
      kind?: string;
      data?: {
        message?: string;
        type?: string;
        data?: ActionProposalData;
      };
    }>;
  };
  const failure = result.events?.find((event) => event.kind === "failed");
  if (failure) {
    throw new Error(failure.data?.message ?? "操作方案处理失败，请稍后重试。");
  }
  return result.events?.find(
    (event) => event.kind === "data" && event.data?.type === "data-action-proposal",
  )?.data?.data ?? null;
}
