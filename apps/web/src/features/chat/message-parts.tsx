import { CalendarClock, Check, MapPin, ShieldAlert } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type {
  ActionProposalData,
  HandoffData,
  HospitalRouteData,
  MediPetMessagePart,
  ProposalDecision,
  ProposalDecisionState,
  SlotOption,
  SlotOptionsData,
} from "./message-types";

type MessagePartViewProps = {
  part: MediPetMessagePart;
  decisionState: ProposalDecisionState;
  onDecision: (proposalId: string, decision: ProposalDecision) => void;
  onSelectSlot: (slot: SlotOption) => void;
};

export function MessagePartView({
  part,
  decisionState,
  onDecision,
  onSelectSlot,
}: MessagePartViewProps) {
  switch (part.type) {
    case "text":
      return <MarkdownText text={part.text} />;
    case "data-slot-options":
      return <SlotOptionsCard data={part.data} onSelect={onSelectSlot} />;
    case "data-action-proposal":
      return (
        <ActionProposalCard
          data={part.data}
          decisionState={decisionState}
          onDecision={onDecision}
        />
      );
    case "data-hospital-route":
      return <HospitalRouteCard data={part.data} />;
    case "data-handoff":
      return <HandoffCard data={part.data} />;
    default:
      return null;
  }
}

export function MarkdownText({
  text,
  className = "bubble",
}: {
  text: string;
  className?: string;
}) {
  return (
    <div className={`${className} markdown-body`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export function SlotOptionsCard({
  data,
  onSelect,
}: {
  data: SlotOptionsData;
  onSelect: (slot: SlotOption) => void;
}) {
  if (data.slots.length === 0) return null;

  return (
    <section className="data-card" aria-label="可选号源">
      <p className="card-kicker">Available slots</p>
      <h3><CalendarClock size={17} aria-hidden="true" /> 可预约号源</h3>
      <div className="candidate-list">
        {data.slots.map((slot) => (
          <article className="slot-line" key={slot.id}>
            <div><small>科室</small><strong>{slot.department}</strong></div>
            <div><small>医生</small><strong>{slot.doctor} · {slot.doctorTitle}</strong></div>
            <div><small>时间</small><strong>{formatDateTime(slot.startsAt)}</strong></div>
            <div><small>挂号费</small><strong>{formatMoney(slot.feeCents)}</strong></div>
            <button className="card-button primary" onClick={() => onSelect(slot)}>
              选择此号源
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}

export function ActionProposalCard({
  data,
  decisionState,
  onDecision,
}: {
  data: ActionProposalData;
  decisionState: ProposalDecisionState;
  onDecision: (proposalId: string, decision: ProposalDecision) => void;
}) {
  const resolved = data.status !== "pending";
  const working = decisionState === "working";
  const operation = data.toolName ?? data.toolId ?? "受确认保护的操作";
  const version = data.toolVersion ? ` · v${data.toolVersion}` : "";
  const confirmation = data.confirmation;

  return (
    <section className="data-card" aria-label="预约确认">
      <p className="card-kicker">Explicit confirmation</p>
      <h3><Check size={17} aria-hidden="true" /> {proposalTitle(data.status, Boolean(confirmation))}</h3>
      <p><strong>{operation}{version}</strong></p>
      {confirmation ? (
        <div className="proposal-grid">
          <div><small>患者</small><strong>{confirmation.patient.display_name}</strong></div>
          <div><small>服务医院</small><strong>{confirmation.hospital.name}</strong></div>
          <div><small>科室</small><strong>{confirmation.department.name}</strong></div>
          <div><small>医生</small><strong>{confirmation.doctor.name} · {confirmation.doctor.title}</strong></div>
          <div><small>时间</small><strong>{formatDateTime(confirmation.starts_at)}</strong></div>
          <div><small>挂号费</small><strong>{formatMoney(confirmation.fee_cents)}</strong></div>
        </div>
      ) : (
        <pre>{JSON.stringify(data.arguments ?? {}, null, 2)}</pre>
      )}
      {data.status === "confirmed" && data.receiptId && (
        <p><small>收据编号</small><br /><strong>{data.receiptId}</strong></p>
      )}
      {data.status === "pending" && data.expiresAt && (
        <small>确认有效期至 {formatDateTime(data.expiresAt)}</small>
      )}
      {!resolved && (
        <div className="card-actions">
          <button
            className="card-button primary"
            disabled={working}
            onClick={() => onDecision(data.proposalId, "confirm")}
          >
            {working ? "处理中…" : confirmation ? "确认预约" : "确认操作"}
          </button>
          <button
            className="card-button"
            disabled={working}
            onClick={() => onDecision(data.proposalId, "reject")}
          >
            {confirmation ? "拒绝预约" : "拒绝操作"}
          </button>
        </div>
      )}
    </section>
  );
}

function HospitalRouteCard({ data }: { data: HospitalRouteData }) {
  return (
    <section className="data-card" aria-label="院内路线">
      <p className="card-kicker">Hospital route</p>
      <h3><MapPin size={17} aria-hidden="true" /> {data.destination}</h3>
      <ol className="route-list">
        {data.steps.map((step, index) => <li key={step}>{index + 1}. {step}</li>)}
      </ol>
    </section>
  );
}

function HandoffCard({ data }: { data: HandoffData }) {
  return (
    <section className={`data-card ${data.priority === "emergency" ? "urgent" : ""}`}>
      <p className="card-kicker">{data.priority === "emergency" ? "Emergency" : "Human handoff"}</p>
      <h3><ShieldAlert size={17} aria-hidden="true" /> {data.title}</h3>
      <p>{data.description}</p>
    </section>
  );
}

function proposalTitle(status: ActionProposalData["status"], appointment: boolean) {
  if (status === "confirmed") return "预约成功";
  if (status === "rejected") return "已拒绝预约";
  if (status === "expired") return "确认已过期";
  return appointment ? "请核对预约挂号" : "请核对操作";
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "long",
    day: "numeric",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatMoney(feeCents: number) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
  }).format(feeCents / 100);
}
