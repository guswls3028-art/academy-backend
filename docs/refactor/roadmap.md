# Large Refactor Roadmap

**Status:** [PROPOSED] execution plan  
**Scope:** workspace root, backend, frontend, docs, validation pipeline  
**Target document:** `C:\academy\ARCHITECTURE.md`  
**Companion docs:** `inventory.md`, `phase-0-guardrails.md`, `validation-matrix.md`

This document is the executable path toward the target architecture. It does not
describe current behavior unless marked [VERIFIED]. Current truth still comes
from code, scripts, CI, and runtime checks.

## Operating Assumption

We will refactor aggressively, but not blindly. Physical code movement only
starts after guardrails can prove that tenant/auth, API contracts, worker
entrypoints, and frontend routes still work.

## Definition Of Done

- Backend/frontend type and boundary drift is blocked by tooling, not memory.
- Tenant isolation and auth/account-recovery behavior have regression tests.
- Legacy paths touched by the refactor are either routed to the canonical path or
  removed.
- Docs that become stale are updated, absorbed into SSOT, or archived.
- Every phase has static checks, focused tests, and at least one user-facing
  validation path.

## Workstreams

| Workstream | Owner | Purpose |
|---|---|---|
| Backend boundary | Main + backend audit agent | `apps/`, `academy/`, worker, tenant/auth, context contracts |
| Frontend SSOT | Main + frontend audit agent | generated API types, shared format/status/query/ui, app boundaries |
| Validation/deploy | Main + validation audit agent | CI, local render, E2E, worker, production smoke |
| Docs/rules | Main + docs audit agent | target/current/proposed separation, stale guidance removal |

The main agent owns final decisions and integration. Parallel agents collect
evidence and propose bounded changes; they do not own architecture decisions.

## Phase 0 - Guardrails Before Movement

Goal: make current risk visible before moving code.

Required work:

- [ ] Generate and commit a current inventory snapshot.
- [ ] Add backend import-boundary guard in baseline mode.
- [ ] Add tenant-scope audit in baseline mode.
- [ ] Add OpenAPI/schema generation path or prove the blocker.
- [ ] Add frontend generated API type path or prove the blocker.
- [ ] Add frontend app/domain boundary guard in baseline mode.
- [ ] Capture Django app-label, URL resolver, migration dry-run, and worker
      settings snapshots.
- [ ] Capture frontend `shared -> app_*` and role-app `@admin/*` dependency
      baselines.
- [ ] Decide frontend lockfile and React type-version policy before relying on
      typecheck as a migration gate.
- [ ] Register validation commands and expected owners.
- [ ] Update docs so target, current, proposed, and reports are not mixed.

Exit criteria:

- Existing backend and frontend checks still pass.
- New guardrails can run locally without blocking on old violations.
- New violations in touched files can be detected.
- `validation-matrix.md` is accepted as the phase gate list.
- App labels, URL prefixes, and worker app registries are explicitly protected.

## Phase 1 - Contract And SSOT Rails

Goal: stop future drift while the old tree still exists.

Backend:

- Keep Django app labels and migrations stable.
- Keep deployed URL prefixes stable through compatibility routing.
- Introduce published contract surfaces under current domains before physical
  relocation.
- Route account recovery, tenant resolution, and notification send paths through
  canonical services.
- Add tests around login, find ID, password reset, tenant switching, and
  Alimtalk payload generation.

Frontend:

- Route auth recovery UI through one API module and one modal/controller surface.
- Introduce generated API type imports for new or touched endpoints first.
- Centralize status labels, format helpers, and query keys when a touched feature
  currently duplicates them.
- Remove `shared -> @admin` and role-app `@admin` dependencies by graduating true
  shared contracts before enforcing strict app boundaries.

Exit criteria:

- Serializer or response shape changes fail typecheck when frontend is stale.
- Account recovery can be validated end-to-end.
- No new duplicate auth/recovery API or modal path remains.

## Phase 2 - Context Boundaries Without Table Churn

Goal: create bounded-context behavior before risky file moves.

Rules:

- No database table rename unless a phase explicitly proves it is required.
- No migration path may weaken tenant isolation.
- Cross-domain calls must move toward contract functions before package moves.
- Compatibility imports are allowed only at the old public boundary and must be
  documented with removal criteria.
- Model package moves require worker registry verification and migration dry-run.

Initial backend contexts:

| Context | Current domains |
|---|---|
| identity | students, parents, teachers, staffs |
| academics | lectures, enrollment, schedule, attendance, progress |
| assessment | exams, submissions, results, homework, homework_results |
| clinic | clinic |
| content | matchup, inventory, assets, tools |
| messaging | messaging |
| community | community |
| commerce | billing/fees surfaces |
| media | video |
| public | landing_public |

Exit criteria:

- Contract surfaces exist for the first migrated context.
- Import guard blocks new direct cross-context model/service imports.
- Compatibility paths have tests and cleanup tickets in this document.

## Phase 3 - Physical Layout Migration

Goal: move files only after behavior boundaries are stable.

Migration order:

1. Create destination packages and compatibility shims.
2. Move pure/shared code first.
3. Move service/API code next.
4. Move models last, preserving Django app labels unless a dedicated migration
   plan is approved.
5. Remove shims only after all imports and tests prove they are unused.

Do not move `core.User` or tenant models in the first physical migration wave.
They are auth and migration fixed points.

Exit criteria:

- `manage.py check`, import guard, unit tests, and focused E2E pass.
- No unresolved compatibility shim remains without a removal date.

## Phase 4 - Frontend Domain Cleanup

Goal: make the frontend match backend boundaries where useful without forcing a
cosmetic rewrite.

Rules:

- `shared/` cannot import any app.
- App domains cannot import other app internals.
- Cross-app reusable pieces graduate to `shared/`.
- Domain moves must include route-level render checks.

Exit criteria:

- Boundary lint runs in baseline mode.
- Touched pages use shared API/types/format/status/query paths.
- Admin/student/teacher auth and recovery flows render locally and pass E2E.

## Phase 5 - Worker And Deployment Hardening

Goal: ensure background systems keep working through the refactor.

Rules:

- Worker entrypoints remain thin.
- Worker settings and Docker/job definitions are verified from executable
  artifacts, not narrative docs.
- Messaging/Alimtalk validation includes payload content and delivery evidence
  when a real send is required.

Exit criteria:

- Worker boot checks pass.
- Messaging worker can process the affected notification path.
- Production smoke covers API, frontend, and the touched async path.

## Phase 6 - Cleanup And Seal

Goal: finish the work instead of leaving scaffolding.

Required cleanup:

- Delete dead code and unused compatibility shims.
- Remove stale docs or absorb their durable rules into SSOT docs.
- Update release notes or refactor report.
- Archive bulky evidence under `_artifacts/`.
- Produce a final validation report with passed/failed/skipped checks and
  residual risks.

## Rollback Policy

- Guardrail-only changes can roll back by removing the guard and its dependency.
- Contract changes roll back by restoring previous API shape and generated types.
- Physical moves must keep compatibility shims until a rollback window closes.
- Data migrations require a separate rollback section before execution.

## Stop Conditions

- Tenant isolation cannot be proven.
- Auth/account recovery behavior diverges from production policy.
- Worker deploy path cannot be verified.
- A destructive data operation is required without an approved migration plan.
