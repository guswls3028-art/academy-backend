# 클리닉 다중 시간대 예약 계약

## 목적과 사용자 흐름

클리닉 수업은 한 시간 단위 세션으로 유지한다. 학생이나 교직원이 같은 날짜의
여러 세션을 한 번에 선택하면, 각 시간대마다 독립적인 `SessionParticipant`를
만든다. 예를 들어 17:00–18:00과 18:00–19:00을 함께 선택하면 화면에는
17:00–19:00 이용으로 요약하지만 데이터와 정원은 두 세션에서 각각 관리한다.

- 학생은 자신의 계정에 연결된 학생 한 명과 같은 날짜의 시간대 여러 개를
  선택해 신청한다.
- 교직원은 활성 학생 여러 명과 같은 날짜의 시간대 여러 개를 선택해 일괄
  추가한다.
- 기존 단일 생성 `POST /clinic/participants/`와 일정 변경 API는 호환을 위해
  유지한다. 일정 변경은 계속 하나의 새 세션만 받는다.
- 시간대가 하나일 때만 `preferred_start_time`과 `preferred_end_time`을 받을 수
  있다. 여러 세션의 부분 구간을 하나의 희망 시간으로 추정하지 않는다.
- 세션의 `allow_multi_slot_booking`이 `false`이면 학생 한 명은 해당 날짜에
  `pending` 또는 `booked` 예약을 하나만 가질 수 있다. `true`인 세션끼리만
  같은 날짜의 여러 시간대를 예약할 수 있다.

프론트엔드 상호작용 계약은 학생 앱
`src/app_student/domains/clinic/README.md`와 선생님 앱
`src/app_teacher/domains/clinic/README.md`가 소유한다.

## API 계약

`POST /api/v1/clinic/participants/bulk-create/`

```json
{
  "session_ids": [701, 702],
  "student_ids": [801, 802],
  "student_request_memo": "두 시간 연속 참여"
}
```

성공하면 `201`과 생성된 참가자 목록을 돌려준다.

```json
{
  "count": 4,
  "participants": []
}
```

- `session_ids`는 중복 없는 1–20개이며 모두 현재 테넌트의 같은 날짜여야 한다.
- 학생 요청은 `student_ids`를 받지 않고 인증된 학생만 사용한다.
- 교직원 요청은 중복 없는 활성 학생 1–100명을 명시한다.
- 한 요청의 학생 × 시간대 조합은 최대 500개다.
- 학생 신청은 세션 대상 강의·학년·학교·정원 규칙을 기존 단일 예약과 동일하게
  적용한다. 교직원 추가도 기존 수동 추가 권한과 상태 규칙을 유지한다.
- 세션 또는 학생이 없거나 다른 테넌트에 속하면 존재를 추정하지 않고 실패한다.
- 여러 시간대를 선택했는데 하나라도 `allow_multi_slot_booking=false`이면 아무
  참가자도 만들지 않고 `409`로 실패한다.

## 세션 정책과 초기값

`Tenant.clinic_allow_multi_slot_booking_default`는 새 세션의 기본값이고,
`Session.allow_multi_slot_booking`은 생성 시점에 그 값을 복사한 snapshot이다.
세션 생성 요청이 값을 명시하면 명시값이 우선하며, 이후 테넌트 기본값을 바꿔도
이미 생성된 세션은 바뀌지 않는다.

- 기본값은 `false`다.
- 초기 운영값은 `tchul=false`, `godmin=false`, `limglish=true`다.
- 기존 `limglish` 세션은 migration에서 `true`, 나머지 기존 세션은 `false`로
  설정한다.
- 세션을 `true`에서 `false`로 바꿔도 기존 참가자 행은 보존한다. 이후 같은 날짜의
  충돌하는 새 예약만 막는다.
- `cancelled`, `rejected`, `no_show`는 활성 충돌로 보지 않는다.

날짜 비교의 기준은 tenant로 격리된 `Session.date`다. 이 값은 클리닉 운영의
현지 날짜를 직접 저장하는 `DateField`이므로 서버 UTC 시각에서 날짜를 다시
추정하지 않는다. tenant나 session을 현재 요청 범위에서 확인할 수 없으면 다른
tenant를 추정하지 않고 실패 폐쇄한다.

## 원자성·동시성·알림

서비스는 학생을 ID 순으로 먼저 잠그고 세션을 날짜·시작 시각·ID 순으로 잠근다.
단일 생성, 학생 bulk, 교직원 bulk, 일정 변경이 모두 같은 학생 row lock을
사용하므로 서로 다른 세션을 향한 동시 요청도 같은 날짜 정책을 우회하지 못한다.
이후 기존 단일 참가자 생성 규칙을 학생 × 세션 조합마다 적용한다.
정원 마감, 잘못된 대상, 권한 오류가 하나라도 발생하면 요청 전체를 롤백한다.
따라서 2명 × 2시간대 요청이 일부만 저장되는 상태는 없다.

이미 같은 학생·세션의 활성 예약이 있거나 같은 날짜 정책이 충돌하면 단일 생성과
bulk 모두 `409`로 거부하고 요청 전체를 롤백한다. 일정 변경은 기존 예약을 충돌
검사에서 제외한 뒤 새 세션을 확보하고 기존 행을 취소하므로 OFF 세션 간의 정상적인
1:1 변경은 허용한다. 알림은 트랜잭션 커밋 뒤 각 참가자별
`clinic_reservation_created` 이벤트로 요청하며, 승인된 알림톡 템플릿이 없으면
기존 메시징 정책대로 실패 폐쇄한다.

다중 예약은 기존 `SessionParticipant` 행들의 집합이므로 조회·출석·취소·패스카드
규칙을 그대로 따른다. 학생 직접 예약 생성·일정 변경·취소 알림은 기존 계약대로
학생과 학부모 모두에게 보내며, 교직원 수신자 선택 규칙은 변경하지 않는다.

참가자와 보충 대상의 상태 변경은 일반 detail `PATCH/PUT/DELETE`로 허용하지 않는다.
예약 생성·일정 변경·상태 변경·완료·완료 취소·하원·오늘 계획 action만 각 service의
잠금과 감사 규칙을 통과한다. 일정 변경은 이전 참가자의 반복 알림을 취소하고, 이전
오늘 계획 행을 `booking_changed`로 닫은 뒤 새 세션의 대상 강의에도 유효한 항목만 새
참가자에게 원자적으로 이어 준다. 취소·거절은 오늘 계획을 각각
`booking_cancelled`/`booking_rejected`로 닫는다. 세션 삭제도 cascade 전에 해당
참가자의 미래 반복 알림을 취소·redact한다. 예약 알림 dispatcher는 lifecycle 정리가
누락된 과거 행도 현재 tenant의 `booked` 참가자인지 다시 확인하고 아니면 발송하지 않는다.

자율학습 완료와 완료 취소는 `completion_history`에 actor와 시각을 append-only로
남긴다. 완료 취소는 현재 `completed_at/by`만 비우며 기존 완료 감사와 이미 생성된
알림 이력을 삭제하지 않는다. 별도 승인된 정정 템플릿이 없으므로 다른 trigger를
대용하지 않는다.

## 미등원 하원 감사 계약

`POST /api/v1/clinic/participants/{id}/checkout/`은 정상 등원 후 하원과, 현장에서
등원 처리를 놓친 예약 학생의 하원을 모두 기록한다. 두 경로 모두 기존
`checked_out_at`, `checked_out_by`를 사용하며 자율학습 `completed_at`은 건드리지
않는다.

- 정상 등원(`status=attended`, `checked_in_at` 존재)은 기존 빈 payload도 허용하고
  `checkout_mode=arrival_recorded`를 기록한다.
- 등원 기록이 없는 예약 확정 학생은 `confirm_without_arrival=true`와 현재
  `expected_session_id`, `expected_student_id`를 모두 보내야 한다. 이때
  `checkout_mode=arrival_not_recorded`를 기록하며 `status`나 `checked_in_at`을
  생성·추정하지 않는다.
- 예상 session/student가 바뀌면 `409`, 다른 tenant 참가자는 `404`, pending·취소·
  거절·결석·세션 미연결 행은 실패 폐쇄한다.
- 같은 참가자의 반복 하원 요청은 `200`으로 기존 결과를 돌려주고 하원 시각·처리자를
  다시 쓰지 않는다.
- `clinic_check_out` exact 승인 알림톡 봉투가 없으므로 현재 하원 API는 알림 요청을
  생성하지 않는다. 다른 클리닉 trigger로 대체하지 않는다.

## 구현과 검증

- 입력·응답 직렬화: `apps/domains/clinic/serializers.py`
- 트랜잭션·잠금: `apps/domains/clinic/services/lifecycle.py`
- API 액션·커밋 후 알림: `apps/domains/clinic/views/participant_views.py`
- tenant/session 정책: `apps/core/models/tenant.py`, `apps/domains/clinic/models.py`
- 집중 API 회귀: `tests/test_clinic_multi_slot_booking_api.py`
- 하원·등원 독립 회귀: `tests/test_clinic_operations_workflow_api.py`
- 상태 소유권·오늘 계획·패스카드·완료 감사 회귀:
  `apps/domains/progress/tests/test_generic_write_boundaries.py`,
  `tests/test_clinic_participant_plan_api.py`, `apps/domains/clinic/tests.py`

```powershell
$env:DJANGO_SETTINGS_MODULE='apps.api.config.settings.test'
python -m pytest tests/test_clinic_multi_slot_booking_api.py -q
python manage.py makemigrations --check --dry-run
python manage.py check --settings apps.api.config.settings.test
```

집중 회귀는 ON/OFF 단일·bulk 경로, 혼합 정책 원자성, 비활성 상태, ON→OFF,
일정 변경, tenant 격리, 초기 tenant/session 값과 PostgreSQL 동시 쓰기를 검증한다.
