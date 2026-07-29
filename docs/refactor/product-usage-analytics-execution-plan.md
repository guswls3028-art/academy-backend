# 역할별 기능 사용 분석 실행 계획서

> Historical execution snapshot (2026-07-29). 최초 구현·배포 계획과 검증
> 근거를 보존한 문서이며 현재 실행 절차가 아니다. 현재 제품·API 계약은
> [제품 사용 분석](../domain/product-usage-analytics.md), DB 구조 판단 기준은
> [DB 확장·테넌트 분리](../infrastructure/database-scaling-and-tenant-isolation.md),
> 남은 작업은
> [제품 사용 분석 잔여 작업](product-usage-analytics-remaining-work.md)을
> 따른다.

**Status:** [HISTORICAL EXECUTION SNAPSHOT]

**작성일:** 2026-07-29 KST

**제품 계약:** [역할별 기능 사용 분석 기획서](product-usage-analytics-product-spec.md)

**범위:** `C:\academy\backend`, `C:\academy\frontend`, 사전검증·운영 집계 경로

**기록 시점 상태:** 아래 내용은 2026-07-29 최초 구현·배포 계획의
스냅샷이다. 현재 남은 작업은 상단의 잔여 작업 문서만 갱신한다.

### 현재 판정

- **repo-confirmed:** 수집·집계·보존·운영 조회 API, 기능/라우트 레지스트리, 비차단 수집 클라이언트, 대표 완료 퍼널, 운영 대시보드, API tenant DB usage telemetry
- **needs-manual-validation:** 실제 PostgreSQL volume/query plan, worker telemetry, overhead 부하 시험, isolated preprod, 운영 HMAC secret과 파일럿
- **intentionally unchanged:** 운영 RDS, Multi-AZ, read replica, DB router, tenant schema/database, 기존 메뉴·CTA 순서

## 1. 실행 목표

하나의 작은 수직 슬라이스로 다음 경로를 완성한다.

```text
등록된 화면·CTA
  → 비차단 클라이언트 수집
  → 인증·테넌트·역할 확정
  → 익명 이벤트 저장
  → 역할별 집계
  → 플랫폼 운영 대시보드
  → 28일 기준선
  → CTA·노출 위치 개선 실험
```

첫 배포는 관측만 추가한다. 기존 메뉴 순서, CTA 문구, 업무 동작은 바꾸지 않는다.

## 2. 작업 가정과 제약

- 현재 `apps.core`가 테넌트, 멤버십, 프로그램 기능 플래그와 `/dev` 운영 API를 소유하므로 첫 구현도 이 경계 안에 둔다.
- 별도 Django app이나 외부 분석 SDK를 추가하지 않는다.
- `apps.support.analytics`는 시험·성적 분석이므로 제품 행동 이벤트를 그곳에 섞지 않는다.
- PostgreSQL 원본 이벤트는 30일만 보존하고 일별 익명 사용자 집계로 장기 추세를 유지한다.
- 분석 수집은 fail-open이다. 분석 실패가 사용자 업무를 실패시키면 안 된다.
- 데이터 마이그레이션은 새 테이블만 추가하는 expand 변경으로 만든다.
- 현재 API·일반 워커는 하나의 `default` DB만 사용한다. 승인된 게이트 전에는 `DATABASE_ROUTERS`나 테넌트별 연결을 추가하지 않는다.
- DB 고가용성, 읽기 확장, 테넌트 격리는 서로 다른 작업으로 계획하고 한 배포에 묶지 않는다.
- 생산 배포와 기능 플래그 활성화는 별도 명시적 요청이 있을 때만 수행한다.
- 현재 backend와 frontend에 존재하는 다른 작업자의 변경은 건드리지 않는다.

## 3. 전체 단계와 예상 작업량

예상치는 한 명의 개발자가 기존 코드 이해를 마친 뒤의 순수 개발일 기준이다. 데이터 기준선 28일은 개발일에 포함하지 않는다.

| 단계 | 결과 | 예상 |
|---|---|---:|
| E0 | 이벤트 계약·기능 레지스트리 고정 | 0.5~1일 |
| E1 | 백엔드 수집·저장·권한 | 1.5~2.5일 |
| E2 | 프론트 수집 모듈·화면 방문 | 1.5~2.5일 |
| E3 | 역할별 CTA·주요 업무 계측 | 3~5일 |
| E4 | 운영 분석 API·대시보드 | 2~3일 |
| E5 | 집계·보존·운영 품질 | 1~2일 |
| E6 | 사전검증·파일럿·전체 활성화 | 1~2일 + 관찰 기간 |
| E7A | DB 가용성 준비·테넌트별 부하 계측 | 2~4일 + 28일 기준선 |

MVP 개발 합계는 약 10~16 개발일이다. E3의 계측 화면 수에 따라 가장 크게 달라진다.

E7A는 MVP와 병행 가능한 운영 준비 트랙이며 멀티 DB 구현이 아니다. read replica와 tenant data plane 구현은 게이트가 통과된 뒤 별도 epic으로 산정한다.

## 4. E0 — 계약과 레지스트리

### 4.1 목표

코드를 쓰기 전에 기능 ID, 화면 ID, CTA ID와 성공 행동을 고정한다. 의미가 바뀌는 ID는 과거 데이터를 무효화하므로 이 단계가 선행되어야 한다.

### 4.2 신규 프론트 파일

```text
frontend/src/shared/productAnalytics/
  types.ts
  featureRegistry.ts
  routeRegistry.ts
  featureRegistry.test.ts 또는 scripts 기반 검증
```

현재 프론트엔드에 단위 테스트 러너가 없으므로 레지스트리 정적 검증은 우선 Node 스크립트로 둔다.

```text
frontend/scripts/verify-product-analytics-registry.mjs
frontend/scripts/tests/product-analytics-registry.test.mjs
```

### 4.3 레지스트리 계약

`featureRegistry.ts`:

- `featureId`
- 한국어 label
- domain
- 허용 역할
- 기대 빈도
- 전략 중요도
- 대표 성공 행동
- 기능 플래그
- active/retired

`routeRegistry.ts`:

- route matcher
- route template
- `featureId`
- `screenId`
- `surface`
- 허용 역할

동적 숫자와 UUID는 어떤 경우에도 `route_template`에 포함하지 않는다.

### 4.4 최초 인벤토리 소스

다음 실행 코드를 대조해 누락 없는 라우트 목록을 만든다.

- [AdminRouter.tsx](../../../frontend/src/app_admin/app/AdminRouter.tsx)
- [TeacherRouter.tsx](../../../frontend/src/app_teacher/app/TeacherRouter.tsx)
- [StudentRouter.tsx](../../../frontend/src/app_student/app/StudentRouter.tsx)
- [adminNavConfig.tsx](../../../frontend/src/app_admin/layout/adminNavConfig.tsx)
- [TeacherTabBar.tsx](../../../frontend/src/app_teacher/layout/TeacherTabBar.tsx)
- [TeacherDrawer.tsx](../../../frontend/src/app_teacher/layout/TeacherDrawer.tsx)
- [StudentTabBar.tsx](../../../frontend/src/app_student/layout/StudentTabBar.tsx)
- [StudentDrawer.tsx](../../../frontend/src/app_student/layout/StudentDrawer.tsx)

### 4.5 검증

레지스트리 검증 스크립트는 다음을 실패로 처리한다.

- 중복 `featureId`, `screenId`
- 허용되지 않은 문자
- 동적 라우트인데 템플릿에 실제 ID가 남는 경우
- active 화면이 없는 active 기능
- 존재하지 않는 feature를 참조하는 route
- retired ID를 새로운 의미로 재사용
- `rare` 기능에 자동 저사용 판정을 설정

### 4.6 종료 조건

- 모든 인증 후 현재 라우트가 `tracked`, `redirect`, `excluded` 중 하나로 분류된다.
- excluded 항목에는 이유가 있다.
- 기능·화면 ID 목록이 리뷰 가능하다.
- 레지스트리 검증 스크립트가 통과한다.

## 5. E1 — 백엔드 수집, 저장, 권한

### 5.1 목표

클라이언트가 보낸 행동 이벤트에서 신뢰할 수 없는 사용자·테넌트·역할 정보를 제거하고, 서버가 확정한 익명 이벤트만 저장한다.

### 5.2 권장 코드 배치

현재 구조를 크게 바꾸지 않는 최소 배치는 다음과 같다.

```text
backend/apps/core/
  product_analytics/
    __init__.py
    constants.py
    hashing.py
    serializers.py
    services.py
    queries.py
    views.py
  models/
    product_analytics.py
  management/commands/
    rollup_product_usage.py
    purge_product_usage.py
  tests/
    test_product_analytics_ingestion.py
    test_product_analytics_queries.py
    test_product_analytics_retention.py
```

연결 변경:

- [apps/core/models/\_\_init\_\_.py](../../apps/core/models/__init__.py)
- [apps/core/urls.py](../../apps/core/urls.py)
- 필요 시 [apps/core/views/\_\_init\_\_.py](../../apps/core/views/__init__.py)
- [settings/base.py](../../apps/api/config/settings/base.py)
- production 환경 설정과 SSM 발행 경로

### 5.3 원본 모델

`ProductUsageEvent`의 최소 필드:

```text
id                    BigAutoField
event_id              UUID unique
tenant                FK Tenant, PROTECT
actor_hash             char(64)
role                   char(20)
audience_group         char(20)
session_id             UUID
view_id                UUID
interaction_id         UUID nullable
event_type             char(24)
feature_id             char(80)
screen_id              char(100)
surface                char(16)
route_template         char(180)
cta_id                 char(80) blank
action_id              char(80) blank
placement_id           char(80) blank
position_index         smallint nullable
failure_category       char(20) blank
device_class           char(12)
client_release         char(64)
catalog_version        char(32)
occurred_at            datetime
received_at            datetime auto
synthetic              boolean
is_impersonated        boolean
```

인덱스:

- `(tenant_id, occurred_at)`
- `(role, occurred_at)`
- `(feature_id, event_type, occurred_at)`
- `(screen_id, event_type, occurred_at)`
- `(cta_id, placement_id, event_type, occurred_at)`
- `(interaction_id)`
- `(synthetic, is_impersonated, occurred_at)`

인덱스 이름은 PostgreSQL 제한 길이 안에서 명시적으로 정한다. SQLite 테스트와 PostgreSQL 사전검증을 모두 통과해야 한다.

### 5.4 일별 익명 사용자 모델

`ProductUsageDailyActor`:

```text
day
tenant
actor_hash
role
audience_group
surface
feature_id
screen_id
event_type
cta_id
action_id
placement_id
position_index
device_class
client_release
count
first_at
last_at
```

모든 차원과 `day`의 유일 제약을 둔다. 같은 날짜를 다시 집계하면 update 또는 완전 교체로 같은 결과가 나와야 한다.

### 5.5 익명 사용자 키

새 설정 `PRODUCT_ANALYTICS_HASH_KEY`를 사용한다.

- `HMAC-SHA256(key, "tenant_id:user_id")`
- 키가 없는데 기능 플래그가 켜져 있으면 이벤트를 저장하지 않고 명확한 운영 오류를 남긴다.
- 일반 Django `SECRET_KEY`와 분리한다.
- 키 원문과 생성된 메시지를 로그에 출력하지 않는다.

### 5.6 수집 API

추가 URL:

```text
POST /api/v1/core/product-analytics/events/batch/
```

처리 순서:

1. 인증 확인
2. `request.tenant` 확인
3. 현재 테넌트의 활성 멤버십과 역할 확인
4. `Program.feature_flags.product_usage_analytics_enabled` 확인
5. 요청 크기와 이벤트 개수 확인
6. 전체 배치 serializer 검증
7. 서버 필드 생성
8. `bulk_create(ignore_conflicts=True)`
9. accepted·duplicates 수를 `202`로 반환

클라이언트가 `tenant_id`, `role`, `user_id`, `actor_hash`를 보내면 허용되지 않은 필드로 거부한다.

### 5.7 데이터 오염 제외

- JWT에 `impersonated_by`가 있으면 `is_impersonated=true`
- E2E 클라이언트는 `synthetic=true`
- 개발 모드에서는 클라이언트가 전송하지 않음
- 운영 분석 기본 쿼리는 두 종류를 제외
- 필요할 때만 운영 품질 필터에서 확인

### 5.8 백엔드 테스트

필수 테스트:

- 인증되지 않은 요청 `401`
- 테넌트 없는 요청 저장 0건
- 멤버십 없는 사용자 저장 0건
- 요청 본문 역할·테넌트 위조 거부
- 역할은 활성 멤버십에서 결정
- parent와 student가 구분됨
- 기능 플래그 off이면 저장하지 않음
- 최대 20개와 64KB 제한
- 잘못된 ID 문자·길이 거부
- 24시간 이전·5분 이후 시각 거부
- 같은 `event_id` 재전송 시 중복 없음
- 원본 URL 쿼리와 자유 속성 거부
- 대리 로그인 claim 기록
- 이벤트 저장 오류가 도메인 API에 영향을 주지 않음
- 타 테넌트 이벤트 조회 불가
- 플랫폼 관리자만 교차 테넌트 집계 가능

### 5.9 종료 조건

- 마이그레이션이 새 테이블만 추가한다.
- 수집 API의 tenant/auth/role 테스트가 통과한다.
- 원본 사용자·엔티티 ID가 모델과 요청 계약에 없다.
- endpoint p95 목표 200ms 이내를 로컬 반복 요청으로 확인한다.

## 6. E2 — 프론트 수집 모듈과 화면 방문

### 6.1 목표

앱 전체에 흩어진 임의의 이벤트 호출을 만들지 않고 하나의 공통 모듈에서 배치, 중복 제거, 라우트 매칭, 실패 처리를 소유한다.

### 6.2 신규 파일

```text
frontend/src/shared/productAnalytics/
  client.ts
  queue.ts
  session.ts
  routeMatcher.ts
  ProductAnalyticsRouteObserver.tsx
  TrackedCta.tsx
  useTrackedTask.ts
  failureCategory.ts
```

### 6.3 최상위 연결

[AppInner.tsx](../../../frontend/src/AppInner.tsx)에서 기존 Sentry breadcrumb와 별도로 `ProductAnalyticsRouteObserver`를 연결한다.

Observer 동작:

- 인증 로딩이 끝나고 `user.tenantRole`이 있을 때만 작동
- `program.feature_flags.product_usage_analytics_enabled`가 true일 때만 작동
- `/dev`, `/promo`, `/landing`, `/login` 제외
- route registry 매칭 성공 시 새 `view_id` 생성
- 같은 location key에서 `screen_view` 한 번
- `document.visibilityState=visible` 누적 10초 후 `screen_engaged` 한 번
- query/hash를 이벤트에 포함하지 않음

React 개발 StrictMode의 effect 재실행에도 중복되지 않도록 location key와 view state로 방어한다.

### 6.4 큐와 전송

- 메모리 큐만 사용
- 5초 또는 10개 도달 시 flush
- batch 최대 20개
- 전송은 사용자 작업과 await하지 않음
- 실패 시 지수 백오프 없이 한 번만 재시도
- 오프라인 장기 저장 없음
- 로그아웃 시 세션·큐 폐기
- 탭 종료 전 `visibilitychange/pagehide`에서 가능한 범위 내 `keepalive` flush

인증과 테넌트 헤더는 [axios.ts](../../../frontend/src/shared/api/axios.ts)의 기존 SSOT를 재사용한다. 종료 flush용 `fetch`가 필요하면 인증·테넌트 헤더 생성 함수를 그 모듈에서 공개하고 복제하지 않는다.

### 6.5 CTA 공통 컴포넌트

`TrackedCta`는 기존 Button·Link 외형을 대체하지 않고 감싸거나 hook을 제공한다.

필수 prop:

- `featureId`
- `screenId`
- `ctaId`
- `placementId`
- `positionIndex`

노출 조건:

- IntersectionObserver 50%
- 연속 500ms
- 같은 `view_id + cta_id + placement_id` 한 번

클릭 시 새 `interaction_id`를 만들고 원래 onClick을 그대로 실행한다.

### 6.6 작업 추적 hook

`useTrackedTask` 또는 작은 함수형 API:

```text
start(actionId, interactionId)
success(actionId, interactionId)
failure(actionId, interactionId, failureCategory)
```

- 성공은 API 응답과 반영 확인 후 호출
- 서버 오류 원문은 보내지 않고 category만 전송
- 분석 함수가 throw하지 않도록 내부에서 모든 오류를 삼키고 개발 모드에만 제한된 경고

### 6.7 프론트 검증

- 동적 URL이 route template으로 정상화됨
- query/hash가 payload에 없음
- 같은 화면에서 screen event 중복 없음
- 9초 체류는 engaged 없음, 10초 누적 체류는 1건
- CTA가 보이지 않으면 impression 없음
- 50%·500ms 조건 후 1건
- 클릭·시작·성공이 같은 interaction ID
- 분석 endpoint 실패에도 원래 CTA와 API 흐름 성공
- 큐 최대치와 재시도 횟수 준수
- 로그아웃 시 이전 사용자 큐 폐기
- synthetic 플래그 적용

### 6.8 종료 조건

- admin, teacher, parent, student 대표 라우트에서 정확한 화면 이벤트가 발생한다.
- 프론트 payload에 PII와 엔티티 ID가 없다.
- 네트워크 차단 상태에서도 UI 콘솔 오류와 사용자 오류 안내가 없다.

## 7. E3 — 역할별 CTA와 업무 완료 계측

### 7.1 원칙

한 번에 모든 버튼을 추적하지 않는다. 화면 방문 전체 커버리지와 각 기능의 **대표 진입 CTA 및 대표 완료 행동**을 먼저 계측한다.

각 기능은 다음 최소 퍼널을 가져야 한다.

```text
진입 CTA 노출
  → 진입 CTA 클릭
  → 화면 방문
  → 대표 작업 시작
  → 대표 작업 성공 또는 실패
```

읽기 전용 기능은 대표 작업 대신 `screen_engaged`를 완료 신호로 사용한다.

### 7.2 1차 선생님·직원 계측

우선순위 순서:

1. 대시보드 주요 카드와 빠른 작업
2. 학생 목록·상세 진입
3. 강의·수업 목록과 수업 상세
4. 출석 저장
5. 성적 저장
6. 시험·과제 생성 또는 제출함 처리
7. 클리닉 처리
8. 메시지 발송
9. 영상·자료 저장소
10. 커뮤니티·알림·상담
11. 수납·직원·설정·도구

메뉴 노출·클릭 변경 후보:

- [adminNavConfig.tsx](../../../frontend/src/app_admin/layout/adminNavConfig.tsx)
- [TeacherTabBar.tsx](../../../frontend/src/app_teacher/layout/TeacherTabBar.tsx)
- [TeacherDrawer.tsx](../../../frontend/src/app_teacher/layout/TeacherDrawer.tsx)

기능 내부 CTA는 기존 mutation success 지점에만 최소 호출을 추가한다. 메시지 발송 내용이나 대상자는 기록하지 않는다.

### 7.3 1차 학부모 계측

학생 앱 화면은 같아도 서버 역할이 `parent`인 이벤트만 학부모로 집계한다.

우선순위:

1. 홈 카드
2. 자녀 전환 성공
3. 일정·공지·알림 읽기
4. 성적 목록·상세
5. 시험·과제 결과 확인
6. 클리닉 예약·취소
7. 영상 진입
8. 출결·수납 확인
9. 커뮤니티

자녀 전환 이벤트에는 선택한 학생 ID나 이름을 포함하지 않는다.

### 7.4 1차 학생 계측

우선순위:

1. 홈 카드
2. 영상 재생 시작·완료
3. 일정·공지·알림 읽기
4. 시험 응시 시작·제출 성공
5. 과제 제출 시작·성공
6. 성적 목록·상세
7. 클리닉 예약·취소
8. 출결·커뮤니티·인벤토리

학생 내비게이션 변경 후보:

- [StudentTabBar.tsx](../../../frontend/src/app_student/layout/StudentTabBar.tsx)
- [StudentDrawer.tsx](../../../frontend/src/app_student/layout/StudentDrawer.tsx)

### 7.5 계측 PR 단위

하나의 작업 배치는 기능군 하나만 다룬다.

예:

```text
배치 A: 공통 라우트 + admin/teacher/student 내비게이션
배치 B: 출석·성적
배치 C: 시험·과제·제출
배치 D: 영상·클리닉
배치 E: 메시지·커뮤니티·알림
배치 F: 수납·직원·설정·도구
```

각 배치에서 레지스트리, 실제 CTA, 성공·실패 지점, 테스트를 같이 변경한다.

### 7.6 계측 리뷰 체크리스트

- 이 이벤트가 답할 제품 질문이 있는가?
- 같은 의미의 기존 feature/action ID가 있는가?
- 이벤트에 엔티티 ID나 사용자 입력이 섞이지 않는가?
- 성공이 실제 서버 성공 이후인가?
- 재시도와 중복 클릭이 별도 interaction으로 구분되는가?
- disabled CTA를 노출로 셀 것인지 명시했는가?
- 역할상 볼 수 없는 CTA가 계측되지 않는가?
- 해당 기능의 기대 빈도와 전략 중요도가 등록됐는가?

### 7.7 종료 조건

- 최초 기능 레지스트리의 각 active 기능에 방문 또는 대표 완료 신호가 있다.
- 세 역할 집단에서 최소 3개씩의 완성된 퍼널이 있다.
- 관리자 PC와 선생님 surface를 같은 기능 기준으로 비교할 수 있다.

## 8. E4 — 운영 분석 API와 대시보드

### 8.1 백엔드 API

추가 API:

```text
GET /api/v1/core/dev/product-analytics/overview/
GET /api/v1/core/dev/product-analytics/features/<feature_id>/
GET /api/v1/core/dev/product-analytics/quality/
```

공통 query:

```text
from=YYYY-MM-DD
to=YYYY-MM-DD
audience_group=teacher_staff|parent|student
role=owner|admin|teacher|staff|parent|student
surface=admin|teacher|student
tenant_id=<int>
device_class=mobile|tablet|desktop
include_synthetic=false
```

규칙:

- `IsAuthenticated`, `IsPlatformAdmin`
- 최대 기간 400일
- 최근 30일 상세 퍼널은 원본 이벤트 사용
- 장기 추세는 일별 집계 사용
- 단일 테넌트 고유 사용자 5명 미만 셀은 suppressed 표시
- 모든 조회를 운영 감사 로그에 기록하되 결과 데이터는 로그에 넣지 않음

### 8.2 overview 응답

최소 응답:

```text
meta
  from, to, filters, source, generated_at
summary
  active_actors, engaged_actors, task_starts, task_successes, task_failures
roles[]
features[]
  feature_id
  visitors
  engaged_actors
  visit_rate
  completion_rate
  failure_rate
  successes_per_actor_per_week
  current_period
  previous_period
  last_seen_at
  sample_status
ctas[]
  feature_id, cta_id, placement_id, position_index
  impressions, clicks, click_rate, task_successes, completion_rate
quality
  accepted, duplicates, rejected, unknown_feature_ids, late_event_rate
```

label, 전략 중요도, 기대 빈도, 제안 조치는 프론트 기능 레지스트리와 합친다. 알 수 없는 ID는 숨기지 않고 품질 경고로 보인다.

### 8.3 프론트 운영 화면

신규 파일:

```text
frontend/src/app_dev/domains/product-analytics/
  api/productAnalytics.api.ts
  hooks/useProductAnalytics.ts
  pages/ProductAnalyticsPage.tsx
  pages/ProductAnalyticsPage.module.css
  components/AnalyticsFilters.tsx
  components/FeatureUsageTable.tsx
  components/CtaPlacementTable.tsx
  components/UsageTrendChart.tsx
  components/DataQualityCard.tsx
```

연결:

- [DevAppRouter.tsx](../../../frontend/src/app_dev/app/DevAppRouter.tsx)에 `/dev/product-analytics`
- [DevLayout.tsx](../../../frontend/src/app_dev/layout/DevLayout.tsx)에 `사용 분석`

### 8.4 화면 상태

- 로딩 skeleton
- 데이터 없음
- 표본 부족
- 권한 거부
- API 오류와 재시도
- 일부 수치 suppressed
- unknown feature ID
- 390, 1100, 1366 너비

표는 기능명, 집단, surface, 방문자, 참여율, 완료율, 실패율, 반복 빈도, 추세, 마지막 사용, 제안 조치를 제공한다.

### 8.5 종료 조건

- 역할 집단과 원본 역할 필터가 모두 동작한다.
- 학부모와 학생 수치가 분리된다.
- 같은 기능의 admin/teacher surface를 비교할 수 있다.
- 저사용과 표본 부족이 구분된다.
- 단일 테넌트 작은 셀이 숨겨진다.
- 운영 관리자 외 사용자는 API와 라우트 모두 접근할 수 없다.

## 9. E5 — 집계, 보존, 운영 품질

### 9.1 집계 command

```powershell
python manage.py rollup_product_usage --date 2026-07-28
```

요구사항:

- 대상 날짜와 원본 행 수 출력
- 같은 날짜 재실행 안전
- transaction 사용
- 성공 후 집계 행 수와 원본 대비 축약률 출력
- 합성·대리 이벤트도 저장하되 차원으로 분리하거나 기본 집계에서 제외
- 다른 도메인 테이블 수정 금지

### 9.2 정리 command

기본은 dry-run:

```powershell
python manage.py purge_product_usage --before 2026-06-29 --dry-run
```

실행:

```powershell
python manage.py purge_product_usage --before 2026-06-29 --execute
```

요구사항:

- 정확한 날짜 경계
- 대상 이벤트 수, 테넌트 수, 가장 오래된·최신 시각 출력
- 해당 날짜 집계 완료 여부 검증
- 사용자 작성 데이터 대상 0건 보장
- `--execute` 없이는 삭제 없음
- 운영 자동 실행 승인 전 수동 dry-run만 허용

### 9.3 용량·성능 경보 기준

초기 운영 기준:

- 일일 원본 이벤트 증가량이 예상 대비 2배 이상이면 경고
- 수집 API p95가 200ms 초과 시 경고
- 수집 배치 거부율 1% 초과 시 경고
- unknown feature ID가 0이 아니면 경고
- 역할별 screen_view가 이전 7일 평균 대비 80% 이상 급락하면 경고
- 원본 이벤트 예상 90일 용량이 DB 여유 공간의 20%를 넘으면 수집 범위 또는 저장소 재검토

### 9.4 스케줄

집계와 보존 command가 로컬·preprod에서 검증된 뒤에만 운영 스케줄을 추가한다. 기존 Video 전용 워커 경계를 재사용하지 않는다.

운영 스케줄 후보:

- 매일 02:30 KST: 전일 집계
- 매일 03:00 KST: 30일 초과 원본 dry-run/execute
- 주 1회: 품질·용량 보고

실제 EventBridge·SSM·Batch 경로는 현재 배포 스크립트의 소유권과 비용을 다시 확인한 뒤 하나만 선택한다. 새 규칙은 SSOT 파라미터, IaC, drift 검증, 제거 보호 목록을 동시에 갱신한다.

### 9.5 종료 조건

- 집계 재실행 결과가 동일하다.
- 30일 원본과 400일 집계 경계 테스트가 통과한다.
- 삭제 dry-run이 정확한 대상만 열거한다.
- 운영 품질 카드와 구조화 로그가 일치한다.

## 10. E6 — 사전검증, 배포, 파일럿

### 10.1 로컬·CI

Backend focused:

```powershell
cd C:\academy\backend
python manage.py check --settings apps.api.config.settings.test
python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test
python -m ruff check apps/ academy/
python -m pytest apps/core/tests/test_product_analytics_ingestion.py -v --tb=short -x
python -m pytest apps/core/tests/test_product_analytics_queries.py -v --tb=short -x
python -m pytest apps/core/tests/test_product_analytics_retention.py -v --tb=short -x
python scripts/lint/refactor_boundary_snapshot.py --strict-touched
git diff --check
```

PostgreSQL-specific migration·index·bulk insert 검증은 `test_pg` 설정으로 추가한다.

Frontend focused:

```powershell
cd C:\academy\frontend
node --test scripts/tests/product-analytics-registry.test.mjs
node scripts/verify-product-analytics-registry.mjs
pnpm typecheck
pnpm guard:legacy-api
pnpm lint
pnpm verify:routes
pnpm build
git diff --check
```

대표 E2E:

- 선생님: 출석 또는 성적 저장 퍼널
- 학부모: 성적 상세 또는 클리닉 예약 퍼널
- 학생: 시험·과제 제출 또는 영상 완료 퍼널
- 운영자: `/dev/product-analytics` 역할 필터·권한·빈 상태
- tenant isolation: 타 테넌트 event/query 차단
- tracking outage: 이벤트 endpoint 500이어도 원래 업무 성공

### 10.2 production continuity gate

백엔드 마이그레이션이 있으므로 생산 적용은 프로젝트의 기존 순서를 그대로 따른다.

1. immutable digest 후보 생성
2. 격리된 preprod EC2와 `academy_api_preprod`에서 migration 적용
3. production settings, DB boundary, `/healthz`, `/health`, 이미지 확인
4. 분석 endpoint의 tenant/auth/role·대시보드 권한 확인
5. 임시 인스턴스 종료 확인
6. 그 뒤에만 생산 migration과 ASG/ALB 건강 기반 rolling refresh
7. 기능 플래그는 배포 후에도 기본 off

새 테이블 추가 마이그레이션은 구 API와 신 API가 겹쳐도 안전해야 한다. 기존 테이블 변경이나 필수 필드 추가는 이 배치에 포함하지 않는다.

### 10.3 기능 플래그 롤아웃

`Program.feature_flags.product_usage_analytics_enabled`:

1. 배포 직후 전체 off
2. 플랫폼 운영 테넌트 on, 7일
3. 대표 테넌트 2~3곳 on, 7일
4. 수집 품질·DB 증가량·writer 부하 확인
5. 전체 테넌트 순차 on
6. 28일 기준선 전 UI 위치 변경 금지

### 10.4 파일럿 합격 기준

- 수집 배치 거부율 1% 미만
- unknown feature ID 0건
- 같은 화면의 중복 `screen_view` 1% 미만
- 이벤트 수집 실패로 인한 사용자 오류 0건
- parent/student 역할 혼합 0건
- cross-tenant 접근 0건
- DB 증가량이 90일 용량 안전 기준 이내
- 제품 분석 추가분이 writer DB 시간·쓰기량의 10% 미만
- dashboard 쿼리 p95 1초 이내

## 11. E7 — DB 가용성·멀티 DB·테넌트 분리 실행 계획

### 11.1 지금 실행할 범위와 보류할 범위

지금 실행하는 E7A:

- Academy 태그 기준 비용선과 RDS 용량·복구 상태를 하나의 의사결정 보고서로 고정
- SQL 본문 없이 테넌트별 DB 실행 시간·쿼리·쓰기·작업 부하를 계측
- 28일마다 Multi-AZ, read replica, tenant split을 별도 판정
- Multi-AZ 복원·전환 리허설과 운영 변경안 준비

게이트 전에는 실행하지 않는 항목:

- `DATABASE_ROUTERS` 추가
- 테넌트별 schema 또는 database 생성
- 도메인 모델 이동과 dual-write
- read replica 생성·조회 라우팅
- 운영 RDS `multiAz` 변경

운영 인프라 변경, 데이터 복사와 생산 라우팅 전환은 각각 별도 명시적 승인 대상이다.

### 11.2 E7A-1 — 현재 기준선 고정

다음 실행 진실을 같은 시각 기준으로 수집한다.

- [runtime-current.md](../ssot/runtime-current.md)의 RDS class, engine, Single/Multi-AZ, storage
- [params.yaml](../ssot/params.yaml)의 `rds.multiAz`, 경보, 예산 설정
- [connection-budget.md](../infrastructure/connection-budget.md)의 정상·배포·사고 연결 범위
- [cost-waste-audit.latest.md](../reports/cost-waste-audit.latest.md)의 CPU, 메모리, credit, 연결, 비용
- 최신 스냅샷, PITR 가능 시각과 복원 리허설 결과
- API·일반 워커의 `DATABASES`, `CONN_MAX_AGE`, DB router 부재

보고서에는 `측정 시각`, `기간`, `Academy 태그 여부`, `account-wide 여부`를 반드시 적는다. 현재 비용 보고가 계정 전체 범위이면 Multi-AZ 비용 게이트를 통과한 것으로 간주하지 않는다.

종료 증거:

```text
_artifacts/db-capacity/
  current-baseline-YYYYMMDD.md
  restore-rehearsal-YYYYMMDD.md
  decision-YYYYMMDD.md
```

운영 비밀, 연결 문자열, 사용자 데이터는 artifact에 넣지 않는다.

### 11.3 E7A-2 — 테넌트별 DB 부하 계측

CloudWatch의 RDS 지표는 인스턴스 전체만 보여 주므로 tenant split 근거로 부족하다. API와 일반 워커에 다음 최소 계측을 추가한다.

권장 코드 경계:

```text
backend/apps/core/observability/
  tenant_db_usage.py
backend/apps/core/middleware/
  tenant_db_usage.py
backend/apps/core/tests/
  test_tenant_db_usage.py
backend/apps/core/management/commands/
  report_tenant_db_capacity.py
```

요청·작업 단위 구조화 로그:

```text
observed_at
tenant_id
db_alias
surface_or_job
route_or_job_family
query_count
write_query_count
db_duration_ms
request_or_job_duration_ms
status_class
sample_rate
```

구현 규칙:

- Django `connection.execute_wrapper()`와 요청·작업 ContextVar로 시간과 횟수만 집계한다.
- SQL, bind parameter, URL 동적 ID, 사용자·학생·학부모 식별자는 저장하지 않는다.
- API 일반 요청은 기본 10% 표본, 느린 요청·실패와 정기 작업은 100% 기록한다.
- 샘플링된 값은 `sample_rate`로 보정하되 청구나 SLA 증거로 사용하지 않는다.
- 테넌트가 확정되지 않았거나 라우팅 context가 모호하면 tenant metric도 fail-closed로 버린다.
- CloudWatch 고카디널리티 custom metric을 테넌트별로 만들지 않고 제한된 구조화 로그와 28일 보고서로 집계한다.
- write 판정에 SQL을 메모리에서 보더라도 문장과 table 이름은 로그에 남기지 않는다.
- 계측 overhead는 off/on 부하 테스트로 비교하고 API p95 3% 증가 또는 CPU 5% 증가 시 표본율을 낮춘다.

주 1회 `report_tenant_db_capacity`는 다음을 출력한다.

- 전체 대비 테넌트별 추정 DB 시간, 쿼리, 쓰기, 작업 비중
- 테넌트별 핵심 API p95와 DB 시간 비중
- 공용 DB 연결 peak, CPU/DB wait, free storage·90일 저장 전망
- 제품 분석 이벤트가 추가한 writer 시간·쓰기·저장량
- 임계값 접근 테넌트와 데이터 품질·표본 수

저장 비중은 tenant-owned 모델 inventory의 행 수와 PostgreSQL 통계 기반 추정치로 시작하고, statement timeout을 둔 read-only command로 계산한다. 정확한 물리 byte로 오인하지 않도록 `estimated`를 표시한다.

### 11.4 E7A-3 — Multi-AZ 의사결정과 준비

매 28일 다음 순서로 판정한다.

1. 제품 기획서 16.3의 서비스 중요도 게이트 확인
2. Academy 태그 30일 비용과 변경 시점 AWS 견적으로 월 증분액 확인
3. 증분액 + 20% 여유를 더한 forecast가 승인 예산의 85% 이내인지 확인
4. 최신 snapshot/PITR에서 격리 복원
5. migration 상태, `/healthz`, 로그인, 출석·시험·결제 중 적용 기능 확인
6. failover 시 연결 재수립과 작업 재시도 정책 확인
7. 결정, 승인자, 유효기간과 다음 리뷰일 기록

서비스 중요도와 비용·복구 게이트가 모두 통과하면 다음 변경안을 준비한다.

- [params.yaml](../ssot/params.yaml)의 `rds.multiAz: true`
- 기존 [rds.ps1](../../scripts/v1/resources/rds.ps1)의 canonical 변경 경로 사용
- maintenance window, 예상 영향, rollback 기준과 담당자 명시
- 변경 전후 [run-ops-healthcheck.ps1](../../scripts/v1/run-ops-healthcheck.ps1) 실행
- RDS 상태, AZ, 연결, API·워커 queue와 핵심 사용자 흐름 확인

문서 준비는 생산 변경 승인이 아니다. 실제 운영 변경은 별도 요청과 현재 배포·운영 게이트를 따른다.

### 11.5 E7B — read replica 조건부 epic

제품 기획서 16.4의 28일 게이트를 통과한 경우에만 연다.

선행 작업:

- `pg_stat_statements` 또는 동등한 실행 진실로 읽기 비중과 상위 조회 확인
- 느린 조회, N+1, 누락 인덱스 수정 전후 비교
- lag 허용 endpoint allowlist 작성
- writer에서 읽어야 하는 인증·권한·결제·상태 머신 denylist 테스트
- replica lag, 비용, failover와 stale-read 오류 예산 정의

구현은 기본 writer를 유지하고 allowlist 조회만 명시적으로 replica alias를 선택한다. 전역 round-robin router는 사용하지 않는다. replica 지연 또는 연결 오류 시 writer fallback은 endpoint별 일관성 계약이 허용할 때만 한다.

종료 조건:

- 28일 read ratio와 writer 병목 증거가 있다.
- allowlist 조회가 stale data를 허용한다.
- replica 장애가 쓰기·핵심 사용자 흐름을 막지 않는다.
- 비용이 writer 상향 대안보다 낮다.

### 11.6 E7C — 선택적 tenant data plane 조건부 epic

제품 기획서 16.5의 즉시 분리 사유 또는 성능·비용 게이트를 통과한 테넌트만 대상으로 한다. 구현 전 먼저 현재 모델의 tenant ownership, 직접·간접 foreign key, cross-domain join, transaction과 비동기 작업을 목록화한다.

목표 control/data plane 계약:

```text
default control plane
  Tenant / TenantDomain / auth / membership / feature flags
  TenantDataPlane(alias, kind, schema_version, routing_version, status)

shared data plane
  기본 이동 가능 도메인 데이터

dedicated data plane
  승인된 테넌트의 같은 schema
```

보안·라우팅 규칙:

- DB credentials는 SSM·secret 경로에만 두고 `TenantDataPlane`에는 alias와 상태만 저장한다.
- 요청은 control plane에서 tenant를 확정한 뒤 data plane alias를 결정한다.
- alias가 없거나 비활성·불일치이면 `default`로 fallback하지 않고 실패한다.
- control plane 모델과 data plane 모델에 각각 명시적 router를 둔다.
- cross-DB foreign key, cross-DB join과 원자 transaction을 금지한다.
- 비동기 job은 `tenant_id`를 전달하고 시작 시 최신 `routing_version`과 alias를 다시 해석한다.
- 모든 data plane의 schema version inventory가 일치해야 배포를 진행한다.

테넌트 1곳 이동 런북:

1. 대상 테넌트·도메인·행 수·첨부 저장소·비동기 작업과 영향 사용자 열거
2. 전용 DB 생성, 암호·백업·삭제 보호·관측·복구 정책 설정
3. preprod와 전용 DB에 expand migration 적용
4. 초기 copy 후 table count, checksum과 도메인 invariant 비교
5. logical replication 또는 승인된 짧은 tenant write pause로 delta 수렴
6. 해당 테넌트 신규 작업 중지와 queue drain 확인
7. 최종 delta·검증 후 하나의 routing version을 원자적으로 전환
8. 로그인, 권한, 출석·시험·결제, worker·메시징 중 적용 흐름 확인
9. 구 위치는 최소 7일 read-only로 보존하고 rollback 만료일 기록
10. 안정화 후 별도 승인으로 구 데이터 정리

일반 dual-write는 사용하지 않는다. 필요한 경우에도 양쪽 쓰기의 idempotency, 순서, 부분 실패 복구와 대조 작업이 먼저 증명되어야 한다.

롤백은 새 data plane 쓰기 발생 전에는 routing version을 되돌린다. 새 쓰기 발생 후에는 reverse delta와 검증 없이 단순 route flip을 금지한다. 데이터 삭제는 정확한 대상과 사용자 작성 데이터 보호를 열거한 별도 승인 작업이다.

### 11.7 28일 DB 구조 결정표

| 관측 | 결정 |
|---|---|
| Single-AZ 중요도 게이트 충족, 비용·복구 게이트 통과 | Multi-AZ 변경안 승인 요청 |
| 읽기 70% 이상, writer 압박, lag 허용 조회 존재 | E7B read replica 검토 |
| 특정 테넌트 30% 이상이 두 구간 지속 + 타 테넌트 SLO 영향 | E7C dedicated data plane 검토 |
| 저장 25% 이상 단일 테넌트 + 90일 내 여유 소진 | E7C 저장 분리 검토 |
| 모든 테넌트가 비슷하게 증가 | 공용 DB 상향·쿼리·파티셔닝 검토 |
| 제품 분석만 writer 10% 이상 또는 여유 공간 20% 이상 소비 | 분석 범위 축소 또는 분석 저장소 분리 |
| 어느 게이트도 미충족 | 현재 단일 writer 유지, 28일 뒤 재검토 |

## 12. 첫 28일 운영 절차

### 주 1회 품질 점검

- 역할별 활성 사용자와 screen_view 존재 여부
- unknown feature ID
- 플래그가 켜진 테넌트 대비 수집 테넌트 수
- 합성·대리 로그인 비율
- 수집 거부·중복·지연
- 이벤트 테이블 증가량
- 제품 분석이 추가한 writer 시간·쓰기·90일 저장 전망
- 테넌트별 DB 시간 상위 비중과 공용 RDS 연결·CPU/DB wait
- 3개 대표 퍼널의 click/start/success 연결률

### 28일 종료 리뷰

다음 순서로만 후보를 만든다.

1. 표본 부족과 `rare` 기능 제외
2. 실패율 높은 기능은 UI 노출보다 흐름 수정 우선
3. 노출 낮고 클릭·완료 높은 기능은 위치 상향 후보
4. 노출 높고 클릭 낮은 CTA는 문구·대상 검토
5. 참여·완료가 높은 기능은 빠른 진입 유지
6. 저사용·낮은 전략 중요도 기능은 하위 이동 후보

각 변경 후보에 다음을 기록한다.

```text
대상 역할
feature_id
기존 placement_id / position_index
변경 placement_id / position_index
변경 이유와 예상 결과
기준 기간
최소 표본
보호 지표: 완료율, 실패율
되돌림 조건
```

## 13. 롤백

### 수집 장애

1. 해당 테넌트 또는 전체 기능 플래그 off
2. 프론트 큐가 전송을 중지하는지 확인
3. 원래 업무 경로 정상 확인
4. 수집 API와 테이블은 유지해 이전 프론트 요청을 안전하게 받음

### 대시보드 성능 장애

1. `/dev` 분석 메뉴를 숨기거나 API를 일시 비활성화
2. 수집은 계속 가능
3. 쿼리 범위·인덱스·rollup 사용 여부 수정

### 데이터 오염

1. 오염 기간, 테넌트, `client_release`, `catalog_version`을 정확히 식별
2. 기본 필터로 제외
3. 삭제가 필요하면 exact count와 대상 키를 출력
4. 명시적 승인 후 분석 이벤트만 삭제
5. 일별 집계를 같은 범위로 재생성

스키마 롤백을 위해 새 테이블을 즉시 drop하지 않는다. 기능 플래그 off가 1차 롤백이며, 데이터 삭제와 drop은 별도 승인 대상이다.

## 14. 실행 티켓 목록

### Backend

- [x] B1. Product analytics constants·serializer 계약
- [x] B2. `ProductUsageEvent` 모델과 expand migration
- [x] B3. 전용 HMAC 설정과 서버 actor hash
- [x] B4. tenant/membership/flag 기반 batch ingestion
- [x] B5. ingestion tenant/auth/PII/idempotency 테스트
- [x] B6. `ProductUsageDailyActor` 모델과 rollup command
- [x] B7. retention dry-run/execute command
- [x] B8. platform-only overview/detail/quality API
- [x] B9. small-cell suppression과 감사 로그
- [ ] B10. PostgreSQL index·query plan·volume 검증

### Frontend shared

- [x] F1. 기능·라우트 레지스트리
- [x] F2. 레지스트리 검증 스크립트
- [x] F3. 메모리 큐·세션·batch client
- [x] F4. route observer와 10초 engaged
- [x] F5. `TrackedCta`
- [x] F6. `useTrackedTask`
- [ ] F7. StrictMode·visibility·failure 테스트

### Role instrumentation

- [x] I1. 관리자·선생님·학생 내비게이션 노출·클릭
- [ ] I2. 선생님 출석·성적 퍼널 — 출석 완료 계측만 구현
- [ ] I3. 선생님 시험·과제·제출 퍼널 — 학생 시험 제출만 구현
- [ ] I4. 선생님 클리닉·메시지 퍼널 — 클리닉 생성·취소·변경만 구현
- [ ] I5. 학부모 성적·공지·클리닉 퍼널 — 학부모 클리닉만 구현
- [ ] I6. 학생 영상·시험·과제 퍼널 — 학생 시험 제출만 구현
- [x] I7. 나머지 active 기능 방문 커버리지

### Operations dashboard

- [x] D1. `/dev/product-analytics` API module·query hook
- [x] D2. 역할·기간·surface·tenant 필터
- [ ] D3. 기능 사용 표와 추세 — 현재 기간 신호 레일 구현, 직전 기간 추세는 미구현
- [x] D4. CTA 위치 표
- [x] D5. 품질 카드와 표본 부족 상태
- [ ] D6. 권한·반응형·빈 상태 E2E — 로컬 브라우저 1366/1100/390px 수동 검증 완료

### Release

- [x] R1. 로컬 focused checks
- [ ] R2. isolated preprod migration·contract verification
- [ ] R3. production deploy, flag off 확인
- [ ] R4. 1차 파일럿 7일
- [ ] R5. 2차 파일럿 7일
- [ ] R6. 전체 순차 활성화
- [ ] R7. 28일 기준선 리뷰

### DB availability readiness — 지금 실행

- [ ] A1. RDS·연결·비용·복구 current baseline artifact
- [ ] A2. SQL 비저장 tenant DB usage wrapper·middleware·worker context — API middleware 구현, worker context 미연결
- [ ] A3. 표본·overhead·tenant fail-closed 테스트 — 단위 테스트 구현, 실부하 overhead 미측정
- [x] A4. 주간·28일 tenant capacity report command
- [ ] A5. snapshot/PITR 격리 복원 리허설
- [ ] A6. Multi-AZ 비용·복구 게이트 decision record

### Conditional DB epics — 게이트 통과 후만 실행

- [ ] X1. read replica query allowlist·consistency·비용 검증
- [ ] X2. control/data plane 모델 ownership·cross-DB dependency inventory
- [ ] X3. `TenantDataPlane`·fail-closed router·routing version 설계
- [ ] X4. 전체 data plane migration/schema inventory
- [ ] X5. 테넌트 copy·검증·cutover·rollback 도구
- [ ] X6. 전용 data plane 백업·복구·비용·관측 검증

## 15. 실행 완료 정의

구현 작업은 다음 증거가 모두 있을 때 닫는다.

- 제품 기획서의 이벤트·보존·판정 계약과 코드가 일치한다.
- 기능 레지스트리 검증이 CI에서 실행된다.
- backend tenant/auth/role/idempotency/PII 테스트가 통과한다.
- frontend route/CTA/task 계측이 사용자 흐름을 바꾸지 않는다.
- parent와 student, admin과 teacher surface가 분리 집계된다.
- 대표 세 역할 E2E가 실제 클릭 → API → 성공 이벤트까지 증명한다.
- 분석 endpoint 실패 E2E에서도 본 업무가 성공한다.
- `/dev` 대시보드 권한과 small-cell suppression이 검증된다.
- 원본 30일·집계 400일 보존과 dry-run 삭제가 검증된다.
- backend와 frontend의 필수 검사, `git diff --check`, 최종 status가 확인된다.
- 생산 적용 시 격리 preprod와 zero-downtime gate를 통과한다.
- 첫 28일 동안 기존 메뉴·CTA 우선순위를 바꾸지 않는다.
- 28일 DB 구조 보고서가 Multi-AZ, read replica, tenant split을 별도 판정한다.
- 현재 게이트가 미충족이면 멀티 DB router나 테넌트별 schema가 추가되지 않는다.
- 제품 분석 부하가 writer 10%·90일 저장 20% 게이트 안에 있거나 전체 활성화가 중지된다.
