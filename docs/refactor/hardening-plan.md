# Project Hardening Plan

**Status:** active

**Started:** 2026-07-27 KST

**Scope:** `C:\academy\backend`, `C:\academy\frontend`, shared delivery and
operations gates

**Primary objective:** complete, simplify, and strengthen existing user
journeys without expanding the product feature set.

## 1. Outcome

The hardening program is complete when an existing user can finish each core
journey through the visible UI, the persisted result is correct and observable,
failure and retry behavior are explicit, tenant and permission boundaries are
preserved, and the same behavior is protected by focused automated evidence.

This is not a feature roadmap. A hardening batch must do at least one of the
following:

- remove a blocked, misleading, incomplete, or unnecessarily difficult step;
- make an existing result deterministic, durable, and visible to the user;
- close an error, empty, loading, retry, concurrency, or permission gap;
- replace duplicated or unsafe implementation with the existing canonical path;
- reduce measured structural debt without changing the product contract;
- strengthen the gate that proves an existing contract still works.

Requests for a new workflow, new notification template, new business state, or
new product capability stay outside this plan until they are approved
separately.

## 2. Verified Starting Point

Local baseline captured on 2026-07-27 KST:

| Surface | Verified result | Remaining signal |
|---|---|---|
| Workspace audit | 8 PASS, 3 WARN, 0 FAIL | old dirty worktrees and optional Docker absence |
| Backend framework | Django check PASS; migration drift none | preserve on every model/settings batch |
| Backend static gates | Ruff PASS; lifecycle and refactor boundary gates PASS | 31 cross-domain imports, 230 test-only internal imports |
| Backend smoke | 23 tests PASS, 5 subtests PASS | broaden only by touched journey |
| ID/domain safety | 20 allowed integer-FK warnings, 0 errors | no unordered `.first()` remains |
| Frontend compile | typecheck, lint, legacy API guard, build PASS | repeat after every frontend batch |
| Frontend refactor budget | PASS after first hardening slice | 147 same-app imports; 34 large files |
| E2E inventory | safety guard: 217 active specs; 977 `test(` lines; 118 `test.skip(` lines | skip is not user-journey proof |
| Promo visual check | 1366px and 390px PASS; no horizontal overflow; 0 console errors | repeat when the page is touched |

Known environment constraints:

- `C:\academy` is not a Git repository; backend and frontend close separately.
- Pre-existing dirty secondary worktrees are preserved until their ownership is
  known. They are not cleanup targets for this program.
- Production notification verification is restricted to the controlled
  recipient documented in project rules.
- Production E2E data remains restricted to Tenant 1 with tagged fixtures and
  cleanup.

## 3. Hardening Definition of Done

Every changed journey is reviewed against all applicable rows, not only the
happy path.

| Lens | Required evidence |
|---|---|
| User completion | entry, action, confirmation, and reflected result all work through the UI |
| State clarity | loading, empty, success, error, disabled, and retry states explain what happens next |
| Persistence | refresh/re-entry shows the saved server truth; no false local success |
| Data integrity | duplicate clicks, retry, concurrent requests, and partial failure do not corrupt state |
| Tenant/permission | server-side scope is explicit; wrong-role and wrong-tenant access fail closed |
| Contract | request/response types and status semantics use a canonical definition |
| Accessibility | keyboard path, focus, label, dialog, and meaningful status announcement are intact |
| Responsive UI | relevant 1366px, 1100px, and 390px layouts have no hidden action or horizontal overflow |
| Operations | useful logs/metrics exist for silent or asynchronous failure; no secret or PII leakage |
| Regression proof | focused unit/integration/E2E test fails before the fix or directly proves the contract |
| Documentation | current behavior and proposed work are clearly separated |
| Closure | focused checks, repo gates, `diff --check`, intentional commit, and clean touched repos |

A screenshot-only test, an API-only setup followed by a page-load assertion, or
a test skipped because fixture data is absent does not prove an actual-user
roundtrip.

## 4. Priority Model

Work is ordered by user harm and evidence, not by file size or visual novelty.

### P0 — Trust boundary

- tenant leakage, authorization bypass, wrong-row selection, data loss;
- irreversible or duplicate mutation;
- authentication/account recovery failure;
- release, migration, worker, or notification safety gate failure.

Exit requirement: focused regression, framework/static gate, and the nearest
real user or service roundtrip all pass.

### P1 — Core journey cannot finish

- save/submit/approve/grade/reserve actions that do not persist or reflect;
- a required action hidden by layout or unclear state;
- failure with no recovery path;
- asynchronous work that can silently stall or produce contradictory status.

Exit requirement: provider/consumer role roundtrip, refresh proof, and cleanup
or idempotency proof.

### P2 — Repeated friction and incomplete UX

- misleading labels, duplicate controls, weak validation, poor empty/error
  states, inaccessible dialogs, mobile breakage;
- avoidable repeated work in high-frequency screens.

Exit requirement: zero/one/many data states, long Korean content, relevant
viewport checks, and no console error.

### P3 — Structural and operational debt

- large mixed-responsibility modules, duplicate response types/utilities,
  boundary imports, stale docs, noisy or missing diagnostics.

Exit requirement: a measured count decreases or a new regression guard is
installed, with behavior-preserving verification.

## 5. Workstreams

### A. Data, authentication, and tenant integrity

1. Inventory singleton ORM reads, fallback behavior, integer domain IDs,
   destructive paths, and tenant filters.
2. Make ordering and uniqueness assumptions explicit.
3. Verify login throttle, account recovery, preview-token consumption, public
   interactions, and role switching under retry/concurrency.
4. Audit bulk and background tasks for tenant context, idempotency keys, and
   partial-failure recovery.
5. Keep migration and worker-settings drift checks in every affected batch.

### B. Actual-user core journeys

Build or strengthen one canonical roundtrip at a time:

1. public signup → admin approval → student login;
2. lecture creation → enrollment → session → supplement visibility;
3. homework creation → student submission → admin grading → student result;
4. clinic target creation → student reservation → admin decision → resolution;
5. exam/OMR input → scoring → correction → student/parent reflection;
6. message preview/send → provider result → audit log;
7. video permission/playback/progress → completion-dependent reflection.

For each journey:

- use visible UI actions for the behavior under review;
- create only the minimum uniquely tagged fixture;
- verify both producer and consumer roles;
- refresh and re-enter to prove server persistence;
- exercise one meaningful failure or retry branch;
- prove tenant and permission rejection at the server boundary;
- clean up only through the approved fixture mechanism.

### C. UX completion by route group

Review student, teacher, landing, promo, admin, clinic, materials, messages,
staff, storage, and tools route groups using the existing route verifiers as the
inventory boundary.

For each relevant screen:

- zero, one, and many rows;
- long Korean names and narrow widths;
- initial loading, background refresh, empty, validation, server error, and
  retry;
- double-click/rapid-submit protection;
- unsaved changes and navigation behavior;
- keyboard focus, accessible name, dialog close/restore, and status messaging;
- 1366px primary desktop, 1100px constrained desktop, and 390px mobile where
  the route claims mobile support.

Any visible control that cannot complete its advertised action is fixed,
disabled with a truthful reason, or removed. It is not replaced with a new
feature.

### D. Contract, state, and architecture consolidation

1. Prove a backend OpenAPI generation path and generated frontend type path.
2. Replace hand-written touched response wrappers with canonical contracts.
3. Consolidate query keys, storage access, formatting, status maps, and CDN
   loaders behind existing shared boundaries.
4. Reduce same-app domain reach-through by exposing narrow public contracts.
5. Split large modules only at stable responsibility seams, with no incidental
   redesign.
6. Keep React Query/server state authoritative; local/session storage is only a
   guarded compatibility or navigation aid.

### E. Resilience, performance, and observability

1. Add bounded timeout, retry, and idempotency behavior at external and
   asynchronous boundaries.
2. Make pending, failed, retrying, and terminal status distinguishable.
3. Measure high-frequency routes and large exports before optimizing.
4. Prevent duplicate network work and unnecessary render/fetch cascades.
5. Log actionable correlation and tenant-safe identifiers without secrets or
   message payload leakage.
6. Verify notification, worker, storage, and media degradation paths rather than
   silently falling back.

### F. Quality system and workspace hygiene

1. Keep touched-file strict gates and aggregate budgets non-regressing.
2. Convert high-value skips into deterministic fixtures; do not chase raw test
   count.
3. Keep manual review, E2E inventory, and executable workflows synchronized.
4. Close backend/frontend independently with explicit file staging.
5. Report old worktrees as ownership debt; never delete or overwrite them by
   assumption.

## 6. Phased Execution

### H0 — Baseline and gate recovery

Status: first slice complete locally on 2026-07-27.

Completed:

- removed six unordered singleton ORM selections;
- reduced ID/domain safety output from 26 to 20 allowed warnings;
- restored all four failing frontend refactor budgets;
- centralized guarded session storage access;
- centralized typed PDF CDN module loading and removed eight TypeScript
  suppression directives;
- consolidated touched API response types;
- extracted the promo file ribbon from a large page stylesheet/component;
- verified the changed promo page at desktop and mobile widths.
- passed the local E2E gate with 31 tests passed and 4 guarded password-mutation
  tests skipped. The first run also exposed that local Vite must receive
  `VITE_DEV_PROXY_TARGET` from the E2E API setting; rerunning with the CI
  workflow contract passed.

Exit:

- backend focused tests and static gates pass;
- frontend refactor budget, typecheck, lint, build, and E2E gate pass;
- the current baseline and this plan are committed.

### H1 — Core roundtrip proof

Order:

1. select the highest-risk incomplete journey from the seven in Workstream B;
2. record current UI/API/persistence gaps;
3. fix the smallest complete vertical slice;
4. add deterministic fixture and consumer-side assertion;
5. run focused backend tests, local UI rendering, and the relevant E2E tier;
6. update the inventory with what is now truly proven.

Prefer homework or clinic as the first no-real-send journey. Signup and
notification live-send branches follow only with their controlled-recipient
guards.

Exit:

- the selected journey completes without API-assisted substitution for the
  behavior under test;
- refresh, wrong-role/tenant, retry, and cleanup evidence exist;
- no skip is counted as pass.

### H2 — UX completeness sweep

Process route groups in user-frequency order:

1. student/teacher daily work;
2. admin lectures, sessions, homework, clinic, exams, results;
3. messages, materials, storage, staff, tools;
4. public landing and promo surfaces.

Exit:

- every reviewed route has a disposition: pass, fixed, or backlog with
  reproduction and priority;
- P0/P1 findings are closed before moving to the next route group;
- mobile and accessibility evidence exists where promised.

### H3 — Contract and boundary consolidation

Exit:

- generated API type path is proven or a concrete blocker is documented;
- touched duplicate contracts and storage/CDN/query helpers use canonical
  boundaries;
- `same_app_domain_import`, response-type, suppression, and large-file metrics
  do not regress and selected metrics decrease.

### H4 — Failure-mode and operations hardening

Exit:

- asynchronous/provider boundaries have explicit timeout, retry, terminal
  status, and actionable diagnostics;
- concurrency and idempotency tests cover critical mutations;
- production canaries remain controlled and reproducible.

### H5 — Release proof and cleanup

Exit:

- tiered backend/frontend gates pass;
- required local and production E2E evidence is attached to the release;
- docs match executable behavior;
- touched repositories are clean;
- temporary processes/artifacts are removed;
- remaining work is a prioritized backlog, not an unclassified observation.

## 7. Batch Protocol

Each batch follows this fixed loop:

1. **Recon:** identify the user, entry route, persisted entity, consumer, and
   failure boundary.
2. **Assumption:** state the smallest behavior-preserving assumption before
   editing.
3. **Reproduction:** capture failing test, trace, screenshot, or deterministic
   code-level finding.
4. **Implementation:** change only the vertical slice required for the existing
   contract.
5. **Focused verification:** run touched unit/integration checks.
6. **User verification:** render locally and run the narrow actual-user path.
7. **Regression gates:** run static budgets, framework checks, build, and the
   required E2E tier.
8. **Review:** inspect diff, security/tenant/concurrency behavior, and docs
   drift.
9. **Closure:** explicit staging, intentional commit, clean status, process and
   artifact cleanup.

If a batch reveals a separate feature request, record it outside the active
batch and continue closing the original contract.

## 8. Standard Verification Matrix

Backend minimum for touched runtime code:

```powershell
cd C:\academy\backend
git diff --check
python manage.py check --settings apps.api.config.settings.test
python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test
python -m ruff check apps/ academy/
python scripts/lint/check_submission_lifecycle_boundary.py
python scripts/lint/check_id_domain_safety.py
python scripts/lint/refactor_boundary_snapshot.py --strict-touched
python scripts/lint/refactor_boundary_snapshot.py --enforce-baseline
python -m pytest <focused tests> -v --tb=short -x
```

Frontend minimum for touched runtime code:

```powershell
cd C:\academy\frontend
git diff --check
pnpm typecheck
pnpm guard:legacy-api
pnpm lint
pnpm refactor:budget
pnpm build
pnpm test:e2e:gate
```

For local checkout E2E, start Vite with `VITE_DEV_PROXY_TARGET` explicitly set
to the same API target as `E2E_API_URL`. Loading `.env.e2e` inside Playwright
does not reconfigure an already running Vite process.

Add `pnpm guard:e2e-safety` before broad/manual E2E, and use canary or
production verification only when the batch crosses that delivery boundary.

## 9. Current Measured Backlog

These are signals for ordering, not standalone success metrics:

- 147 frontend same-app domain imports;
- 34 frontend files over the current large-file threshold;
- 101 hand-written API response-shaped definitions;
- no generated frontend API directory;
- 38 direct session-storage references after the first consolidation;
- 118 E2E skip references;
- four explicitly documented journeys still needing stronger actual-user proof:
  signup approval, lecture/session, homework, and clinic;
- 31 backend cross-domain imports and 230 test-only internal imports;
- 20 explicitly allowed integer-FK safety warnings;
- multiple pre-existing dirty secondary worktrees with unconfirmed ownership.

The next batch starts with one canonical homework or clinic roundtrip, not a
repository-wide rewrite.
