# Pre-Promotion Structure Plan

**Status:** [PROPOSED] launch-readiness refactor entry plan
**Captured:** 2026-05-24 KST
**Updated:** 2026-05-25 KST
**Scope:** backend, frontend, deployment verification, worktree hygiene
**Rule:** no physical package moves before Phase 0 guardrails can measure drift.

This document turns the pre-promotion inspection into an execution plan. It is
not a claim that the target architecture is already implemented. Current truth
comes from code, scripts, CI/CD, and runtime checks.

## Confirmed Current State

Repository state:

- Backend `main` is aligned with `origin/main`; no local tracked changes before
  this planning pass.
- Frontend `main` is aligned with `origin/main`; no local tracked changes before
  this planning pass.
- Workspace root is not a git repository. Loose promotion/report artifacts were
  moved under `_artifacts/`.

Deployment state:

- Backend latest repository HEAD is release documentation commit `8b076f3c`.
- Backend deployed runtime baseline is `d9059b4b`; backend run `26361832699`
  completed successfully after workflow dispatch.
- Frontend deployed commit is `7554fb22`; frontend run `26363380568`
  completed successfully after push.
- No queued or in-progress backend/frontend release runs were found during this
  pass.
- `https://api.hakwonplus.com/healthz`,
  `https://hakwonplus.com/version.json`, and
  `https://hakwonplus.com/login/hakwonplus` returned 200-level responses.

Local validation:

- Backend: `ruff`, Django check, migration dry-run, smoke tests, worker settings
  drift, and worker boot check passed.
- Frontend: typecheck, lint, production build, refactor inventory, and
  `test:e2e:gate` passed. The password-reset test account was absent, so that
  spec skipped its 4 account-specific cases by design.
- Frontend build still reports the existing large chunk warning. This is a
  performance backlog item, not a failed gate.

## Measured Structure Snapshot

Backend boundary snapshot:

- `cross_domain_import`: 123
- `cross_domain_internal_import`: 678
- `domain_infra_import`: 86

Frontend boundary snapshot:

- `api_generated_dir_present`: false
- `same_app_domain_import`: 217
- `large_frontend_file`: 34
- `local_format_defs`: 124
- `status_map_defs`: 37
- `query_key_literals`: 970
- `inline_style_objects`: 3884
- `api_response_type_defs`: 105
- `e2e_wait_for_timeout`: 69

Interpretation:

- The product is deployable, but the codebase is not ready for broad movement.
- Backend risk is concentrated in direct cross-domain internals and domain-level
  infrastructure imports.
- Frontend risk is concentrated in missing generated API types, same-app domain
  imports, repeated query/status/format definitions, and large UI files.

## Working Assumption

The next refactor phase should be guardrail-first:

1. preserve current deployment behavior;
2. add or strengthen report-only boundary checks;
3. make new/touched violations fail while old violations remain baselined;
4. introduce contract/type SSOT before moving packages;
5. move code only after the compatibility boundary is explicit.

## Backend Plan

Phase B0 - baseline and block new drift:

- Treat `scripts/lint/refactor_boundary_snapshot.py` as the measured baseline.
- Add touched-file strict mode before moving domain packages.
- Separate public selector/service imports from direct internal model/view/service
  imports in reports.
- Keep Django app labels, migrations, URL prefixes, tenant resolver, auth, and
  worker registries as fixed points.

Phase B1 - contract surfaces:

- For high-traffic cross-domain paths, publish explicit selectors/contracts
  before package moves.
- Start with assessment, clinic, attendance, messaging, and AI callback paths,
  because they dominate current internal import samples.
- Keep legacy imports as compatibility facades only when callers cannot be moved
  in the same slice.

Phase B2 - infrastructure boundary:

- Move direct `requests`, R2, Redis, SQS, AI, PDF/OCR, and storage calls out of
  domain code when the touched path already crosses that dependency.
- Route new infrastructure calls through `academy/adapters/*` or the current
  documented adapter boundary.

Phase B3 - worker safety:

- Run worker settings drift and direct worker boot checks for every model,
  settings, queue, or callback movement.
- Keep video Batch, messaging SQS/EC2, and AI SQS/EC2 paths separate.

## Frontend Plan

Phase F0 - baseline and block new drift:

- Treat `pnpm refactor:inventory` as the measured baseline.
- Add touched-file strict mode for app/domain boundary violations before moving
  role app packages.
- Keep `shared/` app-agnostic and prevent role apps from importing other role
  app internals.

Phase F1 - backend API type SSOT:

- Prove backend schema generation first.
- Generate frontend API types to `src/shared/api/generated/`.
- Apply generated types to new/touched endpoints before trying a broad rewrite.

Phase F2 - shared product contracts:

- Graduate repeated API modules, status maps, query keys, and format helpers into
  `src/shared/*` only when at least two app/domain callers need them.
- Preserve compatibility facades in old app paths during the slice, then remove
  them when imports reach zero.

Phase F3 - UI file reduction:

- Split large UI files only when touching their flow or when they block
  validation/debuggability.
- Route visual slices through local render, screenshots, and production E2E when
  user-facing behavior changes.

## Promotion Readiness Gates

Always:

- `git -C C:\academy\backend diff --check`
- `git -C C:\academy\frontend diff --check`
- clean `git status --short --branch` in each repo before release commit

Backend:

- `python -m ruff check apps/ academy/`
- `python manage.py check --settings apps.api.config.settings.test`
- `python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test`
- `python -m pytest tests\test_smoke.py -v --tb=short -x`
- `python -m pytest tests\test_worker_settings_drift.py -v --tb=short -x`
- worker boot check when worker settings, models, queues, or callbacks change

Frontend:

- `pnpm typecheck`
- `pnpm lint`
- `pnpm build`
- `pnpm refactor:inventory`
- `pnpm test:e2e:gate` for release gates or user-facing flows

Deployment:

- Backend deploy truth: `.github/workflows/v1-build-and-push-latest.yml`
- Frontend deploy truth: `.github/workflows/quality-gate.yml`
- Verify there are no queued/in-progress release runs before closure.
- Check production API health and frontend version/login URLs after deploy.

## First Execution Slices

1. [COMPLETED 2026-05-25] Add touched-file strict mode to backend boundary
   snapshot output and wire it into the backend deploy lint gate.
2. Add frontend boundary strict mode for same-app domain imports in touched
   files.
3. Prove backend OpenAPI/schema generation path without committing broad
   generated churn.
4. Add frontend generated type path and apply it to one low-risk endpoint family.
5. Pick one backend domain with clear ownership, publish selectors/contracts, and
   reduce internal imports there.
6. Pick one frontend domain hot pair, graduate the shared contract, and remove
   compatibility imports after callers move.

## Non-Goals For The First Phase

- No table rename.
- No Django app-label rename.
- No worker topology change.
- No broad visual redesign.
- No all-at-once `apps/` to `contexts/` move.
- No type generation adoption across the full API in one PR.

## Closure Criteria

A structure slice is done only when:

- touched behavior still passes focused tests and the relevant release gate;
- old compatibility paths are removed or marked with a removal condition;
- boundary counts for direct/internal violations do not increase;
- docs distinguish `[VERIFIED]`, `[PROPOSED]`, and compatibility-only paths;
- release notes or refactor docs record the actual commit and validation
  evidence.
