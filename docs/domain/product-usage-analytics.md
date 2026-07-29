# 제품 사용 분석

**상태:** 운영 코드·마이그레이션 배포 완료, 전체 테넌트 수집 OFF

**제품 범위:** 선생님·직원, 학부모, 학생의 인증 후 화면 방문·CTA·대표 업무 완료 신호

**프런트 계약:** [PRODUCT-USAGE-ANALYTICS.md](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/PRODUCT-USAGE-ANALYTICS.md)

**DB 구조 판단:** [database-scaling-and-tenant-isolation.md](../infrastructure/database-scaling-and-tenant-isolation.md)

**최초 배포:** [v1.12.7](../releases/v1.12.7.md)

## 1. 목적과 의사결정 원칙

이 기능은 역할별로 실제 방문·참여·CTA 선택·업무 완료를 구분해 메뉴,
홈 카드, CTA 문구, 노출 위치와 우선순위를 판단하기 위한 제품 신호다.
개인 감시, 직원·학생 평가, 마케팅 추적, 자동 메뉴 재정렬에는 사용하지
않는다.

제품 변경은 다음 순서를 따른다.

1. 수집 품질과 표본을 확인한다.
2. 클릭보다 실제 업무 성공·실패를 먼저 본다.
3. 노출이 적고 완료가 높은 기능은 발견성 개선 후보로 본다.
4. 노출이 많고 클릭이 낮은 CTA는 문구·대상·위치를 검토한다.
5. 실패율이 높은 기능은 노출 확대 전에 흐름을 고친다.
6. `rare` 기능과 전략상 핵심 기능은 낮은 사용량만으로 숨기지 않는다.
7. 메뉴·CTA 우선순위는 최소 28일의 적격 기준선 전에는 바꾸지 않는다.

기본 보고 집단은 다음과 같다.

| 보고 집단 | 서버 역할 |
|---|---|
| 선생님·직원 | `owner`, `admin`, `teacher`, `staff` |
| 학부모 | `parent` |
| 학생 | `student` |

관리자 PC, 선생님 업무 화면, 학생·학부모 앱은 각각 `admin`,
`teacher`, `student` surface로 구분한다. 역할은 클라이언트 값을
신뢰하지 않고 현재 테넌트의 활성 멤버십에서 결정한다.

## 2. 현재 사용자 흐름

1. 인증 사용자와 현재 테넌트가 확정된다.
2. 프런트는 `Program.feature_flags.product_usage_analytics_enabled`가
   정확히 `true`이고 등록된 인증 라우트일 때만 메모리 큐를 만든다.
3. 화면 진입, 10초 가시 체류, 실제 노출된 CTA, CTA 선택과 계측이
   연결된 대표 업무의 시작·성공·실패를 익명 이벤트로 만든다.
4. 최대 20개 이벤트를 배치 수집 API로 보낸다.
5. 서버는 테넌트, 활성 멤버십, 기능 플래그와 전용 HMAC 키를 다시
   검증하고 역할·보고 집단·익명 actor hash를 서버에서 채운다.
6. 일별 rollup이 원본을 익명 사용자·역할·기능·화면·위치 차원으로
   축약한다.
7. 플랫폼 운영자는 `/dev/product-analytics`에서 7·28·90일, 역할,
   surface와 테넌트를 필터링해 본다.

현재 코드에는 22개 안정 기능 ID와 69개 인증 라우트 템플릿이 등록되어
있다. 기능 ID는 과거 데이터 의미를 보존하기 위해 재사용하지 않는다.

대표 task 계측은 선생님 출석, 학생 시험 제출, 학생·학부모 클리닉
작업에 연결되어 있다. 나머지 등록 라우트는 화면 방문·참여와 내부
진입 CTA 신호를 제공한다. 전체 업무 퍼널 확장은 잔여 작업이다.

## 3. 이벤트와 프라이버시 계약

| 이벤트 | 의미 |
|---|---|
| `screen_view` | 등록 화면 진입 |
| `screen_engaged` | 화면이 보이는 상태로 누적 10초 |
| `cta_impression` | 요소 50% 이상이 500ms 연속 노출 |
| `cta_click` | 등록 CTA 또는 내부 목적지 선택 |
| `task_start` | 계측 대상 업무 요청 시작 |
| `task_success` | 기존 업무 Promise/API가 성공 |
| `task_failure` | 기존 업무가 오류로 종료 |

클라이언트 필드는 안정 ID, UUID 상관관계, route template, surface,
위치·순서, 기기 분류, 릴리즈·카탈로그 버전과 합성 여부로 제한한다.
서버 serializer는 알 수 없는 필드를 거부하고, 실제 숫자 ID·UUID가
포함된 경로와 쿼리·hash가 있는 경로를 거부한다. 이벤트 시각은 서버
기준 24시간 이전부터 5분 이후 사이만 허용한다.

다음 값은 저장하지 않는다.

- 원본 user ID, 이름, 로그인 ID, 전화번호, 이메일
- 학생·학부모·강의·시험·제출물 등 도메인 엔티티 ID
- 검색어, 답안, 점수, 메시지, 메모, 파일명과 자유 형식 properties
- URL query/hash, referrer, API 본문, SQL, bind parameter

`actor_hash`는 분석 전용 키로 `tenant_id:user_id`를 HMAC-SHA256 처리한
값이다. Django `SECRET_KEY`를 재사용하지 않는다. 합성 트래픽과
대리 로그인 트래픽은 저장 시 표시하며 기본 제품 지표에서 제외한다.

## 4. API, 권한과 실패 동작

### 배치 수집

`POST /api/v1/core/product-analytics/events/batch/`

- 인증과 요청 테넌트의 활성 멤버십이 필요하다.
- 요청은 최대 20개·64KB이며 schema version은 1이다.
- 기능 플래그가 꺼져 있으면 저장 없이 `202`와
  `ignored=feature_disabled`를 반환한다.
- 기능 플래그가 켜졌지만 전용 HMAC 키가 없으면 `503`으로 닫힌다.
- `event_id` 중복은 재저장하지 않는다.
- 테넌트가 없거나 활성 멤버십이 없으면 `403`이다.
- 분석 저장 실패는 분석 요청만 실패하며 원래 출석·제출·클리닉 업무의
  성공 여부를 바꾸지 않는다. 프런트는 한 번만 재시도한 뒤 버린다.

### 운영 조회

`GET /api/v1/core/dev/product-analytics/overview/`

- `IsPlatformAdmin`만 허용한다.
- 기간은 7·28·90일, 필터는 tenant, 원본 role, surface다.
- 조회는 운영 감사 로그에 남지만 결과 데이터는 감사 payload에 넣지
  않는다.
- 단일 테넌트 필터에서 고유 actor가 1~4명이면 summary와 세부 셀을
  숨긴다.

### 롤아웃 제어

플랫폼 테넌트 관리 API의 `productUsageAnalyticsEnabled` 변경만 정식
토글 경로다. `true` 전환은 전용 HMAC 키가 없으면 `409
analytics_hash_key_missing`으로 거부된다. 일반 테넌트 관리자는 이
토글을 변경할 수 없다.

## 5. 저장, 집계와 보존

- `core.0051_product_usage_analytics`는 새 원본·일별 집계 테이블과
  인덱스만 추가한다. 기존 도메인 데이터는 변경하지 않는다.
- `ProductUsageEvent`: 원본 익명 이벤트, 정책상 30일 보존.
- `ProductUsageDailyActor`: 일별 익명 actor 집계, 정책상 400일 보존.
- `python manage.py rollup_product_usage --date YYYY-MM-DD`는 같은
  날짜 재실행 결과가 동일하다.
- `python manage.py purge_product_usage --before YYYY-MM-DD
  [--daily-before YYYY-MM-DD]`는 기본 dry-run이다.
- 원본 날짜의 rollup이 없으면 purge를 차단한다.
- 수동 실행의 실제 삭제는 정확한 범위를 검토한 뒤 `--execute`를 붙인다.
- `.github/workflows/product-usage-maintenance.yml`은 매일 03:25 KST에
  전날을 idempotent rollup하고 원본 30일·일별 집계 400일 보존을
  적용한다. GitHub OIDC로 healthy InService API 한 대에만 SSM command를
  보내며 rollup 누락, 인스턴스 부재 또는 command 실패 시 삭제 없이
  실패한다. 수동 dispatch 기본값은 purge dry-run이다.

`scripts/v1/ensure-product-analytics-hash-key.ps1`은 기존 production
SecureString JSON을 보존한 채 전용 384-bit 난수 HMAC key가 없을 때만
추가하고 exact parameter version을 readback한다. 키 값은 출력하지
않으며 기존 키가 짧거나 prod settings가 아니면 교체하지 않고 실패한다.

## 6. 현재 운영 상태

2026-07-30 KST 재검증 결과:

- 백엔드 분석 merge `8f7b8c511d2912883c0bec5b5b8d09d20c10ad6d`는
  현재 `origin/main`의 조상이다.
- 운영 API 후보 `5c58e9a9334b9f89b7a24de13550b2c48073d208`는
  상시 격리 개발환경, 임시 preprod, 운영 migration, ASG/ALB 교체와
  최종 digest 검증을 모두 통과했다.
- 운영 API digest는
  `sha256:1a984901c407b73e7326cd900659476452a92163bf427ff9c75c81814101cdff`다.
- 프런트 분석 merge `ab32657e7e6846ae3aaf272e57b12dbd0dd9b672`는
  운영 프런트 `a04a01c1c5ee1ff017ec7d692442482bd8deb369`의 조상이다.
- `/healthz`는 200, `/health`는 `healthy`와 `database=connected`다.
- 공개 Program readback 대상 7개 테넌트 모두 분석 플래그가 없어서
  `false`로 평가된다. 실제 행동 수집은 시작되지 않았다.
- 메뉴·CTA 위치·문구 변경과 멀티 DB 변경은 없다.

## 7. 운영·검증 경로

Backend focused:

```powershell
python manage.py check --settings apps.api.config.settings.test
python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test
python -m pytest apps/core/tests/test_product_analytics_ingestion.py apps/core/tests/test_product_analytics_queries.py apps/core/tests/test_product_analytics_retention.py apps/core/tests/test_product_analytics_rollout.py apps/core/tests/test_tenant_db_usage.py -q
```

Frontend focused 검증과 사용자 흐름은 프런트 정본을 따른다.

생산 변경은 [deployment-modes.md](../operations/deployment-modes.md)의
상시 개발환경 → 격리 preprod → 운영 게이트를 모두 통과해야 한다.
기능 플래그 활성화는 플랫폼 테넌트 상세 화면의 정식 API 경로로 tenant
하나씩 수행한다. 메뉴 우선순위 변경은 28일 기준선 이후 별도 결정이다.

남은 퍼널, 파일럿과 28일 리뷰는
[product-usage-analytics-remaining-work.md](../refactor/product-usage-analytics-remaining-work.md)에서
관리한다.
