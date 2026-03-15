# Full-Stack Audit — Phase 0: Safety Baseline

**Status:** [IN REVIEW]  
**No edits performed.** This document records confirmed facts from code inspection.

---

## 1. Confirmed Repository Map

| Component | Location | Purpose |
|-----------|----------|---------|
| **Backend** | `backend/` | Django API, workers, hexagonal (`academy/`), libs |
| **Frontend** | `frontend/` | React 18 + Vite 6 SPA (admin + student app) |
| **Docs** | `docs/`, `backend/docs/` | Requirements, SSOT, operations, reports |
| **Cursor/Claude** | `.cursor/`, `.claude/` | Rules, task templates, architecture |
| **Infra/Scripts** | `backend/scripts/v1/`, `backend/scripts/archive/` | Deploy, verify, bootstrap, legacy |
| **CI** | `backend/.github/workflows/` | Build, push ECR, API refresh, video deploy |

### Backend apps (Django)

- **core** — Tenant, User, Program, TenantDomain, TenantMembership, Attendance, Expense
- **domains** — students, teachers, staffs, parents, lectures, attendance, enrollment, schedule, community, exams, homework, submissions, results, homework_results, clinic, progress, ai, assets, inventory, **student_app**
- **support** — video, messaging
- **api** — config (settings, urls, wsgi), common (middleware, auth, health)
- **worker** — video_worker, messaging_worker, ai_worker (SQS/Batch entrypoints)

### Frontend structure

- **src/app/** — AppRouter, AdminRouter, ProtectedRoute
- **src/features/** — auth, dashboard, students, staff, lectures, exams, results, clinic, messages, videos, profile, admin-notifications, etc.
- **src/student/** — StudentRouter, domains (dashboard, video, qna, notifications, clinic, sessions, notices), shared
- **src/shared/** — api (axios), ui, utils, program, tenant

---

## 2. Confirmed Runtime Map

| Entrypoint | Path | Runtime |
|------------|------|---------|
| Django CLI | `backend/manage.py` | Python 3.11, DJANGO_SETTINGS_MODULE |
| WSGI | `backend/apps/api/config/wsgi.py` | gunicorn + gevent (Dockerfile) |
| API v1 | `backend/apps/api/config/urls.py` → `apps.api.v1.urls` | All tenant-scoped API + student app |
| Frontend dev | `frontend/` — `pnpm run dev` (vite) | Node, port 5174, /api → :8000 |
| Frontend build | `vite build` + ensure-spa-mode.js | Static SPA |
| Video worker | `apps.worker.video_worker.batch_entrypoint` → batch_main | Python 3.11, AWS Batch |
| Messaging worker | `apps.worker.messaging_worker.sqs_main` | Python 3.11, SQS |
| AI worker | `apps.worker.ai_worker.sqs_main_cpu` / sqs_main_gpu | Python 3.11, SQS |
| Deploy | `backend/scripts/v1/deploy.ps1` | PowerShell, ECR, ASG refresh |
| CI | `.github/workflows/v1-build-and-push-latest.yml`, video_batch_deploy.yml | OIDC, ECR, deploy.ps1 |

**Framework versions (from code):**

- Backend: Python 3.11 (Dockerfile.base), Django (requirements.txt)
- Frontend: pnpm 9.15.0, React ^18.3.1, Vite ^6.1.0, TypeScript ^5.9.3, Tailwind 4.x

---

## 3. Confirmed Tenant Isolation Boundary Map

### 3.1 Tenant resolution (SSOT)

| File | Role |
|------|------|
| `apps/core/middleware/tenant.py` | Sets `request.tenant` from resolver; clears context in `finally`. Bypass paths: `/health`, `/healthz`, `/readyz`. |
| `apps/core/tenant/resolver.py` | 1) Host in TENANT_HEADER_CODE_ALLOWED_HOSTS + X-Tenant-Code → tenant by code. 2) Host → TenantDomain.host → Tenant. 3) Host not in DB (e.g. ALB) + allowed host or *.elb.amazonaws.com + X-Tenant-Code → tenant by code. No fallback; raises TenantResolutionError if unresolved (except bypass). |
| `apps/api/config/settings/base.py` | TENANT_BYPASS_PATH_PREFIXES: `/admin/`, `/api/v1/token/`, `/api/v1/token/refresh/`, `/api-auth/`, `/internal/`, `/api/v1/internal/`, `/swagger`, `/redoc`. TENANT_HEADER_CODE_ALLOWED_HOSTS: api.hakwonplus.com, localhost, 127.0.0.1. |
| `apps/api/common/auth_jwt.py` | Login: tenant from X-Tenant-Code or body tenant_code; user from `user_get_by_tenant_username(tenant, username)`. No default tenant. |

**Confirmed:** No default tenant, no tenant fallback in resolver. Bypass paths do not set tenant.

### 3.2 Permission classes (tenant + auth)

| Class | File | Requirement |
|-------|------|-------------|
| TenantResolved | core/permissions.py | request.tenant set |
| TenantResolvedAndMember | core/permissions.py | tenant + authenticated + active TenantMembership |
| TenantResolvedAndStaff | core/permissions.py | tenant + (is_superuser or is_staff or membership in owner/admin/staff/teacher) |
| TenantResolvedAndOwner | core/permissions.py | tenant + membership role=owner |
| IsStudent | core/permissions.py | user.student_profile (student app) |

### 3.3 Domains with explicit tenant scoping (from grep)

- **students** — get_queryset uses repo/tenant filter; User lookup with tenant
- **staffs** — get_queryset filter tenant; WorkRecord filter staff.tenant
- **clinic** — Session, SessionParticipant, Test get_queryset filter(tenant=tenant)
- **community** — Post, PostReply, PostTemplate, BlockType, ScopeNode filter(tenant=tenant); selectors take tenant
- **lectures** — lecture__tenant=self.request.tenant
- **attendance** — filter(tenant=tenant)
- **enrollment** — filter(tenant=tenant)
- **homework** — HomeworkPolicy filter(tenant=tenant)
- **results** — ClinicTargetService list_admin_targets(tenant) → session__lecture__tenant=tenant (recent fix)
- **inventory** — repo.inventory_folder_filter(tenant, ...), inventory_file_filter(tenant, ...)
- **student_app** — dashboard tenant_info from request.tenant; media views filter Enrollment/Lecture by tenant
- **messaging** — views use request.tenant for templates, configs, credits, send
- **video** — progress_views filter by tenant; playback/session scope via lecture tenant

### 3.4 Worker / internal paths

- **Internal API** — `/internal/`, `/api/v1/internal/` are in TENANT_BYPASS_PATH_PREFIXES; no request.tenant.
- **Video worker** — Batch jobs receive payload; tenant comes from job payload / Video → session → lecture → tenant_id (code must never mix tenants in one job).
- **Messaging worker** — SQS message carries tenant_id; send path must use that tenant only (policy.py, OWNER_TENANT_ID for SMS).
- **AI worker** — Job payload carries tenant/context; must be single-tenant per job.

**Risks requiring extra caution before edits:**

1. **Settings comment vs resolver:** base.py says "Tenant resolution is **Host-based only**" but resolver uses X-Tenant-Code for allowed hosts and ALB; doc/code mismatch (cosmetic).
2. **Any ViewSet or service that does not receive request or tenant:** must be verified to get tenant from context or explicit argument (no unscoped query).
3. **Workers:** every message/job must carry tenant_id and use it exclusively; no cross-tenant batch.
4. **Bypass paths:** anything under /internal/ or /admin/ does not have request.tenant; views there must not rely on tenant or must obtain it from payload/header explicitly.

---

## 4. Audit Execution Plan (Phases 1–7)

- **Phase 1:** Backend — settings, urls, middleware, auth, each domain’s views/serializers/services/repos, models, migrations, workers, queues, validation, permissions, tenant scoping. Output: findings by subsystem, severity, code refs, fix plan; then incremental fixes + tests/lint.
- **Phase 2:** Frontend — routes, features, API client, hooks, forms, tenant/role UI, loading/error states. Output: findings by module, severity, mismatch list, fix plan; then incremental fixes + typecheck/lint/build.
- **Phase 3:** API contract reconciliation — for each frontend-used API: backend route, request/response, auth, tenant, permissions; compare with frontend; table of mismatches and fixes.
- **Phase 4:** Pipelines — dev/run scripts, Docker, CI, deploy, workers; verify executable, env, no stale/duplicate/unsafe paths.
- **Phase 5:** Test/validation gaps — tenant isolation tests, permission tests, API contract tests, CI gates.
- **Phase 6:** Safe repair execution — batched changes, evidence, risk, verification.
- **Phase 7:** Final report — architecture, tenant map, findings, contracts, pipelines, risks, unknowns, next steps.

---

## 5. Subsystem Checklist (for Phases 1–2)

Backend: [NOT STARTED] settings — [NOT STARTED] urls — [NOT STARTED] middleware — [NOT STARTED] auth — [NOT STARTED] core — [NOT STARTED] students — [NOT STARTED] staffs — [NOT STARTED] clinic — [NOT STARTED] community — [NOT STARTED] lectures — [NOT STARTED] enrollment — [NOT STARTED] results — [NOT STARTED] messaging — [NOT STARTED] video — [NOT STARTED] student_app — [NOT STARTED] others — [NOT STARTED] workers.

Frontend: [NOT STARTED] app router — [NOT STARTED] features (auth, students, staff, messages, clinic, …) — [NOT STARTED] student app — [NOT STARTED] shared API/tenant — [NOT STARTED].
