import { Suspense } from "react";

import { ChatShell } from "@/features/chat/chat-shell";
import { loadCapabilityStatus } from "@/features/chat/capabilities";
import {
  backendBaseUrl,
  demoParticipantId,
  demoVisitMatterId,
} from "@/features/chat/chat-config";
import { restoreConversationHistory } from "@/features/chat/history";

export const dynamic = "force-dynamic";

export default function ChatPage() {
  const history = restoreConversationHistory(
    backendBaseUrl,
    demoVisitMatterId,
    demoParticipantId,
  );
  const capabilityStatus = loadCapabilityStatus(backendBaseUrl);

  return (
    <Suspense fallback={<HistoryLoading />}>
      <ChatShell history={history} capabilityStatus={capabilityStatus} />
    </Suspense>
  );
}

function HistoryLoading() {
  return (
    <main className="app-shell" aria-busy="true">
      <section className="chat-panel panel" aria-label="MediPet 对话">
        <div className="thread">
          <p className="status-line">正在恢复历史对话…</p>
        </div>
      </section>
    </main>
  );
}
