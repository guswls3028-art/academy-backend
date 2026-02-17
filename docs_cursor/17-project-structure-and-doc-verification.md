# 프로젝트 구조 · 문서–코드 검증 (2026-02-18)

**목적**: 프로젝트 구조 파악, `docs_cursor` 문서와 실제 코드 일치 여부 확인 및 불일치 수정.

---

## 1. 프로젝트 구조

### 1.1 academy (백엔드) — 루트

| 디렉터리/파일 | 용도 |
|---------------|------|
| `apps/` | Django 앱 (api, core, domains, support, worker 등) |
| `academy/` | 프로젝트 패키지 (adapters, config 등) |
| `docs/`, `docs_cursor/` | 문서 (SSOT, Cursor용) |
| `infra/` | 인프라 (worker ASG, Lambda 등) |
| `docker/` | Docker/Compose |
| `scripts/`, `tools/` | 배포·유틸 스크립트 |
| `requirements/` | pip 의존성 (base, worker-messaging 등) |
| `supporting/` | 외부 라이브러리 (solapi-python 등) |

### 1.2 academy — apps/

| 디렉터리 | 용도 |
|----------|------|
| `api/` | 설정(config), URL 루트, v1 라우팅, common(health, JWT) |
| `core/` | 테넌트·Program·권한·Me·TenantBranding·Tenant CRUD·Owner (core 봉인: CORE_SEAL.md) |
| `domains/` | 도메인별 앱 (students, staffs, teachers, clinic, results, homework, community, assets, inventory, enrollment, exams, progress, submissions, lectures, attendance, ai, parents, schedule, student_app 등) |
| `support/` | 메시징(messaging), 비디오(video) 등 공용 지원 |
| `worker/` | Messaging/Video/AI 워커 (SQS 등) |
| `infrastructure/` | 인프라 연동 |
| `shared/` | 공용 코드 |

### 1.3 academy — API URL 구조 (apps.api.config.urls + apps.api.v1.urls)

- **루트**: `health`, `admin/`, `api/v1/token/`, `api/v1/token/refresh/`, `api-auth/`, `api/v1/`
- **api/v1/** 아래: `lectures/`, `students/`, `enrollments/`, `staffs/`, `teachers/`, `results/`, `homework/`, `clinic/`, `assets/`, `storage/`, `community/`, `messaging/`, `core/`, `media/`, `jobs/`, `internal/ai/`, `internal/`, `student/` 등

### 1.4 academyfront (프론트엔드) — 루트

| 디렉터리/파일 | 용도 |
|---------------|------|
| `src/` | 소스 (features, shared, app 라우팅 등) |
| `public/` | 정적 자산 |
| `docs/`, `docs_cursor/` | 문서 |
| `config/` | 빌드 설정 등 |

### 1.5 academyfront — src/

- **features/** : 도메인별 기능 (messages, profile 등)
- **shared/** : api(axios), ui, tenant, program 등
- API base: `VITE_API_BASE_URL` → `/api/v1` prefix (axios), JWT Bearer + X-Tenant-Code

---

## 2. 문서–코드 검증 결과 (2026-02-18)

### 2.1 적용한 수정 (문서 ↔ 코드 동기화)

| 문서 | 수정 내용 |
|------|-----------|
| **01-core-tenant-program.md** | `TENANT_BYPASS_PATH_PREFIXES` 목록에 `/api-auth/` 추가 (코드와 동일하게 반영). |
| **03-settings-env.md** | 동일하게 `TENANT_BYPASS_PATH_PREFIXES`에 `/api-auth/` 추가. |
| **02-core-apis.md** | §4 Tenant Owner: GET `tenants/<id>/owners/`, GET `tenants/<id>/owners/<user_id>/` (TenantOwnerListView, TenantOwnerDetailView) 추가. |
| **base.py (설정)** | `TENANT_BYPASS_PATH_PREFIXES`에 `/api-auth/` 추가 (DRF Browsable API 로그인 시 tenant 불필요). |

### 2.2 일치 확인된 항목

| 문서 | 검증 항목 | 결과 |
|------|-----------|------|
| 01-core-tenant-program | 테넌트 결정 우선순위(X-Tenant-Code / Host), bypass 경로, 미들웨어 순서 | ✓ (수정 반영 후 일치) |
| 02-core-apis | core URL·View·권한 (me/, program/, tenant-branding, tenants, owner), messaging 참조(15번) | ✓ (owner 목록/상세 보완 반영) |
| 03-settings-env | CORS_ALLOWED_ORIGINS, CORS_ALLOW_HEADERS(X-Tenant-Code), bypass, Messaging Worker ENV | ✓ |
| 15-messaging-worker-and-message-flow | messaging URL 전부 (info, verify-sender, charge, log, send, templates, auto-send 등) | ✓ |
| 16-verification-report-0218 | 백엔드–프론트 API 경로·Method·Payload 정합성 | ✓ |
| 07-staffs-api | staffs URL·ViewSet·권한(IsPayrollManager), POST staff-work-types body, summary, owner 표시 | ✓ (코드 기준 일치) |

### 2.3 참고 사항 (동작 변경 없음)

- **REST_FRAMEWORK**: `SessionAuthentication` 추가됨 (브라우저 API 로그인용). 문서에는 기본 인증 클래스 목록이 없어서 별도 수정 없음.
- **core urls**: `profile/`, `profile/attendance/`, `profile/expenses/`, `job_progress/<job_id>/` 등은 02 문서 §5 “기타 core 라우트”로 안내되어 있음.

---

## 3. 문서 읽는 순서 (docs_cursor README 기준)

- **00-verification.md**: 문서–코드 검증 요약.
- **01** ~ **05**: core, API, 설정, 배포, 규칙.
- **06** ~ **07**: 프론트/인프라·엑셀, staffs API.
- **08**, **10**, **11**: 워커 배포·명령어·self-stop.
- **12**, **13**: 엑셀 파싱.
- **14**, **15**: 솔라피·메시징 워커/API.
- **16**: 검증 보고서 (0218).
- **17**: 본 문서 (프로젝트 구조·문서 검증).

---

## 4. 규칙

- **코드가 진실**: 문서와 코드 불일치 시 코드 우선, 문서를 코드에 맞게 수정함.
- **추측 금지**: 문서에 없는 동작은 코드/설정을 직접 확인한 뒤 반영할 것.
