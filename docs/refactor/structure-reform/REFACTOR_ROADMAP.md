# Structure Reform Roadmap

**Status:** [PROPOSED] strangler roadmap  
**Captured:** 2026-05-22  
**Principle:** converge feature responsibility first; move folders only after behavior boundaries are proven.

This roadmap is separate from the current account-recovery/password/Alimtalk
release. That release may keep its compatibility paths while this reform starts
with audits, guardrails, and student canonicalization.

## Phase 0: Audit And Guardrails

Goal:

- Make current duplicate roots, tenant risks, and API/frontend drift visible.
- Add baseline-only guards before package movement.

Work:

- Keep `STRUCTURE_AUDIT.md`, `DUPLICATE_ROOTS.md`, and
  `DOMAIN_BOUNDARIES.md` current when touched.
- Add backend import-boundary and tenant-query snapshot checks in baseline mode.
- Add API contract snapshot path for high-risk endpoints.
- Add frontend API-type generation path or record blocker.
- Inventory E2E direct API helpers and legacy/wrong routes.
- Add deprecation logging plan for old routes, not deletion.

Risk:

- Existing dirty worktree and active release work can obscure refactor changes.
- Baseline guard may be noisy if introduced as strict too early.

Tests:

- `manage.py check`, migration dry-run, worker boot snapshot.
- Backend boundary snapshot.
- Frontend typecheck/build once lock/type policy is resolved.
- E2E helper route inventory.

Done:

- New violations in touched files can be detected.
- Tenant fallback/cross-tenant access is not introduced.
- Legacy paths are documented with owner and removal condition.

## Phase 1: Students Canonicalization

Goal:

- Make student identity/profile/lifecycle read and write through canonical
  selectors/services while preserving current URLs.

Work:

- Add `students/selectors.py` for tenant-scoped read contracts.
- Add student services for create, update profile, parent relink, soft delete,
  restore, permanent delete planning, registration approval, import row handling.
- Route `StudentViewSet`, `RegistrationRequestViewSet`, `StudentProfileView`,
  and Excel/import worker through those services incrementally.
- Add deprecation logs for legacy/self/profile paths before removing anything.
- Start frontend convergence after backend contract snapshots exist.

Risk:

- Student state affects enrollment, attendance, clinic, messaging, inventory,
  fees, community, storage, results, and student app.
- Permanent delete is destructive and must not be rewritten first.

Tests:

- Contract snapshots for `/students/`, `/students/{id}/`, `/students/me/`,
  `/student/me/`, `/core/me`.
- Tenant isolation for list/detail/update/delete and parent child selection.
- Student update propagation E2E across admin, teacher, student profile, auth
  linkedStudents, messaging recipient preview.
- Create/restore/delete lifecycle tests including parent, membership, enrollment,
  clinic participant, and notification event.

Done:

- One canonical student update changes every relevant surface consistently.
- Existing URLs still work.
- Legacy routes log deprecation source without breaking callers.

## Phase 2: Enrollment / Results / Exams / Homework Consistency

Goal:

- Make learning/assessment state transitions explicit and consistent.

Work:

- Promote enrollment selectors/services for roster and status changes.
- Move enrollment matrix toggles behind enrollment service.
- Define exam/submission/result recalculation events.
- Clarify `homework` vs `homeworks` ownership and URL compatibility.
- Centralize grade/status calculation contracts used by backend and frontend.

Risk:

- Scores and submissions are user-visible and data-sensitive.
- Existing tests may verify specific screens rather than service contracts.

Tests:

- Enrollment status transition tests.
- Exam/homework submission/result recalculation snapshots.
- Tenant isolation for cross-tenant enrollment/submission/result IDs.
- Frontend grade/status display contract tests.

Done:

- Enrollment/result/exam/homework writes have named use-cases.
- Same score/status value is not recomputed independently in model, serializer,
  view, and frontend state.

## Phase 3: Attendance / Clinic / Messaging Event Structure

Goal:

- Move side effects from view bodies into domain events/tasks.

Work:

- Define attendance and clinic status events.
- Route messaging sends through an enqueue/dispatch contract.
- Add source/use-case/actor/tenant logging metadata.
- Add idempotency/dedup guard for high-volume sends.
- Keep direct-send compatibility until outbox/event behavior is proven.

Risk:

- Messaging side effects are operationally visible and can send real Alimtalk.
- Clinic/attendance transitions are concurrency-sensitive.

Tests:

- Event emission without duplicate send.
- Messaging template/recipient tenant tests.
- Clinic participant transition state machine tests.
- Attendance update notification tests with send mocked unless real send is
  explicitly required.

Done:

- Save flows do not directly send external messages in the middle of unclear
  transaction boundaries.
- Logs include tenant, actor, source, use-case, and target IDs.

## Phase 4: AI / OMR / Matchup Job Structure

Goal:

- Make async job submission, worker processing, and job result ownership clear.

Work:

- Introduce job enqueue contracts for AI, OMR, matchup, and Excel import.
- Normalize tenant and source_domain/source_id payloads.
- Separate public/iframe tenant resolution exceptions from internal job flows.
- Add worker boot and payload contract tests.

Risk:

- Worker settings and app registries differ from API settings.
- Tenant fallback in async jobs can leak data if payloads are ambiguous.

Tests:

- Worker boot check.
- Job enqueue payload snapshot.
- Tenant-scoped job/result access tests.
- OMR batch upload and result processing tests.

Done:

- Every job has explicit tenant/source/use-case metadata.
- Worker cannot process tenant-scoped data without tenant in payload or verified
  system scope.

## Phase 5: Frontend Design System / API Client Cleanup

Goal:

- Stop role apps from depending on each other's internals and align UI/API
  contracts.

Work:

- Generate backend API types and route touched API modules through them.
- Move reusable student types/mappers/query keys to shared contract modules.
- Remove `@admin/domains/students/*` imports from teacher/auth/student/shared.
- Add boundary lint in baseline mode, then touched-file strict mode.
- Normalize design-system components where role apps duplicate table/modal/form
  behavior.

Risk:

- Type generation can create noise if backend schema is unstable.
- UI moves can break role-specific behavior if shared components are too generic.

Tests:

- Typecheck.
- Route render checks for admin/teacher/student student screens.
- E2E for signup, profile update, student list/detail, parent child switch.
- Visual QA only for touched UI surfaces.

Done:

- Touched role apps use shared contracts rather than importing other app
  internals.
- API response changes surface in typecheck or contract snapshots.

## Cleanup And Removal Rule

Legacy removal happens only when all are true:

- runtime deprecation logs show no current caller or the caller is migrated;
- contract/E2E tests cover the canonical route;
- rollback plan is documented;
- tenant isolation tests pass;
- old path removal does not require destructive data migration.
