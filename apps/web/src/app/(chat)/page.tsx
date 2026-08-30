import { Suspense } from "react";

import { ChatShell } from "@/features/chat/chat-shell";
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

  return (
    <Suspense fallback={<HistoryLoading />}>
      <ChatShell history={history} />
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
