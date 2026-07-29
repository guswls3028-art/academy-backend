# 역할별 기능 사용 분석 기획서

**Status:** [IMPLEMENTED LOCALLY / ROLLOUT OFF]

**작성일:** 2026-07-29 KST

**대상:** 선생님·학원 직원, 학부모, 학생이 사용하는 인증 후 제품 화면

**짝 문서:** [역할별 기능 사용 분석 실행 계획서](product-usage-analytics-execution-plan.md)

**구현 상태:** 1차 관측 수직 슬라이스가 코드와 로컬 검증 환경에 구현되었다. 운영 배포·기능 플래그 활성화·RDS 변경은 하지 않았으며, 아래 계약은 계속 제품 판단의 기준으로 사용한다.

## 0. 현재 구현 경계

2026-07-29 기준 상태를 다음처럼 구분한다.

| 분류 | 상태 |
|---|---|
| 저장소에서 확인됨 | 익명 이벤트·일별 집계 모델, 배치 수집 API, 롤업·보존 명령, 플랫폼 전용 overview API, small-cell suppression, 테넌트별 수집 플래그 |
| 저장소에서 확인됨 | 22개 기능·69개 인증 라우트 레지스트리, 비차단 메모리 큐, 화면 방문·10초 참여·내부 CTA 노출/클릭, 출석·시험 제출·클리닉 대표 완료 계측 |
| 저장소에서 확인됨 | `/dev/product-analytics` 역할·기간·surface·tenant 필터, 기능 신호 레일, CTA 위치, 수집 품질 화면 |
| 저장소에서 확인됨 | SQL·bind parameter를 남기지 않는 API 요청 단위 tenant DB usage 계측과 JSONL capacity report 명령 |
| 운영에서 미검증 | PostgreSQL 실행계획·실데이터 증가량, 계측 overhead, 일반 worker context, 28일 기준선, isolated preprod와 production |
| 의도적으로 미적용 | Multi-AZ 전환, read replica, `DATABASE_ROUTERS`, 테넌트별 schema/database, 자동 메뉴 재정렬 |

수집과 DB 텔레메트리는 기본 off다. 전용 HMAC 키가 없으면 수집 플래그를 켤 수 없고, 테넌트가 확정되지 않은 요청은 저장·계측하지 않는다.

## 1. 한 줄 목표

선생님·학원 직원, 학부모, 학생이 **어떤 기능을 실제로 보고, 들어가고, 사용하고, 완료하거나 실패하는지**를 역할별로 측정하여 메뉴, 홈 카드, CTA 문구, 노출 위치와 우선순위를 근거 있게 개선한다.

이 기능의 목적은 사용자를 감시하는 것이 아니라 다음 질문에 답하는 것이다.

- 자주 쓰는 기능은 무엇인가?
- 중요한데 못 찾는 기능은 무엇인가?
- 노출은 많이 되지만 선택되지 않는 CTA는 무엇인가?
- 클릭은 되지만 완료되지 않는 흐름은 무엇인가?
- 역할별로 상단, 하단 탭, 드로어, 홈 카드 중 어디가 실제 진입점인가?
- 낮은 사용량이 기능 가치가 낮아서인지, 노출 부족이나 사용성 문제 때문인지?

## 2. 현재 확인된 출발점

2026-07-29 코드 실측 결과는 다음과 같다.

| 항목 | 현재 상태 | 근거 |
|---|---|---|
| 인증 역할 | `owner`, `admin`, `teacher`, `staff`, `student`, `parent`를 구분한다. | [AuthContext.tsx](../../../frontend/src/auth/context/AuthContext.tsx), [tenant_membership.py](../../apps/core/models/tenant_membership.py) |
| 역할 앱 | 관리자, 선생님, 학생·학부모 앱의 라우터가 분리되어 있다. 학생과 학부모는 같은 학생 앱을 사용한다. | [AdminRouter.tsx](../../../frontend/src/app_admin/app/AdminRouter.tsx), [TeacherRouter.tsx](../../../frontend/src/app_teacher/app/TeacherRouter.tsx), [StudentRouter.tsx](../../../frontend/src/app_student/app/StudentRouter.tsx) |
| 현재 관측 도구 | 프론트엔드는 Sentry 오류·성능·오류 세션 리플레이를 사용하며 성능 이벤트는 샘플링된다. | [main.tsx](../../../frontend/src/main.tsx), [sentryContext.ts](../../../frontend/src/shared/lib/sentryContext.ts) |
| 현재 “analytics” 코드 | 성적·시험 운영 분석이며 제품 기능 방문·CTA 사용 분석은 아니다. | [apps/support/analytics](../../apps/support/analytics), [enterprise_analytics.py](../../apps/support/results/enterprise_analytics.py) |
| 테넌트 경계 | 요청 경계에서 테넌트를 해석하며 기본 테넌트나 교차 테넌트 폴백을 허용하지 않는다. | [tenant.py](../../apps/core/middleware/tenant.py), [permissions.py](../../apps/core/permissions.py) |
| DB 연결 구조 | API와 모든 일반 워커는 하나의 `default` PostgreSQL 연결만 사용하며, 애플리케이션 DB router와 테넌트별 data plane 메타데이터는 없다. | [API base.py](../../apps/api/config/settings/base.py), [worker.py](../../apps/api/config/settings/worker.py), [Tenant](../../apps/core/models/tenant.py) |
| 운영 DB 가용성 | 운영 RDS는 `db.t4g.medium`, Single-AZ, 20GB이다. preprod는 별도 논리 DB이지만 같은 RDS를 쓰므로 AZ·인스턴스 장애 격리가 아니다. | [runtime-current.md](../ssot/runtime-current.md), [params.yaml](../ssot/params.yaml) |
| DB 부하 | 최근 평균 CPU는 낮지만 메모리·T 계열 credit·과거 연결 폭증 때문에 축소가 보류되어 있다. 2026-04-29에는 연결 누수로 `max_connections`에 도달한 이력이 있다. | [cost-waste-audit.latest.md](../reports/cost-waste-audit.latest.md), [connection-budget.md](../infrastructure/connection-budget.md) |
| 운영자 화면 | 플랫폼 운영용 `/dev` 콘솔과 플랫폼 관리자 권한이 이미 있다. | [DevAppRouter.tsx](../../../frontend/src/app_dev/app/DevAppRouter.tsx), [core urls.py](../../apps/core/urls.py) |
| 저장소 | 운영 DB는 PostgreSQL 15.17이며, 원본 이벤트를 무기한 쌓을 용량은 전제할 수 없다. | [runtime-current.md](../ssot/runtime-current.md) |

따라서 Sentry를 사용량 집계 도구로 확장하지 않는다. Sentry의 샘플링과 오류 중심 데이터는 정확한 기능 사용 분모가 될 수 없고, 외부 관측 도구에 불필요한 제품 행동 데이터를 더 보내게 된다.

## 3. 제품 범위

### 3.1 포함

- 로그인 후 관리자·선생님·학생 앱의 화면 방문
- 실제 화면에 보인 메뉴, 홈 카드, CTA의 노출
- CTA 클릭과 진입 위치
- 저장, 제출, 예약, 발송, 채점 등 주요 작업의 시작·성공·실패
- 역할, 화면 종류, 기기 종류, 노출 위치별 집계
- 플랫폼 운영자가 보는 역할별 사용 분석 화면
- 28일 기준선과 UI 변경 전후 비교

### 3.2 이번 범위에서 제외

- 공개 랜딩·홍보 페이지의 마케팅 분석
- 키 입력, 검색어, 메시지 내용, 학생명 등 사용자 입력 내용 수집
- 마우스 이동, 모든 클릭, 전체 DOM을 기록하는 히트맵
- 개인별 사용 실적을 직원 평가나 학생 평가에 사용하는 기능
- 데이터만으로 메뉴를 자동 재정렬하는 기능
- 사용자마다 메뉴 순서를 다르게 만드는 개인화
- 제3자 제품 분석 SDK 추가

자동 재정렬은 첫 28일 기준선과 수동 의사결정이 검증된 뒤 별도 기능으로 판단한다. 동일 역할 사용자에게 메뉴가 계속 바뀌면 학습 비용과 지원 비용이 커질 수 있기 때문이다.

## 4. 사용자 집단 정의

원본 역할과 보고용 집단을 모두 유지한다.

| 보고 집단 | 포함하는 원본 역할 | 비고 |
|---|---|---|
| 선생님·직원 | `owner`, `admin`, `teacher`, `staff` | 관리자 PC 화면과 선생님 모바일 화면을 `surface`로 다시 구분한다. |
| 학부모 | `parent` | 같은 학생 앱을 사용해도 학생과 별도 집계한다. |
| 학생 | `student` | 학부모가 선택한 자녀 ID는 기록하지 않는다. |

보고 화면은 기본적으로 세 집단을 보여주되 `owner/admin/teacher/staff` 원본 역할 필터를 제공한다. 역할은 클라이언트가 보내는 값을 믿지 않고 서버의 활성 `TenantMembership`에서 결정한다.

## 5. 측정 원칙

### 5.1 방문과 사용을 구분한다

- 화면을 열면 `screen_view`
- 화면이 보이는 상태로 누적 10초 이상 머물면 `screen_engaged`
- CTA가 화면에 실제 보이면 `cta_impression`
- CTA를 선택하면 `cta_click`
- 주요 작업을 실행하면 `task_start`
- 서버가 성공을 확정하면 `task_success`
- 서버나 네트워크 오류로 끝나면 `task_failure`

단순 방문 수만으로 “자주 쓰는 기능”이라고 판단하지 않는다. 방문, 체류, 클릭, 실제 완료를 함께 본다.

### 5.2 노출되지 않은 것과 무시된 것을 구분한다

CTA 노출은 DOM에 존재하는 시점이 아니라 다음 조건을 만족할 때 한 번 기록한다.

- 요소 면적의 50% 이상이 보임
- 500ms 이상 연속으로 보임
- 하나의 화면 방문(`view_id`)에서 같은 CTA·위치는 한 번만 기록

드로어 안의 메뉴는 드로어를 열어 실제 항목이 보일 때만 노출로 센다.

### 5.3 성공은 낙관적 UI가 아니라 저장된 서버 결과를 기준으로 한다

`task_success`는 기존 API의 성공 응답과 저장 결과가 확인된 뒤 발생시킨다. 클릭 직후 성공으로 기록하지 않는다. 오류 후 재시도는 새 `interaction_id`로 별도 시도로 기록한다.

### 5.4 드문 기능은 사용량만으로 낮은 우선순위가 아니다

비밀번호 변경, 결제 설정, 직원 관리, 긴급 복구처럼 본질적으로 드문 기능은 낮은 사용량이 정상일 수 있다. 기능 레지스트리에 기대 빈도와 전략 중요도를 기록해 같은 기준으로 잘못 비교하지 않는다.

## 6. 기능 레지스트리

프론트엔드의 `src/shared/productAnalytics/featureRegistry.ts`를 화면·기능 메타데이터의 실행 SSOT로 둔다. 백엔드는 안전한 ID 형식만 검증하고, 운영 분석 화면은 이 레지스트리의 이름과 중요도 정보를 사용한다.

기능 ID는 삭제하거나 재사용하지 않는다. 기능이 사라지면 `retired` 상태로 남겨 과거 데이터의 의미를 보존한다.

### 6.1 기능 정의 필드

| 필드 | 설명 | 예 |
|---|---|---|
| `featureId` | 역할을 넘어 같은 목적을 묶는 안정 ID | `attendance.manage` |
| `label` | 운영 화면에 표시할 한국어 이름 | `출석 처리` |
| `domain` | 제품 도메인 | `attendance` |
| `audiences` | 노출 가능한 원본 역할 | `owner/admin/teacher/staff` |
| `expectedFrequency` | `daily`, `weekly`, `monthly`, `rare` | `daily` |
| `strategicPriority` | `core`, `support`, `optional` | `core` |
| `status` | `active`, `retired` | `active` |
| `requiredFeatureFlag` | 조건부 기능일 때만 사용 | `fee_management` |
| `primarySuccess` | 이 기능의 대표 완료 행동 | `attendance.update` |

ID는 소문자 영문, 숫자, 마침표, 하이픈만 허용하고 한번 배포된 의미를 바꾸지 않는다.

### 6.2 최초 화면 커버리지

최초 배포에서는 모든 인증 후 라우트를 `screen_view`로 커버하고, 다음 기능군의 주요 진입 CTA와 완료 행동을 우선 계측한다.

| 집단 | 최초 기능군 |
|---|---|
| 선생님·직원 | 대시보드, 학생, 강의·수업, 출결, 성적 입력, 시험·OMR, 과제, 결과·제출함, 영상, 클리닉, 커뮤니티·알림, 상담 메모, 메시지, 자료 저장소·인벤토리, 수납, 직원, 도구 |
| 학부모 | 홈, 자녀 전환, 영상, 일정, 성적·상세, 시험·결과, 과제·제출, 클리닉·인증 패스, 출결, 수납, 공지, 커뮤니티, 알림, 프로필·설정 |
| 학생 | 홈, 영상 재생·완료, 일정, 성적·상세, 시험 응시·제출·결과, 과제 제출, 클리닉·인증 패스, 출결, 공지, 커뮤니티, 알림, 인벤토리, 프로필·설정 |

관리자 화면과 선생님 화면에서 같은 업무를 수행할 수 있으면 `feature_id`는 공유하고 `surface`, `screen_id`, `placement_id`로 진입 경로를 구분한다.

예:

| 항목 | 관리자 PC | 선생님 화면 |
|---|---|---|
| `feature_id` | `students.directory` | `students.directory` |
| `surface` | `admin` | `teacher` |
| `screen_id` | `admin.students.home` | `teacher.students.list` |
| 대표 진입 위치 | `admin.sidebar.primary` | `teacher.bottom_tab` 또는 `teacher.drawer.class_ops` |

## 7. 이벤트 규격

### 7.1 이벤트 종류

| 이벤트 | 발생 조건 | 필수 추가 필드 |
|---|---|---|
| `screen_view` | 레지스트리에 등록된 화면에 진입 | `view_id`, `screen_id` |
| `screen_engaged` | 화면이 보이는 동안 누적 10초 경과 | `view_id`, `screen_id` |
| `cta_impression` | CTA가 50% 이상, 500ms 이상 보임 | `view_id`, `cta_id`, `placement_id` |
| `cta_click` | 해당 CTA 선택 | `view_id`, `interaction_id`, `cta_id`, `placement_id` |
| `task_start` | 저장·제출 등 작업 요청 시작 | `interaction_id`, `action_id` |
| `task_success` | 서버 성공과 반영 확인 | `interaction_id`, `action_id` |
| `task_failure` | 작업이 오류로 종료 | `interaction_id`, `action_id`, `failure_category` |

사용자 취소는 실패로 세지 않는다. 취소율이 필요한 특정 흐름은 별도 승인 후 `task_cancel`을 추가한다.

### 7.2 클라이언트가 전송하는 필드

| 필드 | 형식 | 규칙 |
|---|---|---|
| `event_id` | UUID | 이벤트 중복 저장 방지 |
| `event_type` | enum | 위 7개 값만 허용 |
| `occurred_at` | UTC ISO-8601 | 서버 시각보다 24시간 이전 또는 5분 이후면 거부 |
| `session_id` | UUID | `sessionStorage` 단위, 로그아웃 시 폐기 |
| `view_id` | UUID | 한 번의 화면 진입 단위 |
| `interaction_id` | UUID 또는 없음 | 클릭부터 성공·실패까지 같은 값 |
| `feature_id` | 최대 80자 | 기능 레지스트리 ID |
| `screen_id` | 최대 100자 | 역할 앱의 안정 화면 ID |
| `surface` | enum | `admin`, `teacher`, `student` |
| `route_template` | 최대 180자 | 실제 ID와 쿼리를 제거한 템플릿만 허용 |
| `cta_id` | 최대 80자 또는 없음 | CTA의 안정 의미 ID |
| `action_id` | 최대 80자 또는 없음 | 저장·제출 등 작업 ID |
| `placement_id` | 최대 80자 또는 없음 | `bottom_tab`, `drawer.learning`, `dashboard.primary` 등 |
| `position_index` | 0 이상 정수 또는 없음 | 같은 영역 안의 노출 순서 |
| `failure_category` | enum 또는 없음 | `validation`, `network`, `permission`, `server`, `unknown` |
| `device_class` | enum | `mobile`, `tablet`, `desktop` |
| `client_release` | 최대 64자 | 프론트엔드 배포 SHA |
| `catalog_version` | 최대 32자 | 기능 레지스트리 버전 |
| `synthetic` | boolean | E2E·검증 트래픽 표시 |

### 7.3 서버가 결정하는 필드

다음 값은 클라이언트가 보내지 않으며 서버가 인증·테넌트 경계에서 채운다.

| 필드 | 생성 방식 |
|---|---|
| `tenant_id` | `request.tenant`; 없으면 전체 요청 거부 |
| `role` | 해당 테넌트의 활성 `TenantMembership.role` |
| `audience_group` | 서버가 원본 역할에서 파생 |
| `actor_hash` | 전용 비밀키로 `tenant_id:user_id`를 HMAC 처리 |
| `received_at` | 서버 수신 시각 |
| `is_impersonated` | JWT의 `impersonated_by` claim으로 결정 |

사용자 PK도 원본 이벤트 테이블에 저장하지 않는다.

### 7.4 저장하지 않는 정보

- 사용자명, 이름, 전화번호, 이메일
- 학생 ID, 선택한 자녀 ID, 강의·시험·제출물 등 엔티티 ID
- 검색어, 메모, 메시지, 답안, 점수, 파일명
- URL 쿼리 문자열이나 hash
- 원본 referrer URL
- 자유 형식 `properties` JSON
- API 응답 본문이나 오류 메시지 원문

## 8. 수집 API 계약

### 8.1 엔드포인트

`POST /api/v1/core/product-analytics/events/batch/`

- 인증 필수
- 현재 테넌트의 활성 멤버십 필수
- 테넌트의 `product_usage_analytics_enabled` 기능 플래그가 켜진 경우만 저장
- 최대 20개 이벤트
- 최대 요청 본문 64KB
- 같은 `event_id`는 중복 저장하지 않음
- 전체 배치 계약이 잘못되면 `400`
- 저장 성공은 `202`
- 수집 장애가 제품 작업의 성공·실패에 영향을 주지 않음

요청 예:

```json
{
  "schema_version": 1,
  "events": [
    {
      "event_id": "4ad0c7c9-bf27-40a3-a78d-1ca324fcfb33",
      "event_type": "screen_view",
      "occurred_at": "2026-07-29T07:31:04.120Z",
      "session_id": "f251db91-67ab-4608-a233-30d408c1ed57",
      "view_id": "227ef0cb-2103-47e9-bdcc-83585862dfc6",
      "feature_id": "attendance.manage",
      "screen_id": "teacher.attendance.session",
      "surface": "teacher",
      "route_template": "/teacher/attendance/:sessionId",
      "device_class": "mobile",
      "client_release": "0123456789abcdef",
      "catalog_version": "2026-07-29"
    }
  ]
}
```

응답 예:

```json
{
  "accepted": 1,
  "duplicates": 0
}
```

클라이언트는 이벤트를 메모리에서만 모아 5초 또는 10개 단위로 전송한다. 한 번 실패하면 메모리에서 한 번만 재시도하고, 계속 실패하면 버린다. 분석 수집 때문에 사용자 요청을 기다리게 하거나 화면 오류를 표시하지 않는다.

## 9. 저장 및 보존 정책

### 9.1 원본 이벤트

- 보존: 30일
- 목적: 최근 퍼널, 오류, 위치별 비교와 데이터 품질 진단
- 사용자·엔티티 원본 ID 없음
- 인덱스는 날짜, 역할, 기능, 이벤트 종류, CTA 위치와 상호작용 ID 중심

### 9.2 일별 사용자 집계

- 보존: 400일
- 단위: 날짜 × 테넌트 × 익명 사용자 × 역할 × 기능 × 화면 × 이벤트 × 위치
- 반복 이벤트는 `count`, `first_at`, `last_at`으로 축약
- 연간 계절성과 학기별 차이를 볼 수 있음

### 9.3 정리 작업

- 집계 작업은 재실행해도 결과가 같아야 한다.
- 삭제 작업은 먼저 대상 날짜, 테넌트 수, 원본 행 수와 집계 완료 여부를 출력한다.
- 운영 자동 삭제를 켜기 전 보존 정책에 대한 명시적 승인을 받는다.
- 사용자 작성 데이터와 도메인 데이터는 절대 대상에 포함하지 않는다.

## 10. 핵심 지표

모든 기본 지표는 `synthetic=false`, `is_impersonated=false`만 포함한다.

| 지표 | 계산 |
|---|---|
| 역할 활성 사용자 | 기간 내 이벤트가 하나 이상인 고유 `actor_hash` |
| 기능 방문 사용자 | `screen_view`의 고유 `actor_hash` |
| 기능 참여 사용자 | `screen_engaged`의 고유 `actor_hash` |
| 역할 내 방문률 | 기능 방문 사용자 ÷ 역할 활성 사용자 |
| 참여 방문률 | `screen_engaged` 화면 방문 수 ÷ `screen_view` 화면 방문 수 |
| CTA 클릭률 | 고유 `interaction_id` 클릭 수 ÷ CTA 노출 수 |
| 작업 시작률 | 고유 작업 시작 수 ÷ 고유 CTA 클릭 수 |
| 작업 완료율 | 고유 작업 성공 수 ÷ 고유 작업 시작 수 |
| 작업 실패율 | 고유 작업 실패 수 ÷ 고유 작업 시작 수 |
| 반복 사용 빈도 | 작업 성공 수 ÷ 성공 사용자 수 ÷ 기간 주 수 |
| 추세 | 최근 28일 역할 활성 사용자당 사용량과 직전 28일의 차이 |
| 마지막 관측 | 마지막 비합성 `screen_view` 또는 `task_success` 시각 |

단순 이벤트 총량은 일부 헤비 유저에게 왜곡될 수 있으므로 항상 고유 사용자 수와 함께 보여준다.

## 11. “거의 사용하지 않음” 판정

기본 관찰 기간은 28일이다. 기대 빈도가 `monthly`이면 90일, `rare`이면 자동 저사용 판정을 하지 않는다.

다음 표시는 표본이 충분할 때만 붙인다.

- 역할 활성 사용자가 20명 미만이면 `표본 부족`
- CTA 노출이 100회 또는 고유 노출 사용자가 20명 미만이면 CTA 위치 결론을 보류
- 테넌트 단일 필터에서 고유 사용자가 5명 미만인 셀은 수치를 숨김
- 28일 동안 역할 활성 사용자 20명 이상인데 참여 사용자가 0명이면 `관측된 사용 없음`
- 역할 내 참여 사용률이 5% 미만이고 참여 사용자가 5명 미만이면 `매우 낮음`
- 그 외에는 같은 역할·기대 빈도 그룹 안의 상대 사분위를 사용

저사용 표시는 삭제 결론이 아니라 조사 신호다.

## 12. 운영 분석 화면

플랫폼 운영자 전용 `/dev/product-analytics` 화면을 만든다. 테넌트 관리자에게 교차 테넌트 데이터 접근 권한을 주지 않는다.

### 12.1 필터

- 기간: 7일, 28일, 90일, 사용자 지정
- 집단: 선생님·직원, 학부모, 학생
- 원본 역할: owner, admin, teacher, staff, parent, student
- 화면: admin, teacher, student
- 테넌트: 전체 또는 하나
- 기기: mobile, tablet, desktop
- 합성·대리 로그인 포함 여부: 기본 꺼짐

### 12.2 화면 구성

1. 역할 활성 사용자, 기능 참여율, 완료율, 실패율, 수집 품질 요약
2. 기능별 방문·참여·완료 표
3. 기능별 최근 28일 대 직전 28일 추세
4. CTA 위치별 노출, 클릭률, 완료율
5. 화면별 주요 진입 위치 분포
6. `관측된 사용 없음`, `매우 낮음`, `표본 부족` 목록
7. 잘못된 이벤트, 알 수 없는 기능 ID, 배치 거부율, 수집 지연 품질 카드

기능 상세에서는 같은 기능의 `admin`과 `teacher` 진입 위치를 나란히 비교할 수 있어야 한다.

## 13. CTA와 노출 우선순위 결정 규칙

사용량만으로 순서를 자동 결정하지 않는다. 전략 중요도, 기대 빈도, 발견 가능성, 완료 건강도를 함께 본다.

| 관측 | 해석 | 기본 조치 |
|---|---|---|
| 참여·완료가 모두 높음 | 반복 수요가 검증됨 | 홈, 상단, 하단 탭 등 빠른 진입 유지·강화 |
| 노출은 낮지만 클릭률·완료율이 높음 | 찾으면 잘 쓰는 숨은 기능 | 진입 위치를 한 단계 올리거나 관련 화면에 문맥 CTA 추가 |
| 노출은 높지만 클릭률이 낮음 | 문구·대상·위치가 맞지 않음 | CTA 문구와 역할 적합성 검토 |
| 클릭은 높지만 완료율이 낮음 | 흐름 중 마찰 또는 오류 | 노출 확대 전에 기능 흐름을 먼저 수정 |
| 사용은 낮지만 전략 중요도가 `core` | 교육·발견 문제일 수 있음 | 온보딩, 빈 상태 안내, 문맥 CTA 실험 |
| 사용도 낮고 전략 중요도도 `optional` | 우선 노출 비용이 큼 | 하위 메뉴 이동 또는 통합 검토 |
| 기대 빈도가 `rare` | 낮은 횟수가 정상 | 접근 가능성과 성공률만 보고 순위 하향 자동 판단 금지 |

첫 배치 후 28일 동안은 위치를 바꾸지 않고 기준선을 만든다. 이후 한 번에 한 가지 위치·문구만 변경하고 `client_release`, `placement_id`, `position_index`로 전후 28일을 비교한다.

## 14. 개인정보·보안·테넌트 원칙

- 모든 수집은 인증된 사용자와 확정된 요청 테넌트에 한정한다.
- 요청 본문의 `tenant`, `role`, `user` 값은 받지 않는다.
- 테넌트가 없거나 멤버십이 모호하면 저장하지 않는다.
- 사용자 식별은 전용 HMAC 키를 사용한 `actor_hash`만 저장한다.
- 대리 로그인 JWT는 `is_impersonated=true`로 저장하고 기본 집계에서 제외한다.
- 사용자 화면에는 개인 사용 실적이나 순위를 노출하지 않는다.
- 운영 분석 API는 플랫폼 관리자 권한과 접근 감사 로그를 요구한다.
- 원시 URL, 쿼리, 엔티티 ID, 자유 텍스트가 저장되지 않는지 자동 테스트한다.
- 운영 전 현재 개인정보 처리방침과 보존 기간에 대한 제품·법무 검토를 완료한다.

## 15. 데이터 품질과 운영 신호

다음 값을 구조화 로그와 운영 분석 품질 카드에서 본다.

- 수신 배치 수, 이벤트 수, 중복 수
- 계약 오류로 거부된 배치 수
- 기능 플래그가 꺼져 무시된 수
- 알 수 없는 `feature_id`와 `catalog_version`
- 10분 이상 늦게 도착한 이벤트 비율
- 합성·대리 로그인 이벤트 비율
- 일별 역할별 `screen_view`가 갑자기 0이 된 경우
- 원본 이벤트 테이블 일일 증가량과 저장 용량
- 분석 API p95와 DB 쿼리 시간

수집 로그에도 원본 이벤트 전체나 사용자 정보는 남기지 않는다. 이벤트 수, 기능 ID, 서버 결정 테넌트 ID, 거부 사유 코드만 기록한다.

## 16. DB 가용성과 테넌트 분리 기준

이 절은 구현된 현재 상태가 아니라 **승인 후 적용할 목표 의사결정 계약**이다. 현재 실행 진실은 여전히 Single-AZ·단일 `default` DB이며, 실제 변경 시에는 운영 SSOT, 파라미터, 배포 스크립트와 복구 문서를 함께 갱신해야 한다.

### 16.1 세 문제를 분리한다

| 문제 | 맞는 수단 | 해결하지 못하는 것 |
|---|---|---|
| DB 인스턴스·AZ 장애로 전체 서비스가 멈춤 | RDS Multi-AZ | 읽기 성능 확장, 테넌트별 noisy-neighbor |
| 조회 트래픽 때문에 writer가 압박됨 | 지연 허용 조회에 한정한 read replica | writer 장애 격리, 테넌트별 복구 |
| 특정 테넌트의 부하·계약·복구 요구가 다른 고객에게 영향을 줌 | shared pool + 선택적 dedicated data plane | 공통 control plane 장애 |

preprod용 논리 DB나 같은 RDS 인스턴스 안의 테넌트별 database/schema는 컴퓨트, 연결 한도, 스토리지와 장애 지점을 공유한다. 따라서 이를 안정성 목적의 “테넌트 분리”로 계산하지 않는다.

### 16.2 현재 결정

1. 지금 애플리케이션 멀티 DB router를 도입하지 않는다.
2. 다음 DB 투자는 테넌트 sharding이 아니라 Multi-AZ 준비와 테넌트별 부하 계측이다.
3. 모든 테넌트를 database-per-tenant 또는 schema-per-tenant로 바꾸지 않는다.
4. 분리가 필요해지면 대부분은 shared pool에 두고 예외 테넌트만 전용 data plane으로 옮기는 하이브리드 구조를 사용한다.
5. 제품 사용 분석 이벤트는 초기에는 중앙 운영 DB에 두되, 그 자체의 쓰기·용량 증가를 테넌트 업무 부하와 별도로 측정한다.

전면 database-per-tenant는 현재 규모에서 연결 수, 마이그레이션 수, 백업·복구 대상과 운영 비용을 테넌트 수만큼 늘린다. schema-per-tenant도 마이그레이션 복잡도는 늘지만 RDS 장애와 자원 경합은 격리하지 못한다.

### 16.3 Multi-AZ 도입 게이트

Multi-AZ는 다음 **서비스 중요도 게이트 중 하나**가 처음 충족되는 시점에 운영 변경 후보가 된다.

- 계약되거나 내부 승인된 DB RTO가 10분 이하이다.
- 서로 독립된 운영 테넌트 2곳 이상이 평일 핵심 업무에 서비스를 사용하여 한 번의 DB 장애가 복수 고객 장애가 된다.
- 단일 테넌트라도 출석, 시험, 결제 등 정해진 시간의 중단 비용이 월 Multi-AZ 증분 비용보다 크다.
- 최근 90일 안에 DB 인스턴스·AZ 장애 또는 수동 복구가 필요한 동급 사고가 한 번 발생했다.

후보가 되면 다음 **비용·복구 게이트를 모두** 확인하고 명시적 생산 변경으로 실행한다.

- Academy 태그 기준 30일 비용이 확보되어 계정 전체 비용과 프로젝트 비용이 분리된다.
- AWS 변경 견적의 월 증분액과 20% 여유를 더해도 승인된 월 인프라 예산의 85% 이내이다.
- 최신 스냅샷 복원 리허설, 애플리케이션 연결, `/healthz`, 핵심 사용자 흐름 검증이 성공한다.
- Multi-AZ 전환 중·후 연결 수, failover, 롤백·지원 담당자와 점검 시간이 정해져 있다.

서비스 중요도 게이트가 충족됐지만 비용 게이트를 통과하지 못하면 Single-AZ를 조용히 유지하지 않는다. 예산을 올리거나 신규 테넌트 확대를 보류하고, 승인된 위험 예외와 만료일을 남긴다. 기존의 매출액 하나만으로 도입 시점을 결정하지 않는다.

### 16.4 read replica 도입 게이트

Multi-AZ standby는 읽기 endpoint가 아니다. read replica는 다음 조건을 모두 28일 동안 만족할 때만 검토한다.

- DB 소요 시간의 70% 이상이 읽기이고, 느린 조회·인덱스·N+1을 먼저 수정했다.
- writer CPU 또는 DB wait가 반복적으로 사용자 SLO를 위협하지만 쓰기 자체는 병목이 아니다.
- 후보 조회가 replica lag를 허용하며, 읽은 직후 쓰는 일관성을 요구하지 않는다.
- replica와 데이터 전송·관측 비용이 writer 상향 비용보다 유리하다.

운영 통계·과거 리포트처럼 지연 허용 조회만 replica로 보낸다. 인증, 멤버십, 권한, 결제, 메시징, 출석·제출 상태, idempotency와 상태 머신 조회는 writer에 남긴다.

### 16.5 테넌트 data plane 분리 게이트

다음 **즉시 분리 사유** 중 하나가 있으면 부하와 무관하게 dedicated data plane을 검토한다.

- 계약·법무·데이터 레지던시가 물리적 DB 격리를 요구한다.
- 해당 테넌트만의 백업 보존, 복구 시점 또는 RPO/RTO가 필요하다.
- 해당 테넌트의 작업이 다른 테넌트와 독립적으로 중지·복구되어야 한다.

그 외 성능 목적 분리는 아래 조건을 모두 만족할 때만 한다.

1. 쿼리·인덱스·연결 누수와 불필요한 동기 작업을 먼저 고쳤다.
2. 연속된 두 28일 구간에서 한 테넌트가 전체 DB 실행 시간 또는 쓰기의 30% 이상을 차지한다.
3. 같은 기간 그 부하와 함께 다른 테넌트의 핵심 API p95 SLO 위반, 연결 80% 초과, 또는 writer CPU/DB wait 병목이 반복된다.
4. 전용 DB, 백업, 모니터링, 복구 리허설과 운영 시간을 합친 월 비용이 해당 계약에 포함되며, 전용 인프라 비용이 해당 테넌트 월 반복 매출의 20% 이내이거나 별도 격리 요금으로 회수된다.

한 테넌트가 전체 저장량의 25%를 넘고 90일 안에 공용 DB의 저장 여유를 소진할 것으로 예측되는 경우도 같은 검토를 시작한다. 반대로 여러 테넌트가 비슷하게 성장하면 특정 테넌트를 분리하지 않고 공용 writer 상향, 파티셔닝 또는 저장 정책을 먼저 검토한다.

### 16.6 목표 하이브리드 구조

분리 시에도 control plane은 공용으로 유지한다.

- control plane: `Tenant`, `TenantDomain`, 인증·멤버십, 기능 플래그, data plane 라우팅·schema version 메타데이터
- shared data plane: 기본 도메인 데이터와 대부분의 테넌트
- dedicated data plane: 게이트를 통과한 테넌트의 이동 가능한 도메인 데이터
- 중앙 제품 분석: 역할별 교차 테넌트 집계. 계약이 중앙 분석을 금지하면 해당 테넌트는 수집 제외 또는 별도 분석 저장소 사용

DB 간 foreign key와 원자 transaction은 사용할 수 없으므로, 실제 분리 전 도메인별 테이블 소유권과 교차 DB join을 목록화한다. 요청은 control plane에서 테넌트를 fail-closed로 확정한 뒤 `data_plane_key`를 해석한다. 비동기 작업은 `tenant_id`만 믿고 alias를 고정하지 않으며 작업 시작 시 최신 라우팅을 다시 해석한다.

### 16.7 이 분석 기능과 DB 확장의 관계

제품 행동 이벤트는 기능 우선순위 근거이지 테넌트 DB 분리 근거가 아니다. 분리 판단에는 서버가 측정한 테넌트별 DB 실행 시간, 쿼리 수, 쓰기 수, 작업 수와 저장 추정치를 사용한다. SQL 본문과 사용자 입력은 수집하지 않는다.

7일 파일럿을 90일로 환산했을 때 제품 분석 원본·인덱스가 현재 DB 여유 공간의 20%를 넘거나, 분석 수집이 writer DB 시간 또는 쓰기량을 10% 이상 늘리면 전체 활성화를 멈춘다. 이 경우 이벤트 범위를 줄이거나 중앙 분석 저장소를 별도 분리하며, 이를 이유로 업무 도메인의 테넌트 sharding을 시작하지 않는다.

## 17. 출시와 의사결정 일정

| 단계 | 범위 | 종료 조건 |
|---|---|---|
| 로컬 | 개발·테스트 환경 | 이벤트 계약, PII 차단, 실패 무영향 테스트 통과 |
| 격리 사전검증 | preprod | 마이그레이션, 수집, 집계, 권한, 대시보드 확인 |
| 1차 파일럿 | 플랫폼 운영 테넌트 7일 | 중복·거부·누락이 허용 범위 안이고 UI 영향 없음 |
| 2차 파일럿 | 대표 테넌트 2~3곳 7일 | 세 역할과 세 surface 데이터가 분리되고 테넌트별 분석 부하가 보임 |
| 전체 수집 | 기능 플래그 순차 활성화 | 수집 품질, 90일 용량, writer 부하 게이트 통과 |
| 첫 UX 결정 | 전체 수집 28일 후 | 표본 기준을 만족한 기능만 조정 후보로 선정 |
| DB 구조 리뷰 | 매 28일 | Multi-AZ, read replica, tenant split 게이트를 각각 판정 |
| 변경 검증 | 변경 후 28일 | 같은 역할·기능의 전후 지표와 완료 건강도 비교 |

## 18. 완료 조건

다음이 모두 충족되어야 이 기능이 제품적으로 완료된 것으로 본다.

- 등록된 모든 인증 후 주요 라우트가 역할과 surface별 `screen_view`를 남긴다.
- 같은 화면 진입에서 화면 이벤트가 중복 기록되지 않는다.
- CTA 노출은 실제 가시성 조건을 만족할 때만 기록된다.
- 주요 업무의 클릭 → 시작 → 성공·실패가 같은 `interaction_id`로 연결된다.
- 역할과 테넌트는 서버 값이며 클라이언트가 위조할 수 없다.
- 이벤트 저장 실패가 사용자 업무를 막거나 오류 알림을 만들지 않는다.
- 학부모와 학생이 같은 앱을 사용해도 별도 집계된다.
- E2E, 개발, 대리 로그인 트래픽이 기본 제품 지표에서 제외된다.
- 30일 원본 보존과 400일 집계 보존이 검증된다.
- 플랫폼 운영 화면에서 역할, 기간, 기능, CTA 위치별 비교가 가능하다.
- 표본 부족과 드문 기능을 저사용으로 오판하지 않는다.
- 첫 28일 기준선이 쌓이기 전 자동 또는 일괄 메뉴 재정렬을 하지 않는다.
- 제품 분석이 추가한 writer 부하와 90일 저장 예측이 파일럿 게이트 안에 있다.
- Multi-AZ와 테넌트 분리는 제품 사용량이 아니라 별도 비용·안정성·부하 지표로 판정된다.

## 19. 확정된 설계 결정

- 제품 사용 분석은 Sentry와 분리된 1st-party 경로로 만든다.
- 최초 버전은 로그인 후 화면만 측정한다.
- 사용자 원본 ID와 자유 텍스트를 저장하지 않는다.
- 기능 레지스트리는 프론트엔드 실행 SSOT로 관리한다.
- 원본 역할과 보고 집단을 모두 유지한다.
- 원본 이벤트는 30일, 일별 익명 사용자 집계는 400일 보존한다.
- 운영 분석은 플랫폼 관리자 전용 `/dev` 화면에 둔다.
- 메뉴·CTA 순위는 자동 변경하지 않고 28일 기준선 후 수동 실험한다.
- 현재는 애플리케이션 멀티 DB를 도입하지 않고 Multi-AZ 준비와 테넌트별 부하 계측을 먼저 한다.
- 장래 테넌트 분리는 전면 database-per-tenant가 아니라 shared pool + 선택적 dedicated data plane 구조로 한다.
- Multi-AZ, read replica, tenant split은 서로 다른 게이트로 승인한다.
