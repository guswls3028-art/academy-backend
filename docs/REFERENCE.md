# Academy Backend — 참조 문서 (단일 SSOT)

실제 코드·설정 기준만 기술. Cursor·개발 시 이 파일 + 루트 README + docs/배포.md 등만 보면 됨.

---

## 1. Core · 테넌트 · Program

**CORE_SEAL**: `apps/core/CORE_SEAL.md` — Core 봉인(헌법). tenant resolve fallback/Program write-on-read/TenantDomain primary 다중/ host 외 식별자/과금·워커 로직 추가 금지.

- **apps/core**: Tenant 식별·request 단위 resolve, TenantMembership(역할 SSOT), Program(tenant 1:1, 브랜딩/기능토글), TenantDomain(host→tenant SSOT), 권한 계층만.
- **테넌트 결정** (`apps/core/tenant/resolver.py`): (1) 중앙 API + X-Tenant-Code 헤더 (TENANT_HEADER_CODE_ALLOWED_HOSTS), (2) Host → TenantDomain.host. Query/Cookie/Env fallback 금지. 에러: tenant_invalid 404, tenant_inactive 403, tenant_ambiguous 500.
- **Tenant bypass**: `TENANT_BYPASS_PATH_PREFIXES` (base.py) — /admin/, /api/v1/token/, /api-auth/, /internal/, /swagger, /redoc 등.
- **TenantDomain**: host DB 전역 unique, tenant당 is_primary=True 1개, is_active + Tenant.is_active 만 resolve.
- **Program**: Tenant 1:1. Tenant 생성 시 signal/bootstrap만. GET 시 자동 생성 금지. 없으면 ProgramView 404, code program_missing.
- **권한** (permissions.py): TenantResolved, TenantResolvedAndMember, TenantResolvedAndStaff, TenantResolvedAndOwner(dev_app 전용), IsAdminOrStaff, IsSuperuserOnly. View 내부 role 분기 금지. 프론트 SSOT: GET /core/me/ tenantRole.
- **User username**: 테넌트 소속은 `t{tenant_id}_{로그인아이디}` 형식만. 레거시 없음. 조회/생성 core_repo 사용. 정규화: `manage.py normalize_user_tenant_usernames --apply`.

---

## 2. Core API (URL · View · 권한)

**기준**: `apps/core/urls.py`, `views.py`, `permissions.py`. Base path `/api/v1/core/`.

- **me/**: GET, MeView, TenantResolvedAndMember — tenantRole 등. **program/**: GET AllowAny+TenantResolved, PATCH TenantResolvedAndStaff.
- **Tenant Branding (dev_app 전용, TenantResolvedAndOwner)**: GET/PATCH `tenant-branding/<id>/`, POST `tenant-branding/<id>/upload-logo/`. DTO: tenantId, loginTitle, loginSubtitle, logoUrl, windowTitle, displayName (snake_case 저장).
- **Tenants (dev_app 전용)**: GET tenants/, GET tenants/<id>/, PATCH tenants/<id>/, POST tenants/create/. 목록/상세/생성.
- **Tenant Owner (dev_app 전용)**: POST tenants/<id>/owner/ (username 필수, password/name/phone), GET tenants/<id>/owners/, GET tenants/<id>/owners/<user_id>/.
- **Staff (staffs 도메인)**: GET/POST work-types, staff-work-types (POST body: staff, work_type_id, hourly_wage?). 상세: docs/archive/cursor_legacy/07-staffs-api.md.
- 기타: profile/, job_progress/, messaging(/api/v1/messaging/).

---

## 3. 설정 · 환경 변수

**위치**: `apps/api/config/settings/base.py`, `prod.py`.

- **CORS**: CORS_ALLOW_ALL_ORIGINS=False, CORS_ALLOWED_ORIGINS에 hakwonplus, limglish, tchul, ymath (https+www), academy-frontend.pages.dev, localhost 5173/5174, dev-web. CORS_ALLOW_HEADERS + X-Tenant-Code 등.
- **CSRF_TRUSTED_ORIGINS**: 동일 오리진. prod는 localhost/trycloudflare 제외 가능.
- **ALLOWED_HOSTS**: api.hakwonplus.com, 각 도메인, .pages.dev, .trycloudflare.com 등. 새 도메인 시 ALLOWED_HOSTS + CORS + CSRF 반영.
- **DB**: PostgreSQL, ENV DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_CONN_MAX_AGE.
- **기타**: SECRET_KEY, DEBUG, AWS_REGION, R2/CDN, Solapi(SOLAPI_API_KEY, SOLAPI_API_SECRET, SOLAPI_SENDER), MESSAGING_SQS_QUEUE_NAME. USE_X_FORWARDED_HOST, SECURE_PROXY_SSL_HEADER.

---

## 4. 배포 요약

**상세**: `docs/배포.md`. 리전 ap-northeast-2(서울).

- 순서: RDS → SQS → IAM → 보안그룹 → EC2 API(Messaging/Video/AI). Video 4GB+100GB EBS, /mnt/transcode.
- Docker: docker/Dockerfile.base, docker/api/Dockerfile, docker/messaging-worker/, docker/video-worker/, docker/ai-worker-cpu/. ECR 푸시 후 EC2에서 pull, .env, migrate, health.
- 환경: `scripts/prepare_deploy_env.py -o .env.deploy`. EC2 API 자동 배포: scripts/auto_deploy_cron_on.sh, auto_deploy_cron_off.sh (cron 1분마다 origin/main 감지).

---

## 5. Conventions

- **코드가 진실**: 문서와 다르면 코드 따르고 문서 수정. 추측 금지.
- **Core 봉인**: CORE_SEAL.md 위반 금지. 확장은 TenantDomain 운영 필드, Program feature_flags/ui_config, core 외부 정책만.
- **API**: View 권한은 permissions 클래스만. role 분기 금지.
- **테넌트**: Host 기반만. 새 도메인 시 TenantDomain + ALLOWED_HOSTS/CORS/CSRF.

---

## 6. 프론트·인프라 계약

- **CORS/도메인**: 새 프론트 도메인 사용 시 CORS_ALLOWED_ORIGINS, CSRF_TRUSTED_ORIGINS 추가. 프론트 구현 사실: **academyfront/docs/REFERENCE.md**.
- **엑셀 파싱**: `application/services/excel_parsing_service.py`. parse_student_excel_file 결과 비어 있으면 `ValueError("등록할 학생 데이터가 없습니다.")` — 프론트와 동일 메시지. 강의 수강생 일괄 등록용.
