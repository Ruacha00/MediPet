import { CalendarClock, Check, MapPin, ShieldAlert, Stethoscope } from "lucide-react";

import type {
  ActionProposalData,
  DepartmentCandidatesData,
  HandoffData,
  HospitalRouteData,
  MediPetMessagePart,
  ProposalDecision,
  ProposalDecisionState,
  SlotOptionsData,
} from "./message-types";

type MessagePartViewProps = {
  part: MediPetMessagePart;
  decisionState: ProposalDecisionState;
  onDecision: (proposalId: string, decision: ProposalDecision) => void;
};

export function MessagePartView({ part, decisionState, onDecision }: MessagePartViewProps) {
  switch (part.type) {
    case "text":
      return <div className="bubble">{part.text}</div>;
    case "data-department-candidates":
      return <DepartmentCandidatesCard data={part.data} />;
    case "data-slot-options":
      return <SlotOptionsCard data={part.data} />;
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

export function DepartmentCandidatesCard({ data }: { data: DepartmentCandidatesData }) {
  return (
    <section className="data-card" aria-label="候选科室">
      <p className="card-kicker">Department guidance</p>
      <h3><Stethoscope size={17} aria-hidden="true" /> 候选科室</h3>
      <p>信息不确定性：{data.uncertainty}。以下为就诊引导，不代表疾病诊断。</p>
      <ul className="candidate-list">
        {data.candidates.map((candidate) => (
          <li key={candidate.name}>
            <strong>{candidate.name}</strong>
            <span>{candidate.reason}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function SlotOptionsCard({ data }: { data: SlotOptionsData }) {
  const slot = data.slots[0];
  if (!slot) return null;

  return (
    <section className="data-card" aria-label="可选号源">
      <p className="card-kicker">Available slot</p>
      <h3><CalendarClock size={17} aria-hidden="true" /> {data.department}</h3>
      <div className="slot-line">
        <div><small>医生</small><strong>{slot.doctor}</strong></div>
        <div><small>时间</small><strong>{slot.date} · {slot.time}</strong></div>
        <div><small>挂号费</small><strong>¥{slot.fee}</strong></div>
        <div><small>状态</small><strong>可预约</strong></div>
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
  const resolved = decisionState === "confirm" || decisionState === "reject";
  const working = decisionState === "working";
  const operation = data.toolName ?? data.toolId ?? "受确认保护的操作";
  const version = data.toolVersion ? ` · v${data.toolVersion}` : "";
  const argumentsText = JSON.stringify(data.arguments ?? {}, null, 2);

  return (
    <section className="data-card" aria-label="操作确认">
      <p className="card-kicker">Explicit confirmation</p>
      <h3><Check size={17} aria-hidden="true" /> 请核对操作</h3>
      <p><strong>{operation}{version}</strong></p>
      <pre>{argumentsText}</pre>
      {data.expiresAt && <small>确认有效期至 {data.expiresAt}</small>}
      <div className="card-actions">
        <button
          className="card-button primary"
          disabled={working || resolved}
          onClick={() => onDecision(data.proposalId, "confirm")}
        >
          {decisionState === "confirm" ? "已确认" : working ? "处理中…" : "确认操作"}
        </button>
        <button
          className="card-button"
          disabled={working || resolved}
          onClick={() => onDecision(data.proposalId, "reject")}
        >
          {decisionState === "reject" ? "已拒绝" : "拒绝操作"}
        </button>
      </div>
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
