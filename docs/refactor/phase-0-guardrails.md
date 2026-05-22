# Phase 0 Guardrails

**Status:** [PROPOSED] before physical code movement  
**Purpose:** make the refactor observable and enforceable before package moves.

Phase 0 does not chase beauty. It installs rails so later movement cannot hide
tenant leaks, stale frontend types, broken workers, or silent docs drift.

## Guardrail Order

### 1. Repository Baseline

Capture current state:

```powershell
git -C C:\academy\backend status --short
git -C C:\academy\frontend status --short
```

Required before implementation phases:

- identify unrelated dirty files;
- avoid reverting user changes;
- separate backend and frontend commits/reports.
- decide how untracked refactor/account-recovery files are promoted into tracked
  work before treating them as stable API surface.

### 2. Backend Static Guard

Current:

- ruff `F821` exists.
- `scripts/lint/refactor_boundary_snapshot.py` exists in baseline mode.
- adapter -> application boundary scan allows application port/cancellation
  contracts and reports concrete use-case imports as violations.

Proposed:

- add import-linter or an equivalent AST-based boundary checker in baseline mode;
- forbid new direct imports across future context internals;
- forbid domain/application reverse imports where the hexagonal rule applies;
- allow compatibility shims only from documented legacy boundary modules.

First baseline targets:

- `apps.domains.* -> apps.domains.*.{models,services,views}` direct imports;
- `academy.domain -> django` imports;
- infrastructure SDK imports from domain services.
- Django app labels and migration dependencies that must not move without a
  dedicated migration plan.

Required backend compatibility snapshots:

```powershell
cd C:\academy\backend
python scripts\lint\refactor_boundary_snapshot.py
python manage.py check --settings apps.api.config.settings.test
python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test
pytest tests\test_worker_settings_drift.py -v --tb=short -x
$env:PYTHONPATH='C:\academy\backend'; $env:DJANGO_SETTINGS_MODULE='apps.api.config.settings.worker'; python tests\_worker_boot_check.py
```

Current baseline snapshot:

- `adapter_application_import`: 0
- `cross_domain_import`: 104
- `cross_domain_internal_import`: 645
- `domain_infra_import`: 84

### 3. Tenant Scope Guard

Current:

- tenant usage is widespread and mostly hand-authored.

Proposed:

- add a lightweight AST audit that reports ORM queries in tenant-scoped modules;
- start as report-only;
- make touched files strict;
- graduate repeated safe query patterns into managers/repositories.

The guard must fail closed: if it cannot understand a high-risk query, it should
ask for an explicit local exemption with a reason.

Existing helper:

```powershell
cd C:\academy\backend
python scripts\lint\check_id_domain_safety.py
```

### 4. Backend API Schema Guard

Current:

- no committed OpenAPI/type generation path was found.

Proposed:

- add backend schema generation using the least invasive DRF-compatible path;
- commit generated schema only after it is stable enough for frontend codegen;
- add a drift check that fails when generated schema/types are stale.

Initial scope:

- new or touched auth/account recovery endpoints;
- high-traffic admin/student endpoints after the first path is proven.
- drift check that compares committed schema/types with regenerated output.

### 5. Frontend Generated Types

Current:

- frontend has `typecheck`, but no generated backend API type source.

Proposed:

- add `openapi-typescript` or equivalent;
- generate to `frontend/src/shared/api/generated/`;
- prohibit hand-written entity response types in touched API modules once a
  generated type exists.

### 6. Frontend Boundary Guard

Current:

- eslint guards style and E2E wait patterns.
- `scripts/refactor-boundary-snapshot.mjs` captures app/domain import
  boundaries in baseline mode.
- `shared -> app_*` imports are at 0 as of 2026-05-22.
- role-app `@admin/*` imports remain at 37 and are the next frontend boundary
  cleanup target.

Proposed:

- add dependency-cruiser or eslint boundary rules in baseline mode;
- capture existing `@admin/*` imports from `app_teacher`, `app_student`,
  `shared`, `auth`, `landing`, and `core`;
- block `shared/*` from importing any app;
- block one app from importing another app's internals;
- block app domain internals from importing sibling domain internals unless they
  use a shared/contract surface.

Known frontend dependency policy items:

- decide whether to commit a `pnpm-lock.yaml` or formally keep CI's
  `--no-frozen-lockfile` behavior;
- align React runtime and `@types/react` major versions before using typecheck as
  a high-signal migration gate.

Snapshot command:

```powershell
cd C:\academy\frontend
pnpm refactor:inventory
```

### 7. Validation Harness

Before any package move, the commands in `validation-matrix.md` must have known
owners and expected outcomes.

Minimum local gate:

```powershell
git -C C:\academy\backend diff --check
git -C C:\academy\frontend diff --check
```

Backend and frontend functional gates are selected by touched surface.

## Baseline Mode Rules

- Existing violations are counted and documented.
- New violations in touched files are blocked.
- Compatibility exceptions must include a removal condition.
- Baseline counts should only go down after Phase 0.

## Phase 0 Exit Checklist

- [ ] Guardrail commands exist and are documented.
- [ ] Existing violation counts are captured.
- [ ] Touched-file strict mode is defined.
- [ ] Generated type path is proven or blocker is documented.
- [ ] Tenant/auth/account recovery validation path is defined.
- [ ] Docs distinguish current behavior from proposed architecture.
- [x] Frontend `shared` purity and `@admin` cross-app baseline are captured.
- [ ] Worker settings drift and migration dry-run are included in the backend
      gate.
