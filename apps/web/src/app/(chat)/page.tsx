import { Suspense } from "react";

import { ChatShell } from "@/features/chat/chat-shell";
import { loadCapabilityStatus } from "@/features/chat/capabilities";
import {
  demoParticipantId,
} from "@/features/chat/chat-config";
import { serverBackendBaseUrl } from "@/features/chat/chat-config.server";
import { restoreConversationHistory } from "@/features/chat/history";
import { loadVisitMatters } from "@/features/chat/visit-matters";

export const dynamic = "force-dynamic";

export default async function ChatPage() {
  const capabilityStatus = loadCapabilityStatus(serverBackendBaseUrl);
  const visitMatterResult = await loadVisitMatters(
    serverBackendBaseUrl,
    demoParticipantId,
  ).then(
    (visitMatters) => ({ visitMatters, failed: false }),
    () => ({ visitMatters: [], failed: true }),
  );
  const visitMatterList = visitMatterResult.visitMatters;
  const selectedVisitMatterId = visitMatterList[0]?.visit_matter_id;
  const history = selectedVisitMatterId
    ? restoreConversationHistory(
        serverBackendBaseUrl,
        selectedVisitMatterId,
        demoParticipantId,
      )
    : Promise.resolve({ messages: [], failed: false });

  return (
    <Suspense fallback={<HistoryLoading />}>
      <ChatShell
        history={history}
        capabilityStatus={capabilityStatus}
        visitMatters={Promise.resolve(visitMatterList)}
        visitMattersFailed={visitMatterResult.failed}
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
