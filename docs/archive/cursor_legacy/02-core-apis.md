# Core API (URL · View · 권한 · DTO)

**기준**: `apps/core/urls.py`, `apps/core/views.py`, `apps/core/permissions.py`.  
Base path: `/api/v1/core/` (ROOT_URLCONF에서 prefix).

---

## 1. 인증·프로그램

| Method | Path | View | 권한 | 비고 |
|--------|------|------|------|------|
| GET | `me/` | MeView | IsAuthenticated, TenantResolvedAndMember | `tenantRole` 등 사용자+멤버십 정보. 프론트 권한 SSOT. |
| GET | `program/` | ProgramView | AllowAny, TenantResolved | tenant resolve만 필요. Program 1:1. |
| PATCH | `program/` | ProgramView | IsAuthenticated, TenantResolvedAndStaff | 해당 tenant의 Program만 수정. |

**Program GET 실패**: Program 없으면 404, `code`: `program_missing`, `tenant`: `<tenant.code>`.

---

## 2. Tenant Branding (dev_app 전용)

**권한**: `IsAuthenticated`, `TenantResolvedAndOwner` (owner만).

| Method | Path | View | 비고 |
|--------|------|------|------|
| GET | `tenant-branding/<int:tenant_id>/` | TenantBrandingView | Program.ui_config → DTO. |
| PATCH | `tenant-branding/<int:tenant_id>/` | TenantBrandingView | loginTitle, loginSubtitle, logoUrl, windowTitle, displayName. |
| POST | `tenant-branding/<int:tenant_id>/upload-logo/` | TenantBrandingUploadLogoView | multipart file → R2 academy-admin, ui_config.logo_url 저장. |

**응답 DTO (GET/PATCH)** — `_tenant_branding_dto(program)`:

- `tenantId`, `loginTitle`, `loginSubtitle`, `logoUrl`, `windowTitle`, `displayName`
- 백엔드 저장 키: `ui_config`: `login_title`, `login_subtitle`, `logo_url`, `window_title`; `display_name`은 Program.display_name.

---

## 3. Tenant 목록·상세·생성 (dev_app 전용)

**권한**: `IsAuthenticated`, `TenantResolvedAndOwner`.

| Method | Path | View | 비고 |
|--------|------|------|------|
| GET | `tenants/` | TenantListView | 목록: id, code, name, isActive, primaryDomain, domains. |
| GET | `tenants/<id>/` | TenantDetailView | 상세 + domains, hasProgram. |
| PATCH | `tenants/<id>/` | TenantDetailView | name, isActive 등. |
| POST | `tenants/create/` | TenantCreateView | code, name, domain(선택). Tenant+Program bootstrap. |

---

## 4. Tenant Owner 등록·목록 (dev_app 전용)

**권한**: `IsAuthenticated`, `TenantResolvedAndOwner`.

| Method | Path | View | 비고 |
|--------|------|------|------|
| POST | `tenants/<int:tenant_id>/owner/` | TenantOwnerView | username 필수. User 없으면 생성 시 password 필수. name, phone 선택. Owner 멤버십 생성/갱신. |
| GET | `tenants/<int:tenant_id>/owners/` | TenantOwnerListView | 해당 테넌트 owner 목록. |
| GET | `tenants/<int:tenant_id>/owners/<int:user_id>/` | TenantOwnerDetailView | owner 상세. |

**POST owner/ 응답**: `tenantId`, `tenantCode`, `userId`, `username`, `name`, `role`(owner).

---

## 5. 기타 core 라우트

- `profile/`, `profile/attendance/`, `profile/expenses/` (ViewSet)
- `job_progress/<str:job_id>/` (JobProgressView)

URL 전체 목록은 `apps/core/urls.py` + router 등록 참고.

---

## 6. Messaging API (support)

Messaging API는 `apps/support/messaging/` 에 있으며, `/api/v1/messaging/` prefix로 include됨.  
**전체 스펙**: [15-messaging-worker-and-message-flow.md](15-messaging-worker-and-message-flow.md) 참조.
