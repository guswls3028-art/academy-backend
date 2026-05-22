# Structure Reform Roadmap

**Status:** [ACTIVE] strangler roadmap with implemented Phase 1 and Phase 2/3/4 guardrail slices
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

Implemented:

- Backend boundary snapshot now distinguishes application port/cancellation
  contracts from real adapter -> use-case reverse imports, reducing
  `adapter_application_import` from 12 to 4.
- AI segmentation DTO and proposal payload validation contracts now live under
  `academy.domain.ai`, letting OCR/VLM/proposal adapters depend on pure domain
  contracts instead of application use cases. Backend boundary snapshot now
  reports `adapter_application_import = 0`.

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

Implemented:

- `students.selectors` and `students.services.profile.update_student_profile`
  route admin profile updates, `/students/me`, and student-app profile updates
  through one canonical tenant-scoped profile write path.

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

Implemented:

- `enrollment.selectors` now owns tenant-scoped enrollment/session roster reads,
  including active session enrollment rows.
- `enrollment.services.lifecycle` now owns bulk enrollment create, session
  enrollment create, enrollment delete/status side effects, and student learning
  access toggles for session/exam/homework.
- Student enrollment matrix views now act as a facade and no longer mutate
  enrollment/session/exam/homework assignment state directly.
- Exam/homework assignment screens now use the canonical active session roster
  selector, preventing cross-tenant corrupted rows and INACTIVE enrollments from
  entering assignment writes.
- `ExamRecalculateView` test fixtures now match the sealed submission scope
  contract: regrading requires `SessionEnrollment` + `ExamEnrollment`.

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

Implemented:

- Automatic notification enqueue payloads now carry source metadata:
  `source_domain`, `source_use_case`, `domain_object_id`, and optional
  `actor_id`.
- Attendance and clinic event wrappers populate source/use-case metadata while
  preserving the existing `transaction.on_commit` compatibility path.
- Messaging worker logs the source context for traceability without changing
  delivery semantics or requiring a data migration.

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

Implemented:

- `ai.gateway.dispatch_job` rejects tenant-scoped job dispatch when
  `tenant_id` or `source_domain` is missing.
- `dispatch_job` rejects payload-level `tenant_id` mismatches before creating
  an `AIJobModel` row or publishing SQS.

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

Implemented:

- The known E2E helper route `/api/v1/students/students/` was removed from
  `frontend/e2e/admin/dnb-lectures-sessions.spec.ts` and replaced with the
  current `/api/v1/students/` API.
- Frontend enrollment API calls now have a canonical admin enrollment client at
  `src/app_admin/domains/enrollment/api/enrollments.ts`. Existing lectures,
  exams, and homework API files remain as compatibility facades and no longer
  duplicate session-enrollment normalization.
- Session-enrollment fetch/bulk contract now lives at
  `src/shared/api/contracts/sessionEnrollments.ts`. Admin enrollment remains a
  compatibility facade, homework creation uses its own homework facade instead
  of the exams facade, lecture scores/exam creation/session clinic can call the
  shared contract directly, and teacher mobile score entry no longer imports
  admin homework internals.
- Frontend CI now runs `pnpm guard:legacy-api`, blocking reintroduction of
  `students/students`, `lectures/enrollments`, and direct enrollment-create
  routes in tracked source/E2E files.
- Frontend boundary snapshot improved from 43 to 41 cross-app/admin role imports
  after the shared session-enrollment contract slice.
- Frontend typecheck and production build pass after the shared
  session-enrollment contract slice.
- Shared frontend surfaces are app-agnostic after moving theme runtime/constants,
  responsive view state, clinic-target fetch, video status/workbox APIs, and
  session progress fetches to `src/shared/*` contracts. Admin paths now remain
  compatibility facades where needed.
- Frontend boundary snapshot improved from 41 to 37 cross-app/admin role imports,
  and `shared -> app_*` imports improved from 6 to 0 after the shared purity
  slice.
- Teacher settings E2E now follows the canonical appearance route instead of the
  obsolete inline theme-card contract, and message-log copy is aligned to
  "발송 내역".
- Operational notification counts now live behind
  `src/shared/api/contracts/notifications.ts` and
  `src/shared/hooks/useOperationalNotificationCounts.ts`. Admin notification
  paths are compatibility facades, and teacher surfaces use
  `useTeacherPendingCounts`.
- Frontend boundary snapshot improved from 37 to 30 cross-app/admin role imports
  after the notification-count contract slice.
- Community post/reply/attachment contracts now live behind
  `src/shared/api/contracts/community.ts`. Admin community API remains a
  compatibility facade, student notices/community use the student API facade,
  teacher developer feedback uses a teacher-local facade, and patch notes data
  moved to `src/shared/product/patchNotesData.ts`.
- Frontend boundary snapshot improved from 30 to 24 cross-app/admin role imports
  after the community/notice/developer contract slice.
- Video access-mode/rule contracts now live behind
  `src/shared/api/contracts/videos.ts`, and reusable thumbnail rendering moved to
  `src/shared/media/video/VideoThumbnail.tsx`. Admin video paths remain
  compatibility facades, student video API/player/thumbnail wrappers use shared
  contracts, and video E2E expectations follow the current KPI/folder explorer UX.
- Frontend boundary snapshot improved from 24 to 21 cross-app/admin role imports
  after the video media/access contract slice.
- Lecture/session attendance API now lives behind
  `src/shared/api/contracts/attendance.ts`. The admin attendance path remains a
  compatibility facade, teacher attendance and lecture matrix surfaces use the
  shared contract directly, and duplicate teacher matrix/export calls were
  removed. `e2e/teacher/attendance-contract.spec.ts` covers the teacher
  attendance and matrix render paths.
- Frontend boundary snapshot improved from 21 to 17 cross-app/admin role imports
  after the attendance contract slice.
- Storage/inventory API now lives behind
  `src/shared/api/contracts/storage.ts`, student API contracts now live behind
  `src/shared/api/contracts/students.ts`, and student Excel utilities live under
  `src/shared/product/students/studentExcel.ts`. Admin paths remain
  compatibility facades, while teacher/student storage, inventory, and student
  surfaces use shared contracts directly.
- Frontend boundary snapshot improved from 17 to 9 cross-app/admin role imports
  after the storage/students/inventory contract slice.
- Fees API contracts now live behind `src/shared/api/contracts/fees.ts`, and
  fees status/tone labels live under `src/shared/product/fees/feesStatus.ts`.
  Admin fees paths remain compatibility facades, teacher fees uses shared
  contracts directly, and local fees E2E uses a browser-route feature-flag
  override instead of mutating tenant configuration.
- Frontend boundary snapshot improved from 9 to 7 cross-app/admin role imports
  after the fees contract/status slice.
- Tools timer download API now lives behind `src/shared/api/contracts/tools.ts`.
  The admin stopwatch timer API remains a compatibility facade, teacher timer
  uses the shared contract directly, and teacher timer E2E follows the current
  "타이머" page label.
- Frontend boundary snapshot improved from 7 to 6 cross-app/admin role imports
  after the tools timer contract slice.
- Exam enrollment API now lives behind
  `src/shared/api/contracts/examEnrollments.ts`. The admin exam enrollment API
  remains a compatibility facade, while teacher exam detail and OMR pages use
  the shared `enrollment_id` contract directly.
- Frontend boundary snapshot improved from 6 to 4 cross-app/admin role imports
  after the exam enrollment contract slice.

## Cleanup And Removal Rule

Legacy removal happens only when all are true:

- runtime deprecation logs show no current caller or the caller is migrated;
- contract/E2E tests cover the canonical route;
- rollback plan is documented;
- tenant isolation tests pass;
- old path removal does not require destructive data migration.
