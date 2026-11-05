# Rust Backend Migration Plan

**Status:** Proposed · Deferred until the current Academy execution plan is complete or explicitly reprioritized
**Owner:** Backend
**Last reviewed:** 2026-11-05 KST
**Scope:** Django API and worker runtime; React/TypeScript client remains in place

**Decision basis:** The long-term Rust direction is a strategic preference.
Measure performance to choose priorities and validate releases; do not require
every endpoint to deliver the same speedup before it can leave Django.

This is a long-term architecture proposal. It does not change the current
execution order in [`docs/PLAN.md`](../PLAN.md), authorize a rewrite, or replace
any release/HOLD gate. Implementation starts only as a separately owned task
after the current product work is complete or its owner explicitly changes
priority.

## 1. Direction

Make Rust the eventual primary language for Academy's backend API and worker
processes. Keep the existing React/TypeScript frontend, PostgreSQL data model,
Redis, R2, SQS message boundaries, and AWS Batch video-encoding boundary stable
through the migration unless measured evidence requires a separately approved
change.

The target is a modular Rust API application, horizontally replicated when
needed, plus the existing operational worker boundaries. One API application
does not mean one server. Python and Rust API pools may coexist temporarily for
bounded workflow cutover. Each business command has one authoritative
implementation; account for API handlers, scheduled commands, signals, admin
actions and workers that can write the same state. Multiple replicas of that
implementation remain valid.

Retire Django runtime paths after their production workflows have moved. A
specialist Python document or AI dependency may remain behind an existing worker
boundary when output compatibility requires it. Record its purpose, owner,
replacement criteria and review date; a date alone never justifies removing a
working capability. Such exceptions must not create a second owner of product
permissions or business rules. Operational scripts and test tools do not need
translation merely to eliminate all Python files.

Keep the backend in the existing `academy-backend` repository. During migration,
place Rust code in one Cargo workspace rather than creating another repository
or independently versioned package per domain. Begin with domain modules inside
the API/worker crates; split crates only where a real dependency boundary
justifies it.

### Candidate implementation stack

- **HTTP:** Axum on Tokio as the first proof-of-concept candidate.
- **PostgreSQL:** SQLx as the first candidate for explicit SQL and compile-time
  query checking. The proof of concept must cover existing transactions,
  tenant-scoped reads/writes, connection pooling, and migration compatibility.
- **Contracts:** Keep the current OpenAPI document as the compatibility
  reference. Generate or validate Rust request/response types against it; do not
  create a second independently maintained public schema.
- **Telemetry:** Preserve structured logs, request/correlation IDs, health and
  readiness behavior, and existing runtime metric ownership.

These are candidates, not pinned dependencies. The first Rust proof of concept
must validate their current versions, production build, operational support, and
fit with Academy's authentication and database contracts before adoption.

SQLx query checks do not prove tenant isolation, authorization, query performance
or transaction correctness. Require a validated tenant/actor context at the
repository boundary, scoped SQL, database constraints and cross-tenant behavior
checks. CPU-heavy work must use bounded execution capacity rather than blocking
Tokio's request tasks. Track database-pool size, timeouts, cancellation and
backpressure as part of the platform design.

## 2. Evidence and decision boundary

The 2026-10-25 [usage audit](../reports/cost-waste-audit.latest.md) reports API
CPU averages of 11.19% over 30 days and 11.86% over 90 days, with brief peaks
near 99.8%. The [deployment record](../infrastructure/deployment-architecture.md)
says an isolated p99 failure lacked per-worker telemetry and was not
conclusively attributed to worker cold-route imports. These observations
justify measuring CPU-heavy paths; they do not establish that the API as a whole
is CPU-bound.

Before implementation, capture production-shaped per-route and per-job
baselines: p50/p95/p99 latency, CPU-seconds per successful request/job, request
and job throughput, database time/query count, external-I/O time, queue age,
memory, errors, and retries. Compare equivalent inputs, data sizes, database
plans, hardware limits, and warm/cold conditions. A microbenchmark alone is not
a migration decision.

Do not use an arbitrary universal speedup percentage as a migration gate.
Before the pilot, define workload-specific latency, error, memory, database and
queue budgets from observed use and product requirements, including the allowed
measurement uncertainty. A release must meet those budgets and its correctness
gates. Correct defects before promotion.

Evaluate the program on total operating cost, sustainable peak capacity,
application CPU/memory, user-visible latency, feature delivery time and the
remaining cost of two runtime stacks. A performance-neutral CRUD endpoint can
still migrate to retire Django and simplify ownership. A disappointing pilot
requires revising the implementation, target workload or program economics;
it does not automatically turn the target into an indefinite hybrid. Claims of
large savings or a fixed schedule require measured evidence.

### Known migration obligations in the inspected source

- The OpenAPI generation baseline records inference errors/warnings. It is a
  transport reference, not a complete specification of actual API behavior.
  Capture missing endpoint semantics before migrating them.
- The API config uses both TokenVersionJWTAuthentication and tenant-aware
  session authentication, with refresh rotation and blacklisting. Verifying a
  JWT signature alone cannot reproduce its authentication contract.
- Tools dependencies include PyMuPDF, python-pptx, openpyxl, Pillow and
  reportlab. A worker-first port is not automatically the smallest pilot or the
  best performance investment. Assess dependency replacement and document
  output fidelity before selecting it.

Sources: [schema owner](../architecture/api-schema-contract.md),
[generation baseline](../../schema/generation-baseline.json),
[API settings](../../apps/api/config/settings/base.py), and
[Tools requirements](../../requirements/worker-tools.txt). These are source
observations, not a fresh production profile.

## 3. Target and transition topology

```text
React / TypeScript (unchanged)
              |
       existing ingress
        /           \
legacy routes       migrated routes
   Django API         Rust API (one deployment)
        \           /
       PostgreSQL / Redis / R2

Existing SQS worker boundaries remain separate deployables.
Each business command/message family has one authoritative implementation.
Video encoding remains on AWS Batch.
```

During transition, use one Django API pool and one Rust API pool, with ingress
routing for whole, bounded route groups. This is temporary migration capacity,
not a permanent domain-microservice topology. Choose the exact routing mechanism
from the current load-balancer and deployment contracts during Phase 1. The
final topology removes the Django pool and leaves one Rust API pool plus the
existing worker deployment boundaries.

## 4. Contracts that must survive the migration

- **HTTP:** Preserve deployed URL prefixes, status/error semantics, pagination,
  serialization, authentication behavior, and the OpenAPI/frontend type
  contract. Cut over a bounded route group only after the existing consumer
  journeys pass against Rust. Include null/empty distinctions, decimals,
  timestamps, ordering, validation, concurrency and side effects absent from
  the schema. Fix schema inference gaps as each route is migrated.
- **Authentication and tenant context:** Resolve identity and tenant at every
  request/job entry; missing or ambiguous context fails closed. Preserve role,
  selected-student/child, account-recovery, and session/token behavior. Do not
  move identity storage or change tokens as a side effect of a language port.
  Build token-version checks, active membership/role checks and applicable
  session/CSRF behavior before exposing the first authenticated Rust route.
  Token issuance, refresh/revocation, password management and account recovery
  move as a separately verified identity workflow later.
- **Database:** Keep existing tables and Django app labels initially. One
  migration system owns schema changes at a time. Retain Django migrations as
  that authority through coexistence by default. Do not start a competing Rust
  migration history on the same schema. Assign business-command ownership using
  the complete write graph, including legacy signals and jobs. Use additive,
  backward-compatible changes and maintain cross-domain transaction boundaries.
  At final handover, establish a verified schema baseline for the Rust migration
  runner, covering constraints, indexes, sequences, defaults and data migrations.
  Verify both existing-database upgrades and a fresh installation; do not replay
  historical Django migrations or delete their evidence merely to switch tools.
- **Workers:** Preserve queue names, versioned envelopes, tenant ID,
  correlation/idempotency keys, visibility/retry/dead-letter behavior, and
  persisted job-state semantics. Standard SQS delivery can repeat a message even
  with only one consumer. Idempotency is always required. Plan compatible
  in-flight work, consumer draining, lease/visibility behavior and reconciliation
  during old/new overlap; a deployment must not execute an external side effect
  twice. Distinguish one logical owner from the number of worker processes.
- **Product policies:** Preserve approved Alimtalk owner channels and exact
  templates with no SMS/LMS fallback; keep automated analysis as a proposal
  unless its current owner contract allows canonical writes. Video encoding
  remains AWS Batch only.
- **User-authored data:** Preserve manual, approved, and user-created data and
  references.
- **Operations:** Keep immutable artifact identity, isolated development and
  preproduction, production-resource denial, health/identity checks, healthy
  rolling replacement, and exact live revision/affected-flow readback. Keep
  old/new API and database compatibility during overlap, and preserve active
  playback/editing through rollout. Reuse the existing release owners and
  gates.

## 5. Migration sequence

### Phase 0 — Start gate and baseline

Define a finite set of current work items and their completion evidence in
`docs/PLAN.md`, then start after that milestone, or after its owner explicitly
reprioritizes work. Avoid an undefined requirement that all future product work
must be finished. Reconcile active release ownership and preserve
all current HOLDs. Record exact starting backend/frontend SHAs and current
runtime evidence. Inventory API routes, role journeys, auth/tenant middleware,
domain writes, Django app labels/migrations, worker envelopes, external SDKs,
and current deployment commands. Publish the performance and correctness
acceptance budgets before choosing the pilot. Produce a route/workflow inventory,
write/dependency graph, document-output corpus, Django capability replacement
list, baseline report and pilot work card. Include admin, management commands,
scheduled jobs, ORM signals, validation and transaction/on-commit behavior.

### Phase 1 — Contracts and Rust platform proof

Set up the Cargo workspace and production-equivalent build in the existing
backend repository. Prove health/readiness, structured logs, request/job
correlation, configuration/secrets retrieval, PostgreSQL pooling/transactions,
OpenAPI compatibility, and tenant-scoped repository behavior. Decide the
single schema-migration authority and the bounded ingress cutover mechanism.
Do not change production routing or data ownership in this phase.

### Phase 2 — One complete workflow pilot

Choose a small workflow with manageable dependencies, representative validation
and data access, and a measurable objective. In isolated development, prove
authorized read -> validated write -> reload -> downstream consumption, plus
denial, concurrent modification and recovery. Include a job/side effect when
the selected workflow has one. A CPU-bound worker may be the pilot if its library
and output dependencies make it a suitable first slice; Tools is not mandatory.

Evaluate implementation effort, build/debug cycles, query behavior and output
correctness as well as speed. For PDF/PPT/Excel work, compare semantic content,
layout, fonts and editability on representative synthetic files; byte equality
alone is often inappropriate because metadata can differ. Keep the previous
artifact and data/message compatibility available for rollback.

### Phase 3 — Rust API foundation and read-only route pilot

Introduce one temporary Rust API pool behind the existing ingress when the API
pilot is ready. First implement shared
platform behavior: authentication validation, fail-closed tenant resolution,
permissions, request IDs, errors, health/readiness, and database access. Start
with a bounded read-only route group to compare real response semantics without
duplicate writes. Run the same role journeys and compare response contracts,
query behavior, latency, and resource use. The routing unit must match actual
ingress capabilities and the workflow dependency graph; a path prefix alone
does not prove an independent transaction. Record the routing owner and removal
condition. Avoid a Django-to-Rust-to-Django call cycle or an extra service per
domain. Production mutation must never be replayed for shadow comparison.

### Phase 4 — Domain-by-domain cutover

Move one bounded domain/route group at a time, starting with lower-coupling
areas. Cut over all writers and side effects of the selected business operation,
while retaining the single schema-migration authority. Route
traffic to Rust only after API parity, role journeys, tenant-isolation checks,
reload/persistence, downstream consumers, failure/recovery, and rollback have
passed. Move identity/account-recovery and other cross-cutting core flows only
after the shared Rust platform has a proven production track record.

### Phase 5 — Retire Python backend paths

After every production API route and worker consumer has an identified Rust
owner, drain old consumers, verify no Django/Python production entrypoint or
scheduled job remains outside explicitly accepted specialist exceptions, and
complete the migration-history handover. Replace any required Django admin and
operational commands before removing their runtime. Remove
obsolete Django runtime dependencies, settings, images and configuration,
compatibility shims, and documentation only after exact live readback. Keep any
approved Python-only exception isolated, owned, and review-dated. Preserve the
existing business queues and data unless a separate reviewed retirement is
required. Remove the temporary API pool and routing rules after the final
stabilization window; count remaining production entrypoints, not Python files.

## 6. Cutover, rollback, and completion gates

- Deploy each Rust artifact through the existing immutable candidate,
  development, isolated preproduction, and production sequence. No separate
  Rust fast path bypasses release gates.
- Canary the exact Rust artifact and route group. Compare latency, CPU per
  request/job, errors, database load, queue age, and the affected user journeys.
- Keep the previous Django artifact and compatible schema available through the
  agreed stabilization period. Route back to the previous owner or restore the
  previous worker artifact on a failed gate. Do not combine a language cutover
  with destructive schema cleanup.
- Prove that the previous owner understands rows, tokens and messages written
  by the new version. Routing rollback does not undo a message already sent or
  a committed business operation; retain new user writes and reconcile side
  effects. Restoring an old database snapshot is not the routine rollback plan.
- Do not remove Django runtime capacity, old queue consumers, or compatibility
  paths until the new owner is live, persisted state is read back, downstream
  roles pass, and rollback is no longer needed.
- Declare the migration complete only when every production route and worker
  has an explicit Rust owner, old Python runtime paths are retired or approved
  as exceptions, measured service budgets and the program's agreed operating
  objectives are met, and the
  current architecture/domain/deployment documentation and CI gates describe
  the new state.

## 7. Main risks and controls

| Risk | Control |
|---|---|
| Tenant/auth behavior diverges between runtimes | Port the complete fail-closed contract, including token version, refresh, session and membership behavior; verify every route cutover. |
| Django and Rust both write the same operation | Map API, signals, jobs and admin writes; one authoritative business-command implementation, without dual writes. |
| API or queue contract drifts | Keep OpenAPI and versioned SQS envelopes as compatibility gates; run real role/job journeys. |
| Migration period becomes permanent hybrid | Track complete workflows, active entrypoints and temporary routing; specialist exceptions have an owner, purpose, replacement criteria and review date. |
| Rust adds services and deployment complexity | One Rust API deployment, preserve existing worker boundaries, no domain-per-service split, one cutover at a time. |
| Rewrite ships without useful benefit | Compare representative service budgets, total operating cost and delivery effort; revise the program on evidence, while allowing neutral routes to migrate for consolidation. |
| Database migration history becomes ambiguous | Choose one migration authority before the first Rust write; preserve current schema and app-label history through coexistence. |

## 8. Decisions deferred to the implementation kickoff

The kickoff must verify current source and runtime state before deciding:

1. exact Rust framework/database crate versions and build image;
2. a complete pilot workflow selected using profiles and dependency cost;
3. authentication/token validation and tenant-context compatibility details;
4. one migration authority and the final Django migration-history handover;
5. ingress path split, capacity/cost impact, canary weights, and rollback trigger;
6. workload-specific service budgets, total-cost objectives and stabilization
   period from the baseline and product requirements.

This proposal has no fixed calendar estimate. Size the phases after the route,
worker, migration, and dependency inventory. When execution is authorized, add
one owned Rust-migration work item to `docs/PLAN.md`; this proposal alone does
not change active priority or authorize production changes.

Estimate the remaining work from completed pilot delivery and the inventory,
including migration adapters, CI, operations, parity verification and removal.
Do not infer a schedule from language speed or source-line count. Keep one
active workflow migration at first; expand parallelism only across independent
write ownership after the process has proven reliable.

## References

- [API schema contract](../architecture/api-schema-contract.md)
- [Hexagonal cutover policy](../architecture/hexagonal-cutover-policy.md)
- [Tenant request context](../architecture/tenant-request-context.md)
- [Messaging policy](../ssot/messaging-policy.md)
- [Database scaling and tenant isolation](../infrastructure/database-scaling-and-tenant-isolation.md)
- [Deployment architecture](../infrastructure/deployment-architecture.md)
- [Concurrent session and delivery policy](../operations/concurrent-codex-sessions.md)
- [Axum documentation](https://docs.rs/axum/latest/axum/)
- [SQLx documentation](https://docs.rs/sqlx/latest/sqlx/)
- [SQS at-least-once delivery](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/standard-queues-at-least-once-delivery.html)
