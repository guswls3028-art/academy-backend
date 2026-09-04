# 읽기 전용 상태 detector

## 목적과 실행 경계

`check_state_integrity --tenant <정확한 tenant ID>`는 저장된 차시 시험 통과
projection과 현재 정본 계산을 비교하는 운영자 명령이다. API, 학생/교사 화면,
worker, 자동 수정 작업이 아니며 스케줄러에 자동 연결되지 않는다. 실행 권한은
관리 명령을 수행할 수 있는 운영자에게만 있다. 프런트엔드 변경은 없다.

```powershell
python manage.py check_state_integrity --tenant 123 --dry-run
python manage.py check_state_integrity --tenant 123 --limit 1000
```

첫 명령은 관측만 하고 DB INSERT와 외부 전달을 하지 않는다. 두 번째 명령은
운영 감사 영수증만 저장하고, 필요한 상태 전이를 기존 운영 Slack webhook
`DEV_ALERTS_WEBHOOK_URL`로 전달한다. 제품 알림톡/SMS, outbox, SQS를 사용하지
않는다. 설정 값이나 실제 수신처를 출력하지 않는다. receiver가 없어도 dry-run과
격리 테스트 DB의 loopback 전달 검증은 가능하지만 운영 알림 활성화 증거는 아니다.

## 첫 규칙: `session_exam_projection_v1`

소유 코드는 `apps/support/progress/state_detector_dependencies.py`이다.
기존 `SessionProgress`만 읽고 다음 정본에 따라 `exam_passed`를 비교한다.

- 수강·학생·강의·차시·시험의 정확한 tenant/관계 그래프.
- `ProgressPolicy`의 정규 차시 범위 및 정책/시험별 합격 기준.
- `get_target_exam_ids_for_session_enrollment`의 명시적 대상 명단. 대상 명단이
  없는 legacy 시험만 기존 차시 명단 계약을 따른다.
- `SessionProgressCalculator._aggregate_exam_results`의 대표 `Result`, 대표
  `ExamAttempt`, `NOT_SUBMITTED`, 강의별 `ExamLecturePolicy` 기준. 여러 시험은
  모든 대상 시험을 통과해야 한다. `Result` 없는 미응시와 nullable attempt의
  legacy snapshot은 기존 계산 계약을 그대로 따른다.

`calculate`, progress pipeline, 클리닉 해소 서비스는 호출하지 않는다. 정책이나
projection이 없는 경우 새로 만들지 않는다. 이 규칙의 `healthy`는 검사한 기존
projection의 범위에만 해당하며, 미생성 projection이나 제품 전체의 정상 판정이 아니다.

### 정상 예외와 비대상

보강/정책 범위 밖 차시, 명시적 비대상 시험, 비활성/대기 수강, 삭제 학생은
`excluded`로 센다. 해당 시험 또는 출처 없는 legacy 차시의 명시적 클리닉 해소
`MANUAL_OVERRIDE`, `WAIVED`, `CARRIED_OVER`, `SOURCE_REMOVED`, `NOT_SUBMITTED`,
`BOOKING_LEGACY`도 보수적으로 차시 전체를 제외한다. 같은 출처의 최신 차수만
해소 예외를 결정한다. 이전 면제 뒤 새 차수가 열렸으면 과거 면제로 숨기지 않는다.
과제 해소는 숫자 ID가
시험과 같아도 시험 예외로 취급하지 않는다. 이는 점수 합격 인정이 아니다.

과거 실패 attempt, stale `exam_meta`, 최초 완료 이력인 `completed_at`, 예약 취소,
등원 기록 없이 명시적으로 처리된 하원, 자율학습 완료/취소는 점수 통과의 근거가
아니다. detector는 이 필드를 조합해 새로운 제품 규칙을 만들지 않는다.
관련 정본: [시험](../domain/exam-grading.md),
[클리닉](../domain/clinic-booking.md).

### 비동기 갱신과 불완전 검사

v1은 projection/차시/정책/시험/attempt/Result/제출/해소/강의별 정책 갱신과 대상
명단 생성 중 최신 시각부터 5분 동안 재계산 정착을 기다린다. 이 시간은 detector의
보수적 관측 유예이며 worker 완료 SLA나 예약 reminder의 발송 유효시간이 아니다.
최신 또는 대표 attempt가 pending/grading이거나 해당 제출이 아직 처리 중이면
시간 경과와 무관하게 `deferred`다. 처리 지연 자체는 이 규칙의 탐지 대상이 아니다.

정책 누락, 알 수 없는 상태/정책, 불완전 tenant 관계, 잘못 연결된 대표 attempt,
계산 시각 누락, 쿼리 오류와 한도 초과는 검사 실패다. 실패/유예 검사를 `healthy`로
표시하거나 복구 알림을 보내지 않는다. 이미 확인한 모순은 검사 실패와 별개로 보존한다.

기본 projection 한도는 200, 명시적 상한은 1000, 차시별 각 원본 묶음은 500이다.
초과하면 샘플 결과를 전체 성공으로 간주하지 않고 실패한다. PostgreSQL snapshot은
REPEATABLE READ / READ ONLY, 쿼리별 timeout은 5초다. 차시 사이 및 검사 종료 시
30초 전체 검사 budget을 확인하며, 초과 결과는 실패한다. ORM SQL 쓰기도 별도 거부한다.
SQLite는 휴대 가능한 테스트/dry-run만 지원하며, 영수증·전달 실행은 PostgreSQL
autocommit이 필수다. 제품 row lock은 잡지 않는다.

## 관측과 전달을 분리한 영수증

`apps/core/services/state_detector.py`가 `OpsAuditLog`의
`monitor.state_integrity.transition`과 `monitor.state_integrity.check`를 소유한다.
tenant/rule별 session advisory lock이 검사와 영수증 commit 및 전달을 직렬화한다.
기존 감사 영수증을 수정하거나 삭제하지 않는다.

| 상황 | 처리 |
| --- | --- |
| 첫 모순 | 새 event ID의 `opened` |
| 동일 모순 + 전달 확인 | `suppressed`, 추가 전달 없음 |
| 모순 집합 변경 | `changed` |
| 전체 재검사 완료, 이전 모순 대상 모두 검사/명시적 제외됨 | `recovered` 한 번 |
| 이전 모순 대상 row가 사라짐 | `previous_subject_missing`, 복구 금지 |
| 복구 후 재발 | 새 event ID의 `opened` |
| HTTP 비성공 | `failed`, 같은 상태 재검사는 같은 event ID로 전달 재시도 |
| 수신처 없음 | `receiver_missing`, 검사 결과와 별개로 전달 실패 |
| timeout 또는 전달 결과 저장 실패 | `unknown`, 자동 재발송/복구 전달 금지 |

전송 전에 `pending` 영수증을 commit하고, 응답 뒤 결과 영수증을 추가한다.
외부 수신과 DB commit은 원자적이지 않으므로 exactly-once 전달을 주장하지 않는다.
프로세스 중단으로 pending이 남아도 unknown과 동일하게 대조가 필요하다. 운영자는
event ID와 수신 채널의 실제 기록을 확인하여 해당 tenant/rule/event의 전달 여부를
확정한 뒤, 동일 내용의 `delivered` 또는 `failed` 정정 영수증을 append-only로
남기는 별도의 명시적 운영 조치를 수행해야 한다. 확인 불가 시 그대로 보류하며
기존 영수증 삭제, 임의 성공 처리, 제품 데이터 변경으로 해제하지 않는다.

`recovered`는 검사 규칙에 따른 모순 해소 또는 명시적 비대상 전환이지 detector가
원본을 고쳤다는 뜻이 아니다. 감사 로그와 webhook에는 tenant 숫자 ID, 규칙명,
상태·개수, SHA-256 subject/fingerprint, event ID만 사용한다. 학생 이름, 연락처,
원점수, 제출 payload, provider 응답 본문은 포함하지 않는다.

JSON의 `inspection_status`(complete/deferred/failed),
`state`(healthy/contradiction/unknown), `delivery_status`는 독립적이다.
검사 오류와 전달 failed/unknown은 명령 exit 1이다. 정상 전달된 모순이나 deferred는
exit 0일 수 있으므로 exit code만으로 원본 정상/검사 완료를 판단하면 안 된다.
수신처 설정만으로 테스트 메시지를 보내지 않는다.

## 검증

`apps/core/tests/test_state_detector.py`는 원본 불일치, 정상 예외, tenant 경계,
읽기 전용 SQL, 불완전 검사/복구 금지, 중복 억제, 전달 실패 보존, 실제 loopback
503→200, PostgreSQL 읽기 전용 snapshot과 별도 connection lock 경합을 검사한다.

```powershell
python -m pytest apps/core/tests/test_state_detector.py -q --tb=short
```

`apps.api.config.settings.test_pg`와 격리 PostgreSQL/pgvector fixture를 사용하는
공식 quality gate가 migration을 포함한 최종 검증이다. 로컬 모델 스키마 기반
PostgreSQL 테스트만 실행한 경우 이를 전체 migration 검증으로 표시하지 않는다.
