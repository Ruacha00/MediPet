import type {
  ConversationMessageState,
  MediPetMessage,
} from "./message-types";

type HistoryTextPart = {
  type: "text";
  text: string;
};

type HistoryMessage = {
  id: string;
  role: "user" | "assistant";
  state: ConversationMessageState;
  parts: HistoryTextPart[];
  created_at: string;
  updated_at: string;
};

export type ConversationHistory = {
  visit_matter_id: string;
  messages: HistoryMessage[];
};

export function toMediPetMessages(history: ConversationHistory): MediPetMessage[] {
  return history.messages.map((message) => ({
    id: message.id,
    role: message.role,
    metadata: { state: message.state },
    parts: message.parts.map((part) => ({
      ...part,
      state:
        message.state === "pending" || message.state === "streaming"
          ? ("streaming" as const)
          : ("done" as const),
    })),
  }));
}

export async function loadConversationHistory(
  backendBaseUrl: string,
  visitMatterId: string,
  participantId: string,
): Promise<MediPetMessage[]> {
  const query = new URLSearchParams({ participant_id: participantId });
  const response = await fetch(
    `${backendBaseUrl}/v1/visit-matters/${visitMatterId}/messages?${query}`,
  );
  if (!response.ok) {
    throw new Error("历史对话恢复失败");
  }
  return toMediPetMessages((await response.json()) as ConversationHistory);
}
