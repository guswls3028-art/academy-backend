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

## 원자성·동시성·알림

서비스는 세션을 날짜·시작 시각·ID 순으로 잠그고, 교직원 요청의 학생도 ID
순으로 잠근다. 이후 기존 단일 참가자 생성 규칙을 학생 × 세션 조합마다 적용한다.
정원 마감, 잘못된 대상, 권한 오류가 하나라도 발생하면 요청 전체를 롤백한다.
따라서 2명 × 2시간대 요청이 일부만 저장되는 상태는 없다.

이미 같은 학생·세션의 활성 예약이 있으면 기존 단일 생성 계약처럼 충돌로
거부하고 요청 전체를 롤백한다. 알림은 트랜잭션 커밋 뒤 각 참가자별
`clinic_reservation_created` 이벤트로 요청하며, 승인된 알림톡 템플릿이 없으면
기존 메시징 정책대로 실패 폐쇄한다.

모델이나 마이그레이션은 바뀌지 않는다. 기존 데이터는 그대로 유지되고 다중
예약도 기존 `SessionParticipant` 행들의 집합이므로 조회·출석·취소·패스카드
규칙을 그대로 따른다.

## 구현과 검증

- 입력·응답 직렬화: `apps/domains/clinic/serializers.py`
- 트랜잭션·잠금: `apps/domains/clinic/services/lifecycle.py`
- API 액션·커밋 후 알림: `apps/domains/clinic/views/participant_views.py`
- 집중 API 회귀: `tests/test_clinic_multi_slot_booking_api.py`

```powershell
$env:DJANGO_SETTINGS_MODULE='apps.api.config.settings.test'
python -m pytest tests/test_clinic_multi_slot_booking_api.py -q
python manage.py makemigrations --check --dry-run
python manage.py check --settings apps.api.config.settings.test
```

집중 회귀는 학생의 2시간대 신청, 한 세션 정원 마감 시 전체 롤백, 교직원의
2명 × 2시간대 추가, 학생의 임의 `student_ids` 거부를 검증한다.
