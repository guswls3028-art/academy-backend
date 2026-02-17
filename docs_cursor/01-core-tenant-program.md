# Core · 테넌트 · Program (실제 코드 기준)

## 1. Core 책임 범위 (CORE_SEAL)

- **apps/core**: Tenant 식별 및 request 단위 resolve, TenantMembership(역할 SSOT), Program(tenant 1:1, 브랜딩/UI/기능토글), TenantDomain(host→tenant SSOT), 권한 계층만.
- **포함하지 않음**: 과금, 요금제, 워커 수/GPU/트래픽, 비즈니스 규칙(exams, results 등).

---

## 2. 테넌트 결정 (Tenant Resolution)

**경로**: `apps/core/tenant/resolver.py` — `resolve_tenant_from_request()`

- **우선순위 1 — 중앙 API + X-Tenant-Code**: `request.get_host()`가 `TENANT_HEADER_CODE_ALLOWED_HOSTS`(예: api.hakwonplus.com)에 포함되고, `X-Tenant-Code` 헤더가 있으면 → `core_repo.tenant_get_by_code(raw)` 로 테넌트 결정. (SPA가 tchul.com에서 열리지만 API는 api.hakwonplus.com으로 보낼 때 사용.)
- **우선순위 2 — Host**: `_normalize_host(request.get_host())` (포트 제거, 소문자) → `TenantDomain.host` 조회(`core_repo.tenant_domain_filter_by_host`) → `TenantDomain.tenant`.
- Query / Cookie / Env 기반 fallback **금지**.
- **에러**:
  - domain 없음 → bypass 경로가 아니면 `TenantResolutionError`, code=`tenant_invalid`, HTTP 404.
  - domain/tenant inactive → `TenantResolutionError`, code=`tenant_inactive`, HTTP 403.
  - 동일 host 복수 row → `TenantResolutionError`, code=`tenant_ambiguous`, HTTP 500.

---

## 3. Tenant bypass 경로

**위치**: `apps/api/config/settings/base.py` — `TENANT_BYPASS_PATH_PREFIXES`

```
/admin/
/api/v1/token/
/api/v1/token/refresh/
/internal/
/api/v1/internal/
/swagger
/redoc
```

이 prefix로 시작하는 경로만 `tenant=None` 허용. 그 외는 tenant resolve 실패 시 에러.

---

## 4. TenantDomain 규칙

- `TenantDomain.host`: DB 전역 unique.
- tenant 당 `is_primary=True` 1개만 (DB constraint).
- Resolve 대상: `TenantDomain.is_active == True` and `Tenant.is_active == True`.

---

## 5. Program 규칙

- Program ↔ Tenant **1:1**.
- **생성**: Tenant 생성 시 signal/bootstrap만. API GET 시 자동 생성(write-on-read) **금지**.
- **누락 시**: `ProgramView.get` → HTTP **404**, body `{ "detail": "program not initialized for tenant", "code": "program_missing", "tenant": "<tenant.code>" }` (apps/core/views.py).

---

## 6. 권한 계층 (apps/core/permissions.py)

| 클래스 | 용도 |
|--------|------|
| `TenantResolved` | tenant만 필요, 인증/멤버십 불필요. 로그인 전 bootstrap 등. |
| `TenantResolvedAndMember` | tenant + 인증 + 활성 TenantMembership. role 미해석. |
| `TenantResolvedAndStaff` | tenant + 인증 + role in (owner, admin, staff, teacher). |
| `TenantResolvedAndOwner` | tenant + 인증 + role == owner. admin_app 전용(tenant-branding, tenants API 등). |
| `IsAdminOrStaff` | Django admin/staff (테넌트 무관). |
| `IsSuperuserOnly` | 슈퍼유저만. 개발자 전용. |

- View 내부에서 role로 분기 금지. 프론트는 `/api/v1/core/me/` 의 `tenantRole` 만 신뢰.

---

## 7. 미들웨어 순서

**위치**: `apps/api/config/settings/base.py` — `MIDDLEWARE`

- `CorsMiddleware` → … → `apps.core.middleware.tenant.TenantMiddleware` → `CsrfViewMiddleware` → `AuthenticationMiddleware` → …

---

## 8. User username (테넌트 격리 · 레거시 없음)

- **테넌트 소속 User**: DB에 `username = t{tenant_id}_{로그인아이디}` 형식만 허용. **레거시(접두어 없는) 형식은 존재하면 안 됨.**
- **조회**: `core_repo.user_get_by_tenant_username(tenant, display_username)` → 내부적으로 `t{tenant_id}_{display}` 로만 조회. fallback 없음.
- **신규 생성**: `user_internal_username(tenant, display_username)` 사용 (core, staffs, ensure_tenant_owner 등).
- **기존 DB 정규화 (격리 마이그레이션 이후 1회)**: `python manage.py normalize_user_tenant_usernames --apply` — tenant가 있는데 username이 `t{id}_` 로 시작하지 않는 유저를 정규 형식으로 변경. **환경(로컬·운영)별로 레거시가 있었으면 1회씩 실행.**
