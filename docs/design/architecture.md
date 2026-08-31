# MediPet Architecture

Status: Accepted

Accepted: 2026-08-29

## Purpose

MediPet is a full-stack conversational web application for one outpatient hospital. A Next.js chat client communicates with a Python backend, where a LangGraph-backed ReAct loop understands a visit participant's intent and invokes typed Skills for pre-visit and in-hospital assistance.

The architecture must preserve the language and rules in `CONTEXT.md`, especially:

- one visit matter belongs to exactly one patient;
- the product serves exactly one hospital;
- department information comes from the service hospital catalog, and the model does not match symptoms to departments;
- deterministic emergency interruption takes priority over the model and ordinary assistance;
- appointment creation and cancellation require authorization and explicit confirmation;
- payment and clinical-record access are outside V1.

## Design principles

- Keep the external Interface small and put orchestration complexity behind deep modules.
- Treat the language model as an untrusted planner, not an authority.
- Allow the model to request only registered, typed Skills.
- Separate read-only queries from effectful actions.
- Make effectful actions prepare a proposal first and commit only after confirmation.
- Inject external dependencies so production and test Adapters cross the same Seams.
- Test observable behaviour through module Interfaces rather than internal ReAct steps.

## Technology decisions

| Concern | Choice | Role |
| --- | --- | --- |
| Web application | Next.js App Router, React, TypeScript | Routes, responsive chat shell, browser state |
| Styling and UI primitives | Tailwind CSS, shadcn/ui, AI Elements | Accessible primitives and chat building blocks, customized for MediPet |
| Chat state | AI SDK UI `useChat` | Browser message state and streaming transport only |
| HTTP delivery | FastAPI and Pydantic | Typed JSON requests, ordinary JSON reads, and streaming responses |
| Agent orchestration | LangGraph behind `AgentRuntime` | ReAct execution, checkpoints, interrupts, and resume |
| Persistence | PostgreSQL, SQLAlchemy asyncio, Alembic | Visit matters, messages, proposals, receipts, authorization, and audit |
| Python tooling | uv, pytest, Ruff, Pyright | Dependencies, verification, formatting, linting, and type checking |
| Web tooling | pnpm, Vitest, Testing Library, Playwright | Dependencies, module tests, and browser scenarios |
| Windows local environment | Docker Desktop and Docker Compose | Containerized Web, backend, PostgreSQL, health checks, and startup order |

AI SDK UI does not run the Agent and is not a second orchestration framework. Next.js does not contain duplicate domain or Skill logic. LangGraph is an implementation choice hidden behind `AgentRuntime`, so the application-facing Interface remains stable.

V1 does not introduce Redis, a vector database, WebSockets, microservices, or Kubernetes. Add one only when a measured requirement creates a real Seam.

## System flow

```mermaid
flowchart LR
    Browser[Browser]
    Web[Next.js chat client]
    HTTP[FastAPI delivery Adapter]
    Assistant[MediPet Assistant]
    Agent[LangGraph Agent Runtime]
    Skills[Skill Runtime]
    Policy[Policy and confirmation]
    Hospital[Hospital Operations Adapter]
    Store[Visit Store Adapter]
    Model[Model Adapter]
    DB[(PostgreSQL)]
    Checkpoints[(LangGraph checkpoints)]

    Browser --> Web
    Web -->|AI SDK UI stream over HTTP/SSE| HTTP
    HTTP --> Assistant
    Assistant --> Agent
    Agent --> Model
    Agent --> Skills
    Agent --> Checkpoints
    Skills --> Policy
    Skills --> Hospital
    Assistant --> Store
    Skills --> Audit[Audit trail]
    Store --> DB
    Audit --> DB
```

The Assistant remains the application-facing deep module. HTTP and future delivery Adapters do not coordinate the Agent Runtime, Skill Runtime, persistence, confirmation, or recovery themselves.

## Web chat experience

### Interaction layout

The web application uses a familiar AI-chat composition while preserving the identity of a hospital visit assistant:

- a left rail lists visit matters, not generic chat sessions;
- the central thread streams text and structured assistance cards;
- a sticky composer supports send, stop, retry, and accessible keyboard operation;
- a collapsible right rail shows the current patient, visit stage, authorization state, and pending appointment;
- mobile layouts move both rails into drawers while keeping the thread and composer primary.

The thread renders these typed data parts:

| Data part | Presentation |
| --- | --- |
| `data-agent-status` | Transient progress such as checking hospital slots |
| `data-slot-options` | Selectable doctor, date, time, and fee options |
| `data-action-proposal` | Exact create/cancel proposal with confirm and reject controls |
| `data-hospital-route` | Ordered route steps and preparation notes |
| `data-handoff` | Human or emergency handoff with priority-specific treatment |

Internal Thought text is never streamed. The client may show understandable progress, Skill names, and completed outcomes, but never chain-of-thought.

### Visual direction

The interface follows a quiet clinical-editorial direction rather than a generic AI dashboard:

- warm ivory surfaces, deep teal structure, ink text, and vermilion reserved for urgent states;
- a Chinese serif display face paired with a highly legible Chinese sans-serif body face;
- generous reading width, restrained borders, and tactile paper-like depth instead of purple gradients;
- short card and message transitions that respect `prefers-reduced-motion`;
- WCAG 2.2 AA contrast, keyboard navigation, visible focus, and polite live-region announcements.

shadcn/ui and AI Elements are source primitives to customize, not a default visual theme to ship unchanged.

## Browser-to-backend Interface

The browser uses AI SDK UI's message-stream protocol over HTTP Server-Sent Events:

- `POST /v1/chat/turns` accepts a typed turn and streams text plus structured data parts;
- `POST /v1/action-proposals/{proposal_id}/decision` accepts an explicit confirm or reject decision;
- visit-matter and message-history reads use ordinary JSON responses;
- production exposes the web application and `/v1/` under one origin through routing infrastructure;
- development may use CORS, but it is not the production trust model.

The decision endpoint is mapped back to `MediPetAssistant.handle_turn` as a typed confirmation response. A button decision is never converted into an unstructured natural-language message.

FastAPI emits the stream directly. A Next.js route handler may route bytes only when hosting requires it; it must not interpret Agent state, authorize actions, or duplicate the stream protocol.

## Canonical code vocabulary

| Domain term | Python name | Notes |
| --- | --- | --- |
| 患者 | `Patient` | The person receiving care |
| 就诊参与者 | `VisitParticipant` | The person interacting with MediPet |
| 就诊事项 | `VisitMatter` | May span multiple conversations; never mixes patients |
| 门诊就诊 | `OutpatientVisit` | The planned or actual hospital visit |
| 服务医院 | `Hospital` | The one hospital served by this deployment |
| 症状陈述 | `SymptomStatement` | Unverified participant-provided information |
| 预约挂号 | `Appointment` | A patient holding a hospital slot |
| 预约确认 | `AppointmentConfirmation` | Explicit consent bound to exact action details |
| 紧急转介 | `EmergencyHandoff` | Overrides ordinary assistance |

Do not use `User`, `Task`, `Session`, or `MedicalCase` as substitutes for these domain concepts.

## Module Interfaces

### 1. MediPet Assistant

This is the only Interface used by an HTTP, CLI, or future chat-channel Adapter.

```python
class MediPetAssistant(Protocol):
    def handle_turn(
        self,
        command: TurnCommand,
    ) -> AsyncIterator[TurnEvent]: ...
```

`TurnCommand` contains:

- conversation identifier;
- visit-matter identifier;
- participant identifier;
- participant message;
- idempotency key;
- an optional confirmation response.

The `TurnEvent` stream may contain:

- safe participant-facing text deltas;
- typed structured data parts;
- an optional pending action proposal;
- handoff information when human or emergency help is required;
- transient progress without internal Thought text;
- exactly one completed or failed terminal event with a trace identifier.

Interface invariants:

- the referenced visit matter already determines the patient;
- a participant cannot silently switch patients inside a visit matter;
- repeated idempotency keys produce the same observable result;
- raw model output and internal reasoning are never returned;
- a pending effectful action ends the current ReAct run.
- a success event is emitted only after required state and audit records are persisted.

The implementation loads the visit matter, invokes the Agent Runtime, maps approved internal events into `TurnEvent` values, persists resulting state and audit records, then closes the stream with one terminal event.

### 2. Agent Runtime

```python
class AgentRuntime(Protocol):
    def run(
        self,
        request: AgentRequest,
    ) -> AsyncIterator[AgentEvent]: ...
```

The runtime hides:

- construction of model context;
- the Thought/Action/Observation loop;
- Skill discovery and schema presentation;
- step, token, and time budgets;
- malformed action recovery;
- loop detection;
- trace collection;
- conversion of internal failures into safe outcomes.

The implementation uses LangGraph for the ReAct state graph, checkpointing, streaming, interrupts, and resume. LangGraph types do not cross this Interface, so callers and tests do not depend on graph nodes, checkpoint schemas, or framework message types.

The Assistant applies deterministic emergency interruption before entering the graph. The initial graph contains reasoning, read-Skill execution, action-proposal pause, confirmed write-Skill execution, human handoff, and finish paths. Only explicitly mapped `AgentEvent` values may leave the runtime.

The model crosses a true external Seam:

```python
class ModelPort(Protocol):
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]: ...
```

Production uses a provider Adapter. Tests use a deterministic fake Adapter. Provider response
objects and reasoning metadata remain inside the Adapter; only mapped model chunks cross this Seam.

### 3. Skill Runtime

```python
class SkillRuntime(Protocol):
    async def execute(
        self,
        call: SkillCall,
        context: SkillContext,
    ) -> SkillOutcome: ...
```

This is a deep module. It owns:

- registry lookup and allowlisting;
- typed argument validation;
- participant and visit-matter scoping;
- read versus write effect classification;
- authorization checks;
- emergency precedence;
- action proposal creation;
- confirmation validation;
- idempotent commit;
- timeout and failure normalization;
- audit events.

Individual Skills satisfy one small internal Interface:

```python
class Skill(Protocol):
    manifest: SkillManifest

    async def invoke(
        self,
        request: SkillRequest,
        dependencies: SkillDependencies,
    ) -> SkillResult: ...
```

Skills do not call model providers, persistence, or hospital systems directly. They receive only the dependencies allowed by the Skill Runtime.

Initial Skills:

| Skill | Effect | Responsibility |
| --- | --- | --- |
| `list_departments` | Read | Return department names and descriptions from the service hospital catalog |
| `search_slots` | Read | Query available slots inside the hospital |
| `prepare_appointment` | Read | Build an exact appointment proposal without reserving a slot |
| `commit_appointment` | Write | Create an authorized, confirmed appointment idempotently |
| `prepare_cancellation` | Read | Build an exact cancellation proposal |
| `commit_cancellation` | Write | Cancel an authorized, confirmed appointment idempotently |
| `get_hospital_route` | Read | Return an in-hospital route and relevant preparation |
| `handoff_to_human` | Write | Create a handoff record for hospital staff |

The model cannot invent a Skill name or bypass the prepare/confirm/commit sequence.

### 4. Hospital Operations

Hospital integration crosses a Seam because production and test behaviour must vary without changing Skills.

```python
class HospitalOperations(Protocol):
    async def query(self, query: HospitalQuery) -> HospitalQueryResult: ...
    async def commit(self, action: ConfirmedHospitalAction) -> ActionReceipt: ...
```

The production Adapter translates between MediPet's domain types and the hospital's systems. The in-memory Adapter supplies deterministic departments, doctors, slots, routes, appointments, and failures for tests and the POC.

There is no hospital selector, tenant identifier, cross-hospital query, or tenant-aware configuration in this Interface.

### 5. Visit Store

```python
class VisitStore(Protocol):
    async def load(self, visit_matter_id: VisitMatterId) -> VisitMatter: ...
    async def save(
        self,
        visit_matter: VisitMatter,
        expected_version: int,
    ) -> None: ...
```

Optimistic versioning prevents concurrent turns from overwriting each other. Production and in-memory Adapters must pass the same contract tests.

Conversation history is subordinate to a visit matter. It is not the source of patient identity or authorization.

The production Adapter uses PostgreSQL through SQLAlchemy's asyncio Interface, with Alembic migrations. PostgreSQL is the source of truth for visit matters, messages, authorization, action proposals, receipts, and audit records.

LangGraph checkpoints store resumable execution state. They do not replace the Visit Store or become the source of truth for patient identity, authorization, or committed hospital actions.

## Effectful-action protocol

An appointment mutation follows a two-phase protocol:

1. The Agent requests a prepare Skill.
2. The Skill Runtime validates scope and returns a complete action proposal.
3. The Assistant presents the proposal and stops the ReAct run.
4. The participant explicitly confirms the proposal.
5. The Skill Runtime verifies authorization, proposal identity, expiry, and idempotency.
6. A commit Skill calls Hospital Operations.
7. The resulting receipt and audit event are persisted before success is reported.

A confirmation is bound to the exact patient, hospital operation, parameters, expiry, and idempotency key. Any material change invalidates it.

## Safety precedence

Before model or Skill execution, the Assistant checks only the current participant message for a finite set of explicit emergency signals drawn from official 120 public guidance. A match persists and returns an `EmergencyHandoff`, stops the turn, and directs the participant to call 120, seek the nearest emergency department, or ask someone nearby for help. The check does not produce a diagnosis or risk score and does not create a review queue, notification, or resumable workflow.

Explicit negation and clearly educational or hypothetical questions do not trigger the interruption. Messages without a matching signal continue through the ordinary Agent flow; Skills and the model cannot suppress a handoff once the Assistant has produced it.

This emergency boundary is independent from advising a participant to contact hospital staff when they cannot choose a department. That advice is a fallback, not an automated department-guidance workflow. The boundary removes only the former emergency risk-review queue, notification, and resume workflow.

## Deployment shape

MediPet has two deployable applications and one required datastore:

- a Next.js web application;
- a FastAPI Python backend implemented as a modular monolith;
- PostgreSQL.

Production routing presents the web application and `/v1/` under one origin. The backend alone owns Agent execution and hospital credentials. The canonical Windows development flow is defined by ADR 0006: root Docker Compose runs Web, backend, and PostgreSQL as separate services, while source bind mounts preserve development file watching. Browser requests use the published API address and Next.js server rendering uses the internal Compose service address. This development choice does not define the production deployment topology.

The web and backend may scale independently later, but V1 does not split the Python backend into networked modules.

Skill packages, declarative Tool contracts, and development capability data are deployment inputs,
not Python package contents. They live under the repository-level `capabilities/` directory and are
mounted read-only into the API container through `MEDIPET_CAPABILITIES_PATH`. Trusted Tool executors
remain in application code and accept only manifest entries whose IDs match deployed executors.

An empty development Registry provisions these repository capabilities as enabled and published
defaults. Later starts only reconcile definitions: they preserve Tool enablement and Skill lifecycle or
active-version decisions, leaving new Tool versions disabled and changed Skill versions as drafts until
an administrator explicitly governs them.

## Repository layout

```text
capabilities/
├── skills/
│   ├── hospital-appointment-assistance/
│   ├── hospital-appointment-cancellation/
│   └── hospital-service-catalog/
│       └── each package contains SKILL.md and medipet.json
└── tools/
    ├── hospital.json
    └── fake-hospital.json
apps/
├── web/
│   ├── Dockerfile
│   ├── package.json
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx
│   │   │   └── (chat)/
│   │   │       └── page.tsx
│   │   ├── features/
│   │   │   └── chat/
│   │   │       ├── chat-shell.tsx
│   │   │       ├── transport.ts
│   │   │       ├── message-types.ts
│   │   │       └── message-parts/
│   │   │           ├── slot-options.tsx
│   │   │           ├── action-proposal.tsx
│   │   │           ├── hospital-route.tsx
│   │   │           └── handoff.tsx
│   │   ├── components/
│   │   │   └── ui/
│   │   └── styles/
│   │       └── globals.css
│   └── tests/
│       ├── module/
│       └── browser/
└── api/
    ├── Dockerfile
    ├── pyproject.toml
    ├── migrations/
    ├── src/
    │   └── medipet/
    │       ├── assistant.py
    │       ├── contracts.py
    │       ├── domain/
    │       │   ├── models.py
    │       │   └── events.py
    │       ├── agent/
    │       │   ├── runtime.py
    │       │   ├── graph.py
    │       │   ├── state.py
    │       │   └── prompts.py
    │       ├── skills/
    │       │   ├── contracts.py
    │       │   ├── runtime.py
    │       │   ├── appointment.py
    │       │   ├── navigation.py
    │       │   └── handoff.py
    │       ├── hospital/
    │       │   ├── operations.py
    │       │   └── adapters/
    │       │       ├── in_memory.py
    │       │       └── production.py
    │       ├── persistence/
    │       │   ├── visit_store.py
    │       │   └── adapters/
    │       │       ├── in_memory.py
    │       │       └── postgres.py
    │       ├── model/
    │       │   ├── port.py
    │       │   └── adapters/
    │       │       ├── fake.py
    │       │       └── production.py
    │       └── delivery/
    │           ├── http.py
    │           ├── routes/
    │           └── streaming.py
    └── tests/
        ├── scenarios/
        ├── modules/
        └── adapters/
compose.yaml
start.bat
```

The repository remains one domain context even though it contains web and backend applications. The layout follows ownership of behaviour and does not create separate wrapper modules for every class.

## Testing strategy

Tests cross the same Interfaces as callers:

- Assistant scenario tests use a fake Model Adapter, in-memory Hospital Operations Adapter, and in-memory Visit Store Adapter.
- Assistant scenario tests assert emergency interruption precedence; Skill Runtime tests assert authorization, confirmation, schema validation, and idempotency.
- Hospital and Visit Store contract tests run against every Adapter.
- ReAct tests assert observable outcomes, not private Thought text or exact prompt formatting.
- FastAPI stream tests validate event ordering, terminal events, disconnect handling, and AI SDK UI protocol compatibility.
- Vitest and Testing Library tests exercise chat state and every structured message part.
- Playwright scenarios drive the real web application against the in-memory Hospital Operations Adapter.
- A generated or validated schema contract prevents Python event types and TypeScript data parts from drifting.
- Regression tests cover cross-patient isolation, repeated confirmations, expired proposals, stale visit versions, hospital failures, and loop exhaustion.

The first end-to-end scenario is:

1. A participant asks which departments the service hospital provides.
2. MediPet returns department names and descriptions from the hospital catalog without matching symptoms to a department.
3. The participant selects a known department and asks for an available slot.
4. MediPet prepares an appointment proposal.
5. No appointment exists before explicit confirmation.
6. Confirmation commits exactly one appointment.
7. Repeating the same confirmation does not create a duplicate.
8. Reloading the page restores the same visit matter and committed receipt.
9. The browser never receives internal Thought text.

## Deferred decisions

The following choices do not affect the module Interfaces and remain intentionally open:

- model provider and model SDK;
- production hospital transport and authentication;
- hospital identity provider;
- production hosting platform and reverse proxy;
- data-retention periods and archival policy.

These choices should be made only when the next implementation slice requires them.

## Official references

- [Next.js App Router](https://nextjs.org/docs/app)
- [AI SDK UI transport](https://ai-sdk.dev/docs/ai-sdk-ui/transport)
- [AI SDK UI stream protocol](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol)
- [FastAPI streaming responses](https://fastapi.tiangolo.com/advanced/custom-response/)
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)
- [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [uv projects](https://docs.astral.sh/uv/guides/projects/)
