# 문서–코드 검증 (실제 코드 기준)

이 문서는 `docs_cursor` 문서가 **실제 코드와 일치하는지** 검증한 결과입니다.  
추측 없이 코드를 직접 확인한 뒤 반영했습니다.

---

## 검증일

- **2025-02-17**
- **2026-02-18** — 프로젝트 구조 파악, 문서–코드 일치 점검. 01/02/03 수정, base.py bypass에 `/api-auth/` 추가. 상세: [17-project-structure-and-doc-verification.md](17-project-structure-and-doc-verification.md).

---

## academy (백엔드)

| 문서 | 검증 내용 | 결과 |
|------|-----------|------|
| 01-core-tenant-program | resolver.py `resolve_tenant_from_request()` | **수정함** — 문서는 "Header fallback 금지"였으나 코드에는 중앙 API(api.hakwonplus.com) + `X-Tenant-Code` 헤더로 테넌트 결정하는 경로가 있음. 문서를 코드에 맞게 수정. |
| 01-core-tenant-program | TENANT_BYPASS_PATH_PREFIXES, MIDDLEWARE 순서 (base.py) | 일치 |
| 02-core-apis | urls.py, views.py, permissions.py | 일치 (TenantOwnerListView/DetailView는 urls에 있음, 문서는 "기타"로 안내) |
| 03-settings-env | CORS_ALLOW_HEADERS | **수정함** — `X-Tenant-Code` 추가 반영 |
| 07-staffs-api | staffs/urls.py, views.py 권한(IsPayrollManager) | 일치 |
| **staff 도메인 (스태프 폴더)** | **2025-02-17 검증** | **버그 수정함** — (1) GET `/staffs/<id>/summary/` 미구현 → summary action 추가. (2) WorkMonthLock create 검증 추가. (3) **1번 테넌트 오너 안 뜸**: 직원 목록 "대표" 행은 Staff가 아니라 TenantMembership(role=owner) 기반. 1번에 owner 멤버십 없으면 표시 안 됨. `ensure_tenant_owner` 관리 명령 추가, 07-staffs-api §5에 원인·해결 정리. |

---

## academyfront (프론트)

| 문서 | 검증 내용 | 결과 |
|------|-----------|------|
| 01-apps-routing | AppRouter.tsx RootRedirect, /dev/* 라우트 | 일치. **admin_app 내부 라우트 수정함** — AdminAppRouter가 branding → TenantListPage, branding/:tenantId, branding-legacy 등으로 변경됨. 문서 반영. |
| 02-shared-program-tenant | program/index.tsx Program 타입, tenant/config.ts, axios baseURL | 일치 |

---

## 규칙

- **코드가 진실**: 문서와 코드 불일치 시 코드 우선, 문서를 코드에 맞게 수정함.
- **추측 금지**: 이후 진행 시에도 실제 코드/설정을 확인한 뒤 반영할 것.
