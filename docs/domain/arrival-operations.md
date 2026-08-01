# 비정규 등원 예정 운영 계약

## 목적과 사용자 흐름

보강과 클리닉처럼 정규 수업 시각과 별개로 학생이 오는 경우, 교직원이 실제
등원 전에 시험지·오답 자료·강의실을 준비할 수 있게 **예정 등원**을 한곳에
모은다. 출석 상태를 예약 상태로 오용하지 않고, 보강의 명시적 입력과 기존
클리닉 예약을 읽기 전용 운영 현황으로 합치는 것이 핵심이다.

보강은 관리자 앱의 **강의 → 보강 차시 → 출결**에서 학생별 예정 날짜,
선택적인 시간, 준비 메모를 저장한다. 클리닉은 기존 예약·세션 운영 흐름을
그대로 사용한다. 두 출처는 대시보드와 우상단 알림에서 같은 조회 계약으로
표시된다. 프론트엔드 상호작용 계약은
[`ARRIVAL-OPERATIONS.md`](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/ARRIVAL-OPERATIONS.md)가 소유한다.

## 보강 저장 규칙

`apps.domains.attendance.models.Attendance`가 다음 값을 소유한다.

| 필드 | 의미 |
|------|------|
| `planned_arrival_date` | 예정에 포함한다는 명시적 기준 날짜. `null`이면 현황에 포함하지 않는다. |
| `planned_arrival_time` | 선택적인 예정 시각. 날짜만 저장하면 `시간 미정`으로 집계한다. |
| `memo` | 준비물, 시험지 등 교직원용 짧은 메모. 기존 출결 메모 필드를 재사용한다. |
| `status` | 실제 출결 결과. 예정 저장과 독립적으로 유지한다. |

- 예정 날짜·시간은 `session_type=SUPPLEMENT`인 차시에서만 수정할 수 있다.
- 시간은 날짜 없이 저장할 수 없으며 API 직렬화 검증이 같은 불변조건을 보장한다.
- 보강 차시 날짜를 예정 날짜로 자동 간주하지 않는다. 출결 행만 존재하는
  학생이 `시간 미정`으로 과대 집계되지 않게, 교직원이 날짜를 저장한 행만
  예정에 포함한다.
- 날짜·시간·메모를 비우면 예정에서 제거된다. 출결 행과 실제 상태는 삭제하거나
  되돌리지 않는다.
- 출결 상태가 `UNSET`이 아니면 오늘 현황에는 처리됨으로 남지만, 임박·지연
  알림에서는 제외한다. `INACTIVE`, `SECESSION` 행은 현황에서 제외한다.

## 통합 조회 계약

`GET /api/v1/lectures/attendance/arrival-overview/`는 요청의 인증된 테넌트에서
오늘을 포함한 향후 7일을 조회한다. 학생이 주중에 주말 등원을 예약해도 교직원이
미리 준비할 수 있는 범위이며, 별도 조회 조건은 받지 않는다. 보강 출결과 클리닉
참여자를 각각 한 번 조회한 뒤
애플리케이션 계층에서 날짜, 시간, 학생 이름 순으로 병합한다.

클리닉 출처는 `clinic.SessionParticipant`의 `booked`, `attended`, `no_show`를
대상으로 한다. 연결된 세션이 있으면 세션 날짜·시각·장소를 사용하고, 없으면
요청 날짜·시각을 사용한다. `attended`, `no_show`는 처리됨이다.

응답은 `generated_at`, `today`, `tomorrow`, `range_end`, `range_days`, 60분
임박 창, 요약과 항목을 포함한다. 요약의 `soon`, `overdue`, `time_unset`은
미처리 항목만 세며, `time_unset`은 7일 범위 전체의 시각 미정 준비 건을 센다.
`today`, `tomorrow`는 해당 날짜의 전체 예정 인원, `upcoming`은 7일 전체
예정 인원을 센다. 각 항목에는 출처,
학생, 강의·차시 또는 클리닉 세션 연결 ID, 위치, 메모, 처리·지연 여부가 있어
프론트가 자연스러운 상세 화면으로 이동할 수 있다.

## 권한·실패·보존 경계

- 기존 `AttendanceViewSet` 권한과 `request.tenant` 범위를 그대로 사용한다.
  기본 테넌트 추론이나 교차 테넌트 fallback은 없다.
- 조회 실패는 빈 현황으로 위장하지 않고 프론트가 재시도 상태로 표시한다.
- 통합 현황은 읽기 투영이며 클리닉 예약이나 출결 상태를 자동 수정하지 않는다.
- 스키마 변경은 nullable 필드만 추가하는 expand migration이다. 기존 출결
  데이터는 그대로 남고 새 현황에는 자동 포함되지 않는다. 조회는 기존 테넌트
  인덱스로 먼저 범위를 좁히며, 별도 복합 인덱스는 실측 필요가 생길 때 expand가
  충분히 배포된 뒤 contract 절차로 검토한다.

## 소유 구현과 검증

- 모델·직렬화: `apps/domains/attendance/models.py`, `serializers.py`
- 통합 조회: `apps/domains/attendance/services/arrival_overview.py`
- 클리닉 조회 어댑터: `apps/support/attendance/arrival_dependencies.py`
- API 액션: `apps/domains/attendance/views.py`
- 회귀 테스트: `apps/domains/attendance/tests/test_arrival_overview.py`

```powershell
$env:DJANGO_SETTINGS_MODULE='apps.api.config.settings.test'
python manage.py test apps.domains.attendance.tests.test_arrival_overview `
  --settings apps.api.config.settings.test --verbosity 2
python manage.py makemigrations --check --dry-run
python manage.py check --settings apps.api.config.settings.test
```

핵심 회귀는 보강 전용 저장, 날짜 없는 시간 거부, 미입력 보강 학생의 비집계,
7일 경계, 미래 시각 미정 집계, 두 출처의 2-query 병합, 테넌트 격리, 요청
테넌트 사용이다.
