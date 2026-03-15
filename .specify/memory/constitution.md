<!--
SYNC IMPACT REPORT
==================
Version change: 1.1.0 → 1.2.0
Version bump rationale: MINOR — Principle I materially expanded to define Orchestrator and
  Domain Agent archetypes with explicit reasoning/planning responsibilities. New "Agent Role
  Taxonomy & Reasoning Boundaries" subsection added to Multi-Agent System Constraints.
  Orchestration Patterns and LLM-Powered Agents subsections updated to distinguish rules by
  agent role. No principles removed or incompatibly redefined.

Modified principles:
  - I. Agent-First Design
      Expanded: introduced formal Orchestrator and Domain Agent archetypes; reasoning and
      planning responsibilities assigned per role.

Modified sections:
  - Multi-Agent System Constraints › Orchestration Patterns
      Updated to include Orchestrator planning-step mandate, plan serialization, and replanning.
  - Multi-Agent System Constraints › LLM-Powered Agents
      Split guidance into Orchestrator-specific and Domain Agent-specific rules.

Added sections:
  - Multi-Agent System Constraints › Agent Role Taxonomy & Reasoning Boundaries

Removed sections:
  (none)

Templates requiring updates:
  ✅ .specify/templates/plan-template.md  — Constitution Check now covers role-taxonomy gate.
  ✅ .specify/templates/tasks-template.md — Phase 2 (Foundational) should include reasoning-
       trace setup and plan-store provisioning for Orchestrator features.
  ✅ .specify/templates/spec-template.md  — No structural changes required.
  ✅ .specify/templates/agent-file-template.md — No structural changes required.

Deferred TODOs (carried forward):
  - TODO(RATIFICATION_DATE): Confirm exact ratification date with project owner.
  - TODO(TOOL_REGISTRY_URL): Canonical Tool Registry endpoint once provisioned.
  - TODO(A2A_VERSION): Minimum A2A spec version once runtime selected.
  - TODO(MCP_SERVER_LOCATION): Canonical MCP server directory once first server created.
-->

# Agentic SaaS Constitution

## Core Principles

### I. Agent-First Design

Every capability MUST be implemented as a discrete, independently deployable agent or
agent-compatible service. Agents MUST have a single, clearly stated responsibility. An agent
that does two unrelated things MUST be split. There are no organisational-only agents —
every agent MUST deliver observable, testable value to an end-user flow or a system
orchestrator.

The system recognises two canonical agent archetypes, each with distinct cognitive roles:

**Orchestrator Agent**
An Orchestrator MUST possess both reasoning and planning capabilities. It decomposes
high-level goals into ordered sub-tasks, selects which Domain Agents to invoke, sequences
their execution, and adapts the plan in response to intermediate results or failures.
Orchestrators reason at the workflow level — they MUST NOT perform domain-specific work
themselves; all domain execution MUST be delegated to Domain Agents.

**Domain Agent**
A Domain Agent MUST possess reasoning capability within its declared domain. It interprets
task inputs, selects appropriate tools (via MCP), and produces structured outputs. Domain
Agents reason at the execution level — they MUST NOT orchestrate other agents or make
cross-domain decisions; cross-domain coordination is exclusively the Orchestrator's
responsibility.

**Rationale**: Separating orchestration reasoning (goal decomposition, sequencing, replanning)
from domain reasoning (tool selection, execution, interpretation) prevents agents from
accumulating responsibilities that erode single-purpose design and makes each agent's
cognitive scope independently testable and replaceable.

### II. Contract-Driven Communication via Tool Registry & Standard Protocols (NON-NEGOTIABLE)

All inter-agent and agent-to-service communication MUST be governed by three complementary
layers:

**1. Versioned Contracts**
Every agent capability MUST be defined by an explicit, versioned contract (OpenAPI, JSON Schema) before implementation begins. Breaking changes MUST increment the contract's
MAJOR version and include a migration plan. No agent MAY call another agent via an
undocumented or out-of-date contract.

**2. Tool Registry**
Every agent tool or callable capability MUST be registered in the central Tool Registry
before it can be invoked by any other agent or orchestrator. The registry entry MUST
include: tool name, version, input/output schema reference, access-control
policy, and SLO. Agents MUST discover tools via registry lookup — hard-coded tool
endpoints are PROHIBITED. The Tool Registry is the authoritative source of truth for
what capabilities exist in the system.
TODO(TOOL_REGISTRY_URL): Specify the canonical Tool Registry endpoint once provisioned.

**3. Standard Inter-Agent Protocols**
- **A2A (Agent-to-Agent Protocol)**: All agent-to-agent task delegation MUST use the A2A
  protocol. Each agent MUST publish an A2A-compliant Agent Card (JSON) describing its
  skills, authentication requirements, and supported interaction modes. Orchestrators
  MUST use Agent Cards for capability discovery before dispatching tasks.
  TODO(A2A_VERSION): Pin minimum supported A2A spec version once runtime is selected.
- **MCP (Model Context Protocol)**: All tools, resources, and prompt templates exposed
  to LLM-powered agents MUST be served via an MCP-compliant server. MCP server
  definitions MUST be version-controlled alongside the owning agent's code.
  TODO(MCP_SERVER_LOCATION): Document canonical MCP server directory once first server
  is created (suggested: `mcp/servers/<agent-name>/`).

**Rationale**: A contract alone is insufficient in a dynamic multi-agent system where
agents are added, versioned, and retired continuously. The Tool Registry provides runtime
discoverability; A2A provides a vendor-neutral wire protocol for task delegation; MCP
provides a standardised interface for LLM tool-use. Together they prevent undocumented
channels, hard-coded coupling, and protocol fragmentation.

### III. Test-First (NON-NEGOTIABLE)

Test-Driven Development is mandatory for all agent behaviours and inter-agent contracts.
The required order is: contract/integration tests written → reviewer confirms tests fail →
implementation begins → tests pass → refactor. The Red-Green-Refactor cycle MUST NOT be
skipped. Unit tests alone are insufficient; every agent MUST have at least one contract
test and one end-to-end integration test covering its primary user story. Tool Registry
registration and A2A Agent Card validity MUST be covered by automated tests.
Orchestrator plan generation and replanning logic MUST have dedicated test scenarios
covering failure paths and partial-completion recovery.

**Rationale**: Agent systems fail at interaction boundaries. Contract and integration tests
catch boundary failures that unit tests structurally cannot detect.

### IV. Observability & Auditability

Every agent action that changes state or crosses a service boundary MUST emit a structured
log event (JSON) containing: `agent_id`, `agent_role` (`orchestrator` | `domain`),
`trace_id`, `tenant_id`, `action`, `status`, `duration_ms`, and `error` (if applicable).
Reasoning traces produced by both Orchestrators and Domain Agents MUST be captured and
linked to the parent `trace_id`. Orchestrator plans MUST be persisted in the observability
store so that replanning events are diff-able against the original plan. Distributed tracing
(OpenTelemetry or equivalent) MUST be instrumented at agent entry/exit points, including
A2A task handoffs and MCP tool invocations. Audit logs for user-facing actions MUST be
immutable and retained per the data-retention policy defined in SaaS Quality Standards.

**Rationale**: Multi-agent systems with embedded reasoning are inherently harder to debug
than deterministic pipelines. Captured reasoning traces are the primary tool for auditing
Orchestrator decisions and diagnosing unexpected Domain Agent outputs.

### V. Resilience by Design

Agents MUST handle failures gracefully. Every synchronous inter-agent call MUST implement
a timeout. Agents that depend on external services MUST implement a circuit-breaker
pattern. Asynchronous workflows MUST be idempotent — the same message delivered more than
once MUST NOT cause duplicate side-effects. Dead-letter queues MUST be provisioned for
all async message channels. MCP server unavailability MUST be handled as a degraded-mode
scenario, not a hard failure, unless the tool is on the critical path. Orchestrator plans
MUST be serializable and re-entrant so that execution can resume from the last successful
step after a failure without re-running completed sub-tasks.

**Rationale**: In a distributed multi-agent system, partial failures are the norm, not the
exception. Resilience primitives prevent cascading failures from taking down unrelated
tenants or agent chains.

### VI. Multi-Tenancy & Tenant Isolation

All data stores, queues, agent state, and persisted Orchestrator plans MUST be partitioned
by `tenant_id`. Cross-tenant data access MUST be impossible at the infrastructure layer —
not merely prevented by application logic. Tenant provisioning and deprovisioning MUST be
automated and auditable. Resource quotas (API rate limits, compute, storage, LLM token
budgets) MUST be enforced per tenant to prevent noisy-neighbour effects. Tool Registry
entries and A2A Agent Cards MUST include tenant-scoped access-control policies.

**Rationale**: SaaS viability depends on hard tenant isolation. A single tenant's rogue
agent MUST NOT compromise another tenant's data, LLM budget, or performance.

### VII. Simplicity & Incremental Complexity

New agent interactions MUST start with the simplest working topology (direct call or single
queue). Additional orchestration layers, caches, or routing components are only introduced
when a concrete, measurable problem demands them (YAGNI). Every architectural complexity
introduced MUST be documented in the plan's Complexity Tracking table with a justification.
Abstractions shared across more than three agents MAY be promoted to a shared library;
abstractions used by fewer than three agents MUST NOT be prematurely extracted.

**Rationale**: Premature abstraction in agent systems creates hidden coupling and makes
the system harder for orchestrators and humans to reason about.

## Multi-Agent System Constraints

### Agent Role Taxonomy & Reasoning Boundaries

This section defines the enforceable boundaries for each agent archetype introduced in
Principle I.

**Orchestrator Agent — Reasoning & Planning Rules**:
- An Orchestrator MUST execute a structured reasoning step (e.g., Chain-of-Thought,
  ReAct loop, or equivalent) to produce a plan before dispatching any Domain Agent.
- The plan MUST be represented as a serializable, ordered task graph with: goal,
  sub-tasks, assigned Domain Agent per sub-task, dependencies, and success criteria.
- The plan MUST be persisted to an external store immediately after creation so that
  execution is re-entrant (Principle V).
- When a Domain Agent returns a failure or an out-of-expected-range result, the
  Orchestrator MUST trigger a replanning step rather than propagating the failure
  directly to the caller.
- An Orchestrator MUST NOT directly invoke domain tools (MCP tools owned by Domain
  Agents). All domain-level execution MUST go through a Domain Agent via A2A.
- An Orchestrator's Agent Card MUST declare `role: orchestrator` and list the Domain
  Agent skills it is authorised to delegate to.

**Domain Agent — Reasoning & Execution Rules**:
- A Domain Agent MUST use its reasoning capability to interpret the task payload
  received from the Orchestrator, select the appropriate MCP tools, and determine
  the correct execution sequence within its domain.
- A Domain Agent's reasoning is bounded to its declared domain. It MUST NOT make
  decisions that require knowledge of other domains or the broader workflow context.
- A Domain Agent MUST return a structured result containing: `status`, `output`,
  `reasoning_summary` (a brief natural-language explanation of decisions made), and
  `confidence` (where applicable).
- A Domain Agent MUST NOT call other Domain Agents directly. Cross-agent coordination
  MUST be escalated back to the Orchestrator.
- A Domain Agent's Agent Card MUST declare `role: domain` and the domain scope
  (e.g., `domain: billing`, `domain: document-processing`).

**Shared Rules (both archetypes)**:
- Reasoning traces MUST be linked to the active `trace_id` and emitted as structured
  log events (Principle IV).
- LLM token consumption for reasoning MUST be tracked per agent per tenant.
- Reasoning prompts MUST be versioned in source control (see LLM-Powered Agents).

### Orchestration Patterns

- Orchestrator agents MUST be stateless between task executions; all state — including
  the current plan — MUST be persisted in an external store, not in-memory.
- An Orchestrator MUST produce and persist a plan before dispatching the first Domain
  Agent task. Dispatching without a persisted plan is a compliance violation.
- Agent-to-agent calls MUST prefer asynchronous messaging (event/queue) over synchronous
  RPC unless latency requirements explicitly justify synchronous calls (document in plan).
- Recursive agent invocations (Agent A → Agent B → Agent A) are PROHIBITED unless
  explicitly documented and loop-bounded.
- Orchestrators MUST implement a maximum replanning budget per task (e.g., max N
  replanning attempts) to prevent infinite reasoning loops; exceeding the budget MUST
  result in a structured failure response to the caller.

### Protocol Standards (A2A & MCP)

**A2A Requirements**:
- Every agent that accepts delegated tasks MUST implement an A2A-compliant endpoint
  (`/a2a` or equivalent) and publish its Agent Card at a well-known path.
- Agent Cards MUST declare `role` (`orchestrator` | `domain`) and, for Domain Agents,
  the `domain` scope.
- Agent Cards MUST be kept in sync with the Tool Registry; a divergence between the
  two is treated as a contract violation.
- A2A task lifecycle states (`submitted`, `working`, `completed`, `failed`,
  `input-required`) MUST map to observable events in the logging layer (Principle IV).
- Streaming responses via A2A Server-Sent Events (SSE) MUST be used for long-running
  tasks to avoid client timeouts.

**MCP Requirements**:
- Each MCP server MUST expose a `/tools/list` endpoint returning schema-validated tool
  definitions. Tool schemas MUST match the corresponding Tool Registry entries.
- MCP servers MUST be versioned independently of the agents that host them. A new
  breaking tool schema MUST result in a new MCP server version.
- Domain Agents MUST NOT invoke tools outside of the MCP layer (no raw HTTP calls
  to internal services from prompt-driven code).
- Orchestrators MUST NOT consume Domain Agent MCP servers directly; they interact
  via A2A task delegation only.
- MCP `resources` and `prompts` primitives MAY be used where appropriate; their use
  MUST be documented in the feature spec's Key Entities section.

**Tool Registry Requirements**:
- New tool registration MUST be part of the feature's Phase 2 (Foundational) tasks.
- Deregistration of a tool MUST follow the same amendment process as a contract
  MAJOR version bump (migration plan required).
- The registry MUST support health/availability status per tool so orchestrators can
  route around unhealthy tools during replanning without failing entire workflows.

### Agent Versioning

- Agent APIs follow Semantic Versioning. Deploying a new MAJOR version MUST NOT remove
  the previous MAJOR version until all dependents have migrated (blue/green or canary
  rollout required).
- Orchestrator agents MUST declare the minimum version of each Domain Agent they depend
  on in their manifest.
- A2A Agent Cards MUST include the agent's semantic version and list deprecated skills
  with sunset dates.

### LLM-Powered Agents

**Rules applying to all LLM-powered agents**:
- Every LLM prompt (reasoning, planning, tool-selection) MUST be versioned and stored
  in source control alongside the agent code.
- Non-deterministic LLM outputs MUST be post-processed through a validation step before
  being passed to downstream agents or persisted.
- LLM API costs MUST be tracked per agent, per role, and per tenant and surfaced in the
  observability layer.
- Tool selection by any LLM-powered agent MUST be constrained to tools available in the
  MCP layer; unrestricted function-calling against arbitrary HTTP endpoints is PROHIBITED.

**Orchestrator-specific LLM rules**:
- The Orchestrator's planning prompt MUST produce output conforming to the serializable
  plan schema (task graph with goal, sub-tasks, agent assignments, dependencies, success
  criteria). Outputs that do not conform MUST be rejected and retried up to the
  replanning budget.
- Chain-of-Thought or equivalent step-by-step reasoning MUST be enabled for planning
  and replanning prompts to improve auditability.

**Domain Agent-specific LLM rules**:
- Domain Agents MUST use structured output (JSON mode or equivalent) for their final
  response to the Orchestrator to ensure the `reasoning_summary` and `confidence`
  fields are machine-parseable.
- Domain Agents MUST NOT expose raw LLM output to end users without a validation and
  formatting step.

## SaaS Quality Standards

### Security

- Authentication: All external API endpoints MUST require authentication (OAuth 2.0 /
  JWT). Agent-to-agent internal calls MUST use mTLS or a service-mesh identity mechanism.
- A2A endpoints MUST enforce the authentication scheme declared in the Agent Card
  (`securitySchemes`). Unauthenticated A2A calls MUST be rejected with HTTP 401.
- MCP servers MUST validate caller identity before exposing tools; tenant context MUST
  be propagated through MCP invocations.
- Secrets MUST NOT be hard-coded; all secrets are injected via environment variables or a
  secrets manager.
- Dependency scanning MUST run in CI on every pull request.

### Data & Compliance

- PII MUST be identified, minimised, and documented in the data model for each feature.
- Audit logs for PII-touching operations MUST be retained for a minimum of 90 days
  (adjust per applicable regulation; document the applicable regulation in the feature spec).
- Persisted Orchestrator plans and reasoning traces MUST be treated as potentially
  containing PII if the task inputs include user data; apply the same retention policy.
- Data-at-rest encryption is REQUIRED for all persistent stores.

### Performance & Scalability

- Each agent MUST publish its SLO: target p95 latency and target availability percentage.
- Orchestrator planning latency MUST be included in the end-to-end SLO budget; planning
  steps that exceed their time budget MUST abort and surface a timeout error.
- Load testing against SLOs MUST be part of the definition of done for each P1 user story.
- Horizontal scaling MUST be validated before a feature reaches production.

## Governance

This constitution supersedes all other documented practices for this repository. When a
conflict exists between this document and any other guideline, this document takes
precedence.

**Amendment procedure**: Amendments require (1) a written proposal describing the change
and its rationale, (2) review by at least one other contributor, (3) a migration plan for
any impacted feature specs, agent contracts, Tool Registry entries, A2A Agent Cards, or
persisted plan schemas, and (4) a version bump per the versioning policy below.

**Versioning policy**:
- MAJOR: Removal or backward-incompatible redefinition of any principle.
- MINOR: New principle, section, or materially expanded guidance.
- PATCH: Clarifications, wording fixes, non-semantic refinements.

**Compliance review**: All pull requests MUST include a "Constitution Check" section in
their plan.md confirming no principle is violated. If a violation is justified, it MUST be
recorded in the plan's Complexity Tracking table. For features introducing new agents, the
Constitution Check MUST explicitly confirm:
1. Agent role (`orchestrator` | `domain`) declared in Agent Card.
2. Tool Registry registration, A2A Agent Card publication, and MCP server definition are
   in the foundational tasks.
3. For Orchestrators: plan schema defined, plan store provisioned, replanning budget set.
4. For Domain Agents: domain scope declared, `reasoning_summary` field in output contract.

**Runtime guidance**: The agent-file template (`.specify/templates/agent-file-template.md`)
and any generated `CLAUDE.md` / agent context files serve as runtime development guidance
and MUST stay consistent with this constitution.

---

**Version**: 1.2.0 | **Ratified**: 2026-03-15 | **Last Amended**: 2026-03-15
