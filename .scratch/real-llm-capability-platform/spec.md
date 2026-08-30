# 真实 LLM Runtime 与空能力管理平台

Type: spec
Status: ready-for-agent

## Problem Statement

MediPet 当前虽然使用 LangGraph，但实际只是单节点关键词路由器：回复、科室、医生、号源、路线、预约方案和收据均来自硬编码演示数据。它没有调用真实模型，没有 Action/Observation 循环，没有跨进程消息持久化，也没有可以安全管理、发布和审计 Agent 能力的 Skill/Tool 平台。

项目需要先建立一个可运行的真实模型基础：开发者可以通过后端环境变量连接任意符合 OpenAI Chat Completions 与 tool calling 契约的服务，就诊参与者可以获得流式、多轮且受门诊协助边界约束的对话。同时，系统必须建立完整的 ReAct-capable Runtime 和 Skill/Tool 管理配套，但初始不部署任何业务 Skill、业务 Tool 或医院数据。后续接入 fake 或真实医院外部数据时，应只需注册 Tools、发布 Skills，而不必重写聊天、运行时、确认、版本或管理边界。

## Solution

MediPet 将保留现有 Web、FastAPI、MediPetAssistant 和 AI SDK UI 消息流边界，在 Python 后端引入 OpenAI-compatible 模型 Adapter、PostgreSQL 持久化、完整 LangGraph ReAct 循环、通用 Tool Runtime，以及版本化 Skill/Tool 管理 API。

第一次交付时，生产 Registry 合法地保持零业务 Skill 和零业务 Tool。每个 turn 仍会经过完整 Runtime，固定配置与能力版本快照、加载就诊事项历史、调用真实模型、应用预算和持久化终态；由于模型没有获得任何业务 Tool Schema，运行自然结束为纯文本对话。测试环境通过临时 Skill 和 dummy read/write Tools 覆盖实际 Action/Observation、越权拒绝、proposal、确认和幂等流程，但这些测试能力不会进入开发 seed 或生产数据。

开发环境支持对允许字段进行 .env 热加载：每个新 turn 检测配置变化、验证并固定不可变快照，正在执行的 turn 不受影响。数据库连接、运行环境、管理认证和外部适配器类型仍要求重启。

Skill 平台采用 Agent Skills 兼容的声明式包；Tool 由受信任代码注册并由数据库治理。管理 API 支持导入、导出、版本、审核、发布、停用、Tool 启停、绑定和审计。包含可执行脚本的 Skill 包进入隔离状态，不会在 API 进程执行。

## User Stories

1. As a MediPet developer, I want to configure a custom OpenAI-compatible base URL, API token, and model through environment variables, so that I can test different model providers without changing application code.
2. As a MediPet developer, I want the configured base URL to represent the complete API root including the version path, so that URL construction is deterministic.
3. As a MediPet developer, I want to change supported LLM settings in the development .env file without restarting the API, so that model experiments are fast.
4. As a MediPet developer, I want process environment variables to take precedence over .env values, so that deployment-injected configuration cannot be accidentally overridden.
5. As a MediPet developer, I want invalid hot-reloaded configuration to make new turns and readiness fail clearly, so that configuration mistakes are immediately visible.
6. As a MediPet developer, I want an in-flight turn to keep its original configuration snapshot, so that a file edit cannot change behavior halfway through a response.
7. As an operator, I want liveness to remain available when the model is unconfigured, so that I can distinguish a running process from a ready conversational service.
8. As an operator, I want readiness and chat requests to report model misconfiguration without exposing secrets or upstream response bodies, so that failures are diagnosable and safe.
9. As a visit participant, I want assistant text to stream incrementally, so that long model responses do not appear frozen.
10. As a visit participant, I want a stopped generation to remain visibly cancelled rather than completed, so that partial content is not mistaken for a finished answer.
11. As a visit participant, I want transient upstream failures before the first token to be retried once, so that a brief connection issue does not always fail the turn.
12. As a visit participant, I want failures after streaming begins to stop cleanly without duplicating text, so that automatic retry does not corrupt the thread.
13. As a visit participant, I want my completed conversation history to survive an API restart, so that the same就诊事项 can continue later.
14. As a visit participant, I want only messages from the same就诊事项 to enter model context, so that another patient or unrelated need cannot leak into my conversation.
15. As a visit participant, I want failed, cancelled, and management events excluded from model context, so that incomplete system output does not influence later answers.
16. As a visit participant, I want MediPet to stay within门诊就诊协助, so that the model does not present itself as a diagnosing or prescribing clinician.
17. As a visit participant, I want MediPet to state that hospital data is not configured, so that general model knowledge is not presented as information from the服务医院.
18. As a visit participant, I want MediPet not to invent departments, doctors, slots, fees, appointments, or routes, so that development-stage responses cannot be confused with hospital facts.
19. As a developer, I want a complete ReAct-capable graph even when no business Tools are available, so that future Tools can be activated without replacing the runtime architecture.
20. As a developer, I want the zero-Tool runtime to degrade naturally to model-only conversation, so that the initial application remains useful for connectivity testing.
21. As a developer, I want dummy read Tools to be available only inside automated tests, so that the Action/Observation loop is proven without seeding fake production capability.
22. As a developer, I want dummy write Tools to prove proposal, confirmation, commit, expiry, and idempotency behavior, so that effectful infrastructure is ready before hospital integration.
23. As an operator, I want every turn to have step, model-call, and wall-clock budgets, so that a model cannot loop or consume resources without bounds.
24. As an operator, I want unknown and unauthorized Tool calls rejected by the runtime, so that a non-conforming provider cannot bypass the Registry.
25. As an operator, I want repeated invalid Tool calls to trigger loop detection, so that correction attempts remain bounded.
26. As a system administrator, I want the Skill Registry to be valid when empty, so that infrastructure can be deployed before hospital capabilities are designed.
27. As a system administrator, I want to create and edit Skill drafts, so that instructions can be prepared without affecting active conversations.
28. As a system administrator, I want to import and export Agent Skills-compatible packages, so that Skills can move between development and hospital environments.
29. As a system administrator, I want instruction-only Skills to be publishable, so that useful guidance does not require an artificial Tool binding.
30. As a system administrator, I want tool-assisted Skills with missing or disabled bindings to be rejected at publication, so that published instructions cannot promise unavailable actions.
31. As a system administrator, I want published Skill versions to be immutable, so that past Agent behavior remains reproducible.
32. As a system administrator, I want modifications to create a new draft version, so that review and rollback remain explicit.
33. As a system administrator, I want Skills to move through draft, in-review, published, and retired states, so that changes have a controlled lifecycle.
34. As a system administrator, I want a running turn to remain pinned to the Skill versions selected at its start, so that publication cannot change behavior mid-run.
35. As a system administrator, I want packages containing scripts or executable files quarantined, so that I can inspect them without executing uploaded code.
36. As a system administrator, I want to view Tool names, versions, Schemas, effects, and approval requirements, so that I understand the capabilities available for binding.
37. As a system administrator, I want to enable, disable, and bind trusted Tools without editing their implementation, so that governance is separate from deployed code.
38. As a system administrator, I want Tool implementation and Schema changes to create explicit versions rather than overwrite metadata silently, so that active Skills do not drift.
39. As a system administrator, I want management APIs protected by a backend Bearer Token during personal development, so that the capability platform is not anonymously writable.
40. As a system administrator, I want production management APIs disabled until hospital identity integration exists, so that a development token is not mistaken for production authorization.
41. As a maintainer, I want every publication, binding, Tool rejection, proposal decision, and terminal run outcome audited, so that behavior can be reconstructed.
42. As a maintainer, I want logs and metrics to omit participant text, full prompts, API tokens, and raw upstream errors, so that observability does not become a data leak.
43. As a maintainer, I want latency and usage metrics tagged by provider, model, and runtime configuration version, so that different models are not compared as if they were identical.
44. As a maintainer, I want first-token latency, total duration, model duration, Tool duration, model requests, token usage, Agent steps, and terminal outcomes recorded, so that performance regressions can be located.
45. As a developer, I want CI to use a deterministic fake model, so that automated tests are repeatable and do not incur external API costs.
46. As a developer, I want a manual live-model benchmark, so that each configured provider can be measured without turning provider latency into a universal CI requirement.
47. As a developer, I want a seed command that creates only the development patient, participant, and就诊事项, so that local conversation persistence can be tested without seeding business Skills or Tools.
48. As a maintainer, I want database migrations rather than runtime table creation, so that schema changes are reviewable and reproducible.
49. As a Web client, I want the existing AI SDK UI stream protocol preserved, so that the frontend does not depend on LangGraph or provider-specific event types.
50. As a future hospital integration developer, I want HospitalOperations to remain an external boundary, so that fake and real hospital data can be connected without changing the Agent or Skill management interfaces.
51. As a future hospital administrator, I want the same Skill and Tool management contracts to support a later visual interface, so that backend behavior does not need to be redesigned when the UI is added.
52. As a future hospital reviewer, I want risk screening and人工风险审核 to remain a protected platform extension rather than an ordinary removable Skill, so that the later safety workflow cannot be disabled by capability configuration.

## Implementation Decisions

- The existing MediPetAssistant remains the primary application-facing Interface. HTTP delivery, tests, and future channels consume its typed event stream rather than LangGraph or provider objects.
- The existing AI SDK UI Server-Sent Events protocol remains the browser transport. Only approved text, progress, structured data, terminal completion, and safe failure events may leave the backend.
- Model integration crosses a ModelPort. The production Adapter uses ChatOpenAI against an official OpenAI Chat Completions-compatible contract with a configurable base URL, API key, and model.
- The production model SDK remains hidden behind ModelPort so that provider-specific packages or a different API dialect can be adopted later without changing Assistant callers.
- The LangGraph topology is complete and stable from the first delivery: capability discovery, model call, Tool-call routing, Tool validation, read execution, write proposal pause, observation loop, budget checks, failure, and finish.
- The graph is compiled independently of the current Registry contents. Each turn obtains an immutable Runtime Profile, Skill-version, and Tool-version snapshot at its start.
- With zero business Tools, the model receives an empty Tool collection and the complete graph takes the model-to-finish path. This state is expected and must not be reported as an error.
- Platform operations such as capability loading, persistence, proposal pause, confirmation, and audit are not business Tools and cannot be created, disabled, or rebound by an administrator.
- PostgreSQL is the source of truth for patients used by the development seed, visit participants,就诊事项, messages, Skill metadata and versions, resources, Tool metadata and versions, bindings, proposals, receipts, and audit records.
- SQLAlchemy asyncio is used for persistence and Alembic owns schema migrations. Runtime automatic table creation is not part of application startup.
- Conversation messages use pending, streaming, completed, failed, and cancelled states. Participant input is persisted before model invocation; an assistant message exists before streaming begins.
- Assistant deltas are persisted in batches rather than one transaction per token. Only completed messages enter subsequent model context.
- Model context contains the system instruction plus the most recent completed messages from the same就诊事项. The initial default is twenty messages and is configurable.
- Conversation history cannot establish or change the patient identity, participant authorization, or service hospital.
- The system instruction limits first-stage behavior to门诊就诊协助, prohibits diagnosis and prescription, and prohibits presenting unconfigured hospital data as fact.
- The first stage does not perform风险初筛. The application and documentation must clearly identify the stage as development-only and must not accept real patient data.
- Before the first streamed token, a transient provider failure may be retried once. After streaming starts, the run does not retry automatically.
- Turn, model-call, and graph-step limits are enforced by code. The initial defaults are sixty seconds, thirty seconds, and eight steps.
- Unknown or unauthorized Tool calls are never executed. The model receives one safe structured correction opportunity within the remaining budget; a repeated invalid request terminates the run.
- Tools declare read or write effect. Read Tools may execute after validation. Write Tools produce a persisted proposal and pause; commit happens only after exact confirmation, expiry, scope, and idempotency checks.
- No business Tool implementation ships in this stage. Test-only providers supply dummy read and write Tools through dependency injection.
- Skills use an Agent Skills-compatible package with SKILL.md and optional non-executable references, Schemas, and templates.
- Skill governance adds a stable identifier, type, immutable version, lifecycle status, risk metadata, Tool bindings, actor fields, timestamps, and change notes.
- Skills are classified as instruction-only or tool-assisted. Instruction-only Skills can publish without bindings; tool-assisted Skills require compatible, enabled trusted Tools.
- Packages containing scripts or other executable content are stored as quarantined imports. They cannot be published or executed in this stage.
- Text Skill resources are stored in PostgreSQL and can be assembled into or imported from an archive. Object storage is deferred behind a future Adapter.
- Tool implementations come only from deployed trusted ToolProvider code. Registry synchronization records immutable Tool versions and never silently changes the contract used by a published Skill.
- Administrators can inspect, enable, disable, configure approval metadata, and bind Tools. They cannot edit implementations, upload executable code, register arbitrary URLs, or override platform safety checks.
- Skill publication validates package structure, file types and sizes, binding compatibility, effect and approval metadata, protected namespaces, static privilege violations, automated evaluations, and an explicit publish action.
- Management endpoints use a Bearer Token sourced from backend configuration. Token comparison is performed without logging or returning the token.
- Production must not expose the development management authentication mode as a substitute for hospital identity and role management.
- Development configuration supports hot reload at a turn boundary for LLM base URL, API key, model, temperature, model timeout, turn timeout, maximum steps, and context-message limit.
- Runtime environment, database URL, management Token, and hospital Adapter selection are startup configuration and require restart.
- Process environment variables override .env values. Hot reload applies only to fields whose active source is the development .env file.
- An invalid reloaded profile makes new turns and readiness unavailable until corrected; it does not silently retain the last valid profile. Existing turns finish with their pinned profile.
- Tests receive configuration and dependencies directly and do not watch .env. Production reads process configuration and does not watch repository files.
- Liveness indicates process health. Readiness additionally verifies required model and database configuration without exposing secrets.
- Metrics contain no raw participant message, full prompt, API token, database URL, or upstream error body. Configuration is represented by a non-secret version or fingerprint.
- The future风险初筛 and人工风险审核 design remains governed by the accepted SafetyPolicy ADR, but its runtime, queue, and UI are not implemented by this spec.

## Testing Decisions

- The primary test seam is MediPetAssistant. Scenario tests supply deterministic ModelPort, persistence, capability snapshots, and ToolProvider doubles, then assert only the ordered TurnEvents, persisted public state, proposals, receipts, and terminal outcome.
- The second test seam is the HTTP API because system-administrator capability management is a separate actor surface. Tests assert authentication, status codes, lifecycle transitions, archive round-trips, validation failures, and externally visible JSON contracts.
- The delivery Adapter is tested through the existing AI SDK UI stream protocol. Tests assert start, token delta, data, error, finish, cancellation, and final terminator ordering without asserting internal graph nodes.
- ReAct tests use test-only read and write Tools. They prove Action/Observation looping, observation delivery to the model, maximum-step termination, unknown Tool correction, loop detection, proposal pause, confirmation, expiry, duplicate confirmation, and idempotent receipt behavior.
- A zero-capability scenario is mandatory. It asserts that the model receives no business Tool definitions, returns streamed text, and completes without Registry errors.
- Model Adapter contract tests use a mock HTTP transport and cover base URL handling, authentication, streaming chunks, tool-call decoding, timeout normalization, cancellation, and safe upstream error mapping.
- Visit Store and capability repositories receive contract tests against PostgreSQL-backed implementations. In-memory or fake implementations must satisfy the same observable contracts where they exist.
- Message tests assert that pending, streaming, failed, cancelled, and completed states transition legally and that only completed history is supplied to the next model request.
- Hot-reload tests modify a temporary env file and assert next-turn activation, in-flight snapshot pinning, process-environment precedence, invalid-profile readiness failure, and secret-free logging.
- Management API tests use temporary Skills and dummy Tools. They cover empty-list behavior, draft creation, import/export, quarantine, review, publish, immutable versions, retirement, rollback selection, binding checks, Tool version drift, protected namespaces, and audit records.
- Authentication tests assert missing, malformed, and incorrect Bearer Tokens are rejected without leaking comparison details.
- Persistence tests apply Alembic migrations to a disposable PostgreSQL database and verify restart recovery of就诊事项 and completed messages.
- Existing Assistant tests for explicit confirmation and emergency precedence are treated as prior art for observable scenario style, but hardcoded demo identifiers and data will be removed or replaced by dependency-injected test fixtures.
- Existing HTTP tests for the AI SDK UI stream are prior art for delivery-contract assertions and must continue to prohibit internal Thought text.
- Existing Web message-part tests and the browser smoke test are preserved where behavior remains applicable. Demo hospital cards must not be expected in the zero-capability stage.
- CI never calls a paid or external model. Live-provider smoke and benchmark runs are explicit developer commands requiring environment configuration.
- Performance tests distinguish application overhead from provider behavior. Fake-model runs can detect deterministic regressions; live-model reports establish a provider- and model-specific baseline without universal latency thresholds.
- Good tests assert stable interfaces, persisted effects, security boundaries, and participant-visible events. They do not assert private LangGraph node order, exact prompts, chain-of-thought, SDK object shapes, or incidental SQL statements.

## Out of Scope

- Creating or seeding any business Skill or business Tool.
- Creating fake hospital departments, doctors, slots, routes, appointments, or external hospital data.
- Implementing FakeHospitalOperations or a production hospital integration.
- Providing model-generated department guidance from general medical knowledge.
- Activating business Tool calling for the development chat.
- Implementing风险初筛,人工风险审核,审核恢复, hospital review queues, or participant risk notifications.
- Building a Skill/Tool administration Web interface.
- Building the future hospital staff message-push page.
- Supporting executable Skill scripts.
- Supporting arbitrary HTTP, OpenAPI, MCP, shell, Python, or JavaScript Tool creation from the management API.
- Implementing object storage for large Skill resources.
- Implementing hospital administrator accounts, hospital identity-provider integration, roles, or multi-person approval.
- Using the development Bearer Token as production authentication.
- Implementing real patient registration, identity proofing,就诊授权, or authorization delegation.
- Implementing production medical-data encryption, retention, archival, deletion, export, or compliance policies.
- Implementing a persistent LangGraph checkpointer; this stage persists business messages and state while using an in-memory execution checkpointer.
- Implementing appointment cancellation.
- Defining cross-provider response-time SLOs.
- Sending real patient or confidential hospital data to the configured model.

## Further Notes

- The repository glossary defines the canonical outpatient terms used by this spec. Generic terms such as User, Session, Task, and MedicalCase must not replace患者,就诊参与者,就诊事项, or门诊就诊.
- The ADR “使用版本化 Agent Skills 与受信任 Tool Registry” governs capability packaging, versioning, and execution trust.
- The ADR “使用模型风险初筛并由医院人员最终审核” governs the later safety phase and intentionally does not authorize risk adjudication in this stage.
- The research note “Agent 应用中的 Skill 与 Tool 管理模式研究” records the primary-source comparison behind the chosen Skill/Tool separation.
- The next product sequence is: create an independent fake hospital data source, implement FakeHospitalOperations, register hospital query/action Tools, create corresponding Skills, activate business ReAct behavior, add风险初筛 and人工风险审核, then build administration and message-push interfaces.
