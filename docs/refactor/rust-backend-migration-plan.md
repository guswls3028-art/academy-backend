# Rust Backend Migration Plan

**Status:** Proposed · Deferred until the current Academy execution plan is complete or explicitly reprioritized
**Owner:** Backend
**Last reviewed:** 2026-11-05 KST
**Scope:** Django API and worker runtime; React/TypeScript client remains in place

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

The target has one Rust API deployment and the existing operational worker
boundaries. Do not create a service per domain. Python and Rust API pools may
coexist temporarily for route-by-route cutover; each migrated domain has one
runtime owner and one writer at a time. Remove the Django runtime and Python
worker consumers after their production paths have moved. A Python exception
requires a named Python-only dependency, an owner, and a review/removal date.

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

The proposed initial performance bar is either:

- at least **25% lower p95 or p99 latency** at equivalent load and resource
  limits; or
- at least **30% more successful throughput per vCPU** without worsening the
  pre-registered latency and error thresholds.

Finalize the exact bar from the measured baseline before the pilot. The pilot
also requires unchanged business results, zero tenant/role boundary regression,
and no material increase in database load, queue age, retries, or operational
cost. If it misses the agreed bar, stop language migration for that component
and keep its current implementation.

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
Each queue/message family has one active consumer owner during cutover.
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
  journeys pass against Rust.
- **Authentication and tenant context:** Resolve identity and tenant at every
  request/job entry; missing or ambiguous context fails closed. Preserve role,
  selected-student/child, account-recovery, and session/token behavior. Do not
  move identity storage or change tokens as a side effect of a language port.
- **Database:** Keep existing tables and Django app labels initially. One
  migration system owns schema changes at a time. Assign one runtime writer per
  domain; never allow Django and Rust to independently own writes to the same
  business operation. Use additive, backward-compatible changes during
  coexistence. Plan the final migration-history handover before retiring Django.
- **Workers:** Preserve queue names, versioned envelopes, tenant ID,
  correlation/idempotency keys, visibility/retry/dead-letter behavior, and
  persisted job-state semantics. Do not run old and new consumers concurrently
  for the same message family unless duplicate execution is explicitly safe and
  proven.
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

Start after the current `docs/PLAN.md` execution is complete, or after its owner
explicitly reprioritizes work. Reconcile active release ownership and preserve
all current HOLDs. Record exact starting backend/frontend SHAs and current
runtime evidence. Inventory API routes, role journeys, auth/tenant middleware,
domain writes, Django app labels/migrations, worker envelopes, external SDKs,
and current deployment commands. Publish the performance and correctness
acceptance thresholds before choosing the pilot.

### Phase 1 — Contracts and Rust platform proof

Set up the Cargo workspace and production-equivalent build in the existing
backend repository. Prove health/readiness, structured logs, request/job
correlation, configuration/secrets retrieval, PostgreSQL pooling/transactions,
OpenAPI compatibility, and tenant-scoped repository behavior. Decide the
single schema-migration authority and the bounded ingress cutover mechanism.
Do not change production routing or data ownership in this phase.

### Phase 2 — One worker pilot

Select the first worker from profiling. Tools document/transformation work is a
candidate only if it is shown to be CPU-bound; otherwise choose the measured
hotspot. Replace the existing consumer implementation behind its current queue
contract. Keep the old immutable artifact available for rollback. Verify output
parity, tenant isolation, idempotency, retries, queue age, cleanup, and resource
use in isolated development and preproduction before any production rollout.

### Phase 3 — Rust API foundation and read-only route pilot

Add the Rust API pool behind the existing ingress. First implement shared
platform behavior: authentication validation, fail-closed tenant resolution,
permissions, request IDs, errors, health/readiness, and database access. Start
with a bounded read-only route group to compare real response semantics without
duplicate writes. Run the same role journeys and compare response contracts,
query behavior, latency, and resource use.

### Phase 4 — Domain-by-domain cutover

Move one bounded domain/route group at a time, starting with lower-coupling
areas. Keep a single writer and a single migration owner for that domain. Route
traffic to Rust only after API parity, role journeys, tenant-isolation checks,
reload/persistence, downstream consumers, failure/recovery, and rollback have
passed. Move identity/account-recovery and other cross-cutting core flows only
after the shared Rust platform has a proven production track record.

### Phase 5 — Retire Python backend paths

After every production API route and worker consumer has an identified Rust
owner, drain old consumers, verify no Django/Python production entrypoint or
scheduled job remains, and complete the migration-history handover. Remove
obsolete Django runtime dependencies, settings, images, queues/configuration,
compatibility shims, and documentation only after exact live readback. Keep any
approved Python-only exception isolated, owned, and review-dated.

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
- Do not remove Django runtime capacity, old queue consumers, or compatibility
  paths until the new owner is live, persisted state is read back, downstream
  roles pass, and rollback is no longer needed.
- Declare the migration complete only when every production route and worker
  has an explicit Rust owner, old Python runtime paths are retired or approved
  as exceptions, the performance bar is met for the migrated targets, and the
  current architecture/domain/deployment documentation and CI gates describe
  the new state.

## 7. Main risks and controls

| Risk | Control |
|---|---|
| Tenant/auth behavior diverges between runtimes | Port one shared, fail-closed contract; test cross-tenant, role, and selected-user boundaries at every route cutover. |
| Django and Rust both write the same operation | One domain owner and one writer; do not use dual writes. |
| API or queue contract drifts | Keep OpenAPI and versioned SQS envelopes as compatibility gates; run real role/job journeys. |
| Migration period becomes permanent hybrid | Track migrated routes/consumers and remaining Python entrypoints; every Python exception has an owner and review/removal date. |
| Rust adds services and deployment complexity | One Rust API deployment, preserve existing worker boundaries, no domain-per-service split, one cutover at a time. |
| Rewrite ships without user-visible or cost benefit | Require the pre-registered production-shaped performance bar; stop the affected migration if it misses. |
| Database migration history becomes ambiguous | Choose one migration authority before the first Rust write; preserve current schema and app-label history through coexistence. |

## 8. Decisions deferred to the implementation kickoff

The kickoff must verify current source and runtime state before deciding:

1. exact Rust framework/database crate versions and build image;
2. the first CPU-bound pilot from measured profiles;
3. authentication/token validation and tenant-context compatibility details;
4. one migration authority and the final Django migration-history handover;
5. ingress path split, capacity/cost impact, canary weights, and rollback trigger;
6. exact performance threshold and stabilization period from current SLOs.

This proposal has no fixed calendar estimate. Size the phases after the route,
worker, migration, and dependency inventory. When execution is authorized, add
one owned Rust-migration work item to `docs/PLAN.md`; this proposal alone does
not change active priority or authorize production changes.

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
