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
- `scripts/lint/refactor_boundary_snapshot.py --strict-touched` exists for
  touched-file strict mode. With no extra options it inspects the current
  working tree; with `--base-ref <ref>` it inspects `ref...HEAD`; with
  `--touched-file <path>` it inspects explicit paths.
- Strict touched mode fails on direct internal cross-domain imports,
  domain infrastructure imports, kernel Django imports, adapter reverse imports,
  and parse/decode errors. Public cross-domain selector imports are reported and
  baseline-capped, but are not strict failures.
- `scripts/lint/refactor_boundary_snapshot.py --enforce-baseline` fails when
  any current boundary finding count exceeds the committed baseline.
- Backend deploy workflow `run-lint` runs the touched-file guard against the push
  range, so new main pushes cannot touch scanned boundary-risk files without
  clearing their boundary findings. It also runs the baseline guard so total
  boundary debt cannot increase silently in untouched files.
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
python scripts\lint\refactor_boundary_snapshot.py --enforce-baseline
python scripts\lint\refactor_boundary_snapshot.py --strict-touched
python manage.py check --settings apps.api.config.settings.test
python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test
pytest tests\test_worker_settings_drift.py -v --tb=short -x
$env:PYTHONPATH='C:\academy\backend'; $env:DJANGO_SETTINGS_MODULE='apps.api.config.settings.worker'; python tests\_worker_boot_check.py
```

Current baseline snapshot (latest local run: 2026-06-23 KST):

- `adapter_application_import`: 0
- `cross_domain_import`: 117
- `cross_domain_internal_import`: 605
- `domain_infra_import`: 82
- `check_id_domain_safety.py`: 38 warning(s), 0 error(s)
  - `UNORDERED_FIRST`: 10
  - `SILENT_FALLBACK`: 0
  - Remaining warnings are 28 `[ALLOWED]` integer-FK candidates plus
    `UNORDERED_FIRST` instances in files that require boundary extraction before
    strict-touched cleanup can be committed safely.

Current frontend baseline snapshot (latest local run: 2026-06-23 KST):

- `api_generated_dir_present`: false
- `same_app_domain_import`: 146
- `large_frontend_file`: 34
- `local_format_defs`: 121
- `status_map_defs`: 35
- `query_key_literals`: 901
- `inline_style_objects`: 3806
- `api_response_type_defs`: 102
- `e2e_wait_for_timeout`: 34
- `pnpm refactor:budget`: passed against `scripts/refactor-budget-baseline.json`

Interpretation note:

- Public selector/service imports can raise `cross_domain_import` while replacing
  direct model/view/service reach-through. The stricter quality signal during
  this transition is `cross_domain_internal_import`, plus touched-file review
  that verifies the new import is a documented public boundary.

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
- role-app `@admin/*` imports are at 0 as of 2026-05-22.

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
- Internal/direct baseline counts should only go down after Phase 0. Public
  selector/service imports may rise temporarily when they replace internal
  model/view/service imports and are documented as canonical boundaries.

## Phase 0 Exit Checklist

- [x] Backend boundary guardrail command exists, is documented, and is wired into
      the backend deploy lint gate.
- [x] Existing violation counts are captured.
- [x] Touched-file strict mode is defined for backend boundary findings.
- [ ] Generated type path is proven or blocker is documented.
- [ ] Tenant/auth/account recovery validation path is defined.
- [ ] Docs distinguish current behavior from proposed architecture.
- [x] Frontend `shared` purity and `@admin` cross-app baseline are captured.
- [ ] Worker settings drift and migration dry-run are included in the backend
      gate.
