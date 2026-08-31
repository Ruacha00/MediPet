import { Suspense } from "react";

import { ChatShell } from "@/features/chat/chat-shell";
import { loadCapabilityStatus } from "@/features/chat/capabilities";
import {
  demoParticipantId,
  demoVisitMatterId,
} from "@/features/chat/chat-config";
import { serverBackendBaseUrl } from "@/features/chat/chat-config.server";
import { restoreConversationHistory } from "@/features/chat/history";
import { loadVisitMatters } from "@/features/chat/visit-matters";

export const dynamic = "force-dynamic";

export default function ChatPage() {
  const history = restoreConversationHistory(
    serverBackendBaseUrl,
    demoVisitMatterId,
    demoParticipantId,
  );
  const capabilityStatus = loadCapabilityStatus(serverBackendBaseUrl);
  const visitMatters = loadVisitMatters(serverBackendBaseUrl, demoParticipantId).catch(() => []);

  return (
    <Suspense fallback={<HistoryLoading />}>
      <ChatShell
        history={history}
        capabilityStatus={capabilityStatus}
        visitMatters={visitMatters}
      />
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
