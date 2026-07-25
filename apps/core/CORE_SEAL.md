# PATH: apps/core/CORE_SEAL.md
# CORE 봉인 문서 (SSOT / Enterprise Lock / FINAL)

본 문서는 **apps/core 도메인**을 “봉인(LOCK)”하기 위한 최종 헌법이다.  
이 문서 이후의 core 변경은 **리팩터링이 아니라 운영 사고**로 간주한다.

본 봉인은 “프리미엄 단일 운영” 상태에서 즉시 출시 가능하며,  
향후 요금제/워커/트래픽 확장은 **core 외부 도메인**에서만 수행한다.

---

## 0. 봉인 선언 (Final)

- apps/core 는 **플랫폼의 헌법 계층**이다.
- 기능 추가, 조건 분기, 임시 우회는 **전면 금지**한다.
- core 는 “확장되는 곳”이 아니라 “다른 도메인이 믿고 올라서는 곳”이다.

---

## 1. Core의 책임 범위 (Hard Boundary)

Core는 **아래 항목만** 책임진다.

1. Tenant(학원) 식별 및 request 단위 resolve
2. TenantMembership (tenant 내 사용자 역할 SSOT)
3. Program (tenant 1:1, 브랜딩/로그인/UI/기능토글 SSOT)
4. TenantDomain (host → tenant resolve SSOT)
5. 최소 권한 계층 (apps/core/permissions.py)
   - TenantResolved
   - TenantResolvedAndMember
   - TenantResolvedAndStaff
   - TenantResolvedAndOwner (dev_app 전용)
   - IsAdminOrStaff, IsSuperuserOnly (Django admin/개발자용)

❌ Core는 다음을 **절대 포함하지 않는다**:
- 과금 로직
- 요금제 판단
- 워커 수 / GPU / 트래픽 정책
- 비즈니스 규칙(exams, results, clinic 등)

---

## 2. 테넌트 결정 헌법 (Tenant Resolution Constitution)

### 2.1 단일 진실 원칙 (SSOT)

Tenant 결정 경로는 **오직 하나**만 허용한다.

request.get_host() (apps/core/tenant/resolver.py)
→ _normalize_host (포트 제거, 소문자)
→ TenantDomain.host 조회 (core_repo.tenant_domain_filter_by_host)
→ TenantDomain.tenant

- Header / Query / Cookie / Env 기반 fallback ❌
- 테스트 편의용 우회 ❌

---

### 2.2 bypass 규칙

아래 경로만 tenant=None 허용 (apps/api/config/settings/base.py):

```
TENANT_BYPASS_PATH_PREFIXES = [
    "/admin/",
    "/api/v1/token/",
    "/api/v1/token/refresh/",
    "/internal/",
    "/api/v1/internal/",
    "/swagger",
    "/redoc",
]
```

의도:
- 로그인 전 bootstrap
- 헬스체크
- 내부 관리

그 외 모든 요청은 tenant resolve 실패 시 **즉시 에러**.

---

## 3. TenantDomain 규칙 (Domain SSOT)

### 3.1 host 전역 유니크

- `TenantDomain.host` 는 **DB 전역 unique**
- 하나의 host는 하나의 tenant에만 귀속

---

### 3.2 primary 규칙 (봉인)

- tenant 당 `is_primary=True` 는 **최대 1개**
- DB constraint 로 강제

의미:
- 대표 도메인은 하나
- 커스텀 도메인 추가는 가능
- 대표 도메인 다중 허용 ❌

---

### 3.3 active 규칙

Resolve 대상 조건:

TenantDomain.is_active == True
AND
Tenant.is_active == True

yaml
코드 복사

- 비활성 상태 접근 시:
  - 403
  - code = tenant_inactive

---

## 4. Program 규칙 (Tenant 1:1 SSOT)

### 4.1 Program의 정체성

- Program == “원장 개인 프로그램”
- Tenant와 **1:1**
- 모든 UI / 로그인 / 기능 분기는 Program 기준

---

### 4.2 생성 책임 단일화

Program row 생성은 다음 시점에서만 허용:

- Tenant 생성 시 bootstrap
  - signals
  - migration bootstrap

❌ API GET 시 자동 생성(write-on-read) 금지  
❌ 프론트 접근을 이유로 생성 금지

---

### 4.3 누락은 운영 사고

- Program 누락 상태는 **정상 상태가 아님**
- 반드시 다음으로 실패한다 (apps/core/views.py ProgramView.get):

HTTP 404
body: { "detail": "program not initialized for tenant", "code": "program_missing", "tenant": "<tenant.code>" }

자동 생성은 장애를 숨기는 행위로 간주한다.

---

## 5. Permission / Role SSOT

### 5.1 단일 신뢰 원천

- 프론트는 role을 추론하지 않는다.
- `/api/v1/core/me/` 응답의 `tenantRole` 만 신뢰한다.
- 모든 권한 해석은 Permission class에서만 수행한다.

---

### 5.2 허용 Permission 계층 (실제 코드 기준)

- TenantResolved (apps/core/permissions.py)
- TenantResolvedAndMember
- TenantResolvedAndStaff
- TenantResolvedAndOwner (role=owner 전용, tenant-branding/tenants API 등)

❌ View 내부 if role 분기 금지  
❌ 프론트 조건문 기반 권한 처리 금지

---

## 6. 요금제 / 워커 정책에 대한 헌법적 위치

### 6.1 현재 운영 상태

- `all` 단일 요금제 운영
- 모든 tenant는 기능 제한 없이 전체 기능 사용
- 가격 계약은 공급가 145,000원 + 부가가치세 14,000원 = 총 159,000원

---

### 6.2 기능 등급 확장 원칙

- Core의 `Program`은 단일 결제 계약과 구독 상태만 보관한다.
- 기능을 요금제별로 나누는 분기는 두지 않는다.
- 운영 모드가 필요한 기능은 `Program.feature_flags` 또는 해당 도메인이 소유하되
  결제 등급으로 해석하지 않는다.

Core는 기능 허용 여부를 요금제로 판단하지 않는다.

---

## 7. 변경 금지 목록 (Hard Lock)

다음 행위는 **봉인 위반**이다.

- tenant resolve fallback 추가
- Program write-on-read 부활
- TenantDomain primary 다중 허용
- host 외 식별자 기반 멀티테넌트
- core에 과금/요금제/워커 로직 추가

---

## 8. 허용되는 확장 (명시적 허용)

다음은 봉인 위반이 아니다.

- TenantDomain 운영 필드 추가
  - 예: verified_at, ssl_status
- Program.feature_flags / ui_config 확장
- TenantMembership role 추가
- core 외부 도메인에서의 정책 확장

---

## 9. 최종 결론 (Seal)

apps/core는 **플랫폼의 기반 헌법**이다.  
이 문서 채택 이후, apps/core는 **봉인(LOCK)** 상태로 간주한다.

이후 개발은:
- 더 빠르게
- 더 안전하게
- 더 단순하게

진행할 수 있다.

---

## 🔒 SEAL STATUS

- Status: LOCKED
- Change Policy: Bugfix only
- Owner: Platform Core
- Violation = Production Incident
