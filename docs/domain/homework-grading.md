# 과제 만점·합격 정책·성적 저장 — SSOT

## 목적과 사용자 흐름

원장·선생님·채점 권한이 있는 직원이 차시마다 여러 과제를 만들고, 과제별
문제 수에 맞는 만점과 과제별 합격 기준을 설정한 뒤 학생 점수를 입력하는
현재 계약이다.

1. **강의 → 차시 → 과제**에서 과제를 만들고 `점수형` 또는 `완료형` 채점
   방식을 고른다. 점수형은 과제별 만점을 지정한다.
2. 각 과제의 **과제별 합격 기준**에서 퍼센트 또는 원점수 커트라인을 지정한다.
3. **차시 → 성적**에서 학생별 점수를 입력한다. 성적표 분모와 합격률 계산은
   해당 과제의 만점을 사용한다.
4. 만점이나 정책이 바뀌면 기존 1차 점수의 합격·클리닉 판정을 다시 계산한다.
5. 워크북형 과제는 **자산**에서 문제+해설 통합 파일, 문제 파일, 또는 문제·해설
   두 파일을 등록하고 문항·원본 해설을 검수한 뒤 **결과**에서 학생별 O/X/복습을
   기록한다.

프런트 화면 계약은
[frontend/docs/HOMEWORK-SCORING.md](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/HOMEWORK-SCORING.md)가
소유한다.

## 두 설정의 소유 범위

| 설정 | 범위 | 저장·응답 계약 |
|------|------|----------------|
| 채점 방식 | 과제별 | `Homework.grading_mode`: `SCORE` 또는 `COMPLETION` |
| 과제 만점 | 과제별 | `Homework.meta.default_max_score`, API의 `max_score` |
| 합격 기준 | 과제별 | `Homework.cutline_mode`, `cutline_value`, `round_unit_percent` |
| 기존 기본 정책 | 차시 공통 fallback | `HomeworkPolicy`; 개별 기준이 없는 기존 과제만 사용 |

과제 만점은 과제마다 다를 수 있다. 과제 생성·복사·설정 변경과 성적표 조회는
모두 API의 `max_score`를 사용하며, 값이 없는 기존 과제만 100점을 호환
기본값으로 읽는다. 클라이언트가 점수 저장 요청에 다른 `max_score`를 보내거나
생략해도 서버는 `Homework`의 현재 만점을 정본으로 사용한다.

`HomeworkScore.max_score`는 점수 입력 시점의 판정 스냅샷이다. 과제 만점을
바꾸거나 같은 만점을 명시적으로 다시 저장하면 현재 1차 점수 스냅샷과
`passed`, `clinic_required`, `ClinicLink`를 함께 동기화한다. 학생이 입력한
점수와 재시도 이력은 보존한다. 정책을 저장할 때도 각 과제의 현재 만점을
스냅샷에 먼저 반영한 뒤 판정과 링크를 한 transaction에서 갱신한다.

## 점수형과 완료형

- `SCORE`는 기존 계약이다. `0/30`처럼 점수 또는 수행 개수를 저장하고 과제별
  만점·커트라인으로 합격을 계산한다. 기존 과제와 템플릿은 기본적으로 이 방식이다.
- `COMPLETION`은 완료/미완료만 기록한다. 서버 저장값은 완료 `1`, 미완료 `0`,
  미입력 `null`이며 만점·원점수 기준을 모두 `1`로 정규화한다. 다른 수치나
  클라이언트가 보낸 임의 만점은 `400`으로 거부한다.
- 완료형도 별도 상태 테이블을 만들지 않는다. 기존 `HomeworkScore.passed`,
  `clinic_required`와 `ClinicLink` 동기화를 그대로 사용해 학생 상세, 성적표,
  클리닉 대상이 같은 판정을 읽는다.
- 학생 상세의 과제 목록은 **배정 행을 기준**으로 만들고 기존 점수 행을 합친다.
  따라서 아직 `HomeworkScore`가 없는 과제도 빠지지 않는다. 배정 뒤 제출 행과
  점수가 모두 없거나 명시적으로 `meta.status=NOT_SUBMITTED`가 기록된 결과는
  `미제출`, 제출 행은 있지만 점수가 없는 결과는 `검사 전`으로 구분한다.
  제출 여부는 tenant·enrollment·homework가 모두 일치하는 `Submission`을 한 번에
  조회하며 generic target id만으로 다른 tenant의 제출을 인정하지 않는다.
  삭제·시스템 과제와
  다른 tenant·다른 학생 배정은 포함하지 않는다. 과거 배정 행 없이 점수만 남은
  정상 legacy 결과는 호환을 위해 계속 표시한다.
- 결과 행이 하나라도 생긴 과제는 채점 방식을 바꿀 수 없다. 기존 숫자 점수나
  교사 기록을 새 의미로 재해석하지 않으며, 다른 방식이 필요하면 새 과제를 만든다.
- 템플릿 저장·템플릿 불러오기·다른 차시 복사는 `grading_mode`를 보존한다.

## 정책과 만점의 불변 규칙

새로 만들거나 설정 화면에서 저장한 과제는 각자의 기준을 사용한다. 기존 과제에
개별 기준이 없으면 배포 전과 동일하게 차시 `HomeworkPolicy`를 기본값으로 읽는다.
차시 정책을 바꿔도 이미 개별 기준이 있는 과제는 덮어쓰지 않는다.

과제 수정 API는 요청에 포함된 필드만 바꾼다. 따라서 차시 기본 기준을 쓰는
과제에서 제목이나 제출기한만 수정할 때 `max_score` 또는 커트라인 필드를
보내지 않으면 기본 기준 상속과 기존 판정은 그대로 유지한다. 만점 또는
커트라인 필드가 실제 요청에 포함된 경우에만 해당 동기화·재판정 경로를
실행한다.

- `PERCENT`: 각 과제의 `score / max_score`를 기준으로 판정하므로 만점이
  서로 다른 과제에 권장한다. 퍼센트 반올림 단위는 기존 정책을 따른다.
- `COUNT`: 원점수 커트라인이며 해당 과제의 만점을 초과할 수 없다. 차시 기본
  정책을 사용하는 기존 과제에는 기존의 최저 만점 검증을 유지한다.
- 새 과제의 만점은 그 과제에 적용되는 원점수 커트라인보다 낮을 수 없다.
- 기존 과제의 만점은 그 과제에 적용되는 원점수 커트라인이나 이미 입력된 최고 점수보다
  낮출 수 없다. 검증 실패 시 일부 값이나 판정을 쓰지 않고 `400`으로 거부한다.
- 점수는 0 이상 해당 과제 만점 이하여야 한다. 초과 점수는 `400`으로
  거부하고 기존 점수를 보존한다.
- tenant와 차시 범위는 모든 조회·쓰기에서 필수이며 기본 tenant나
  cross-tenant fallback을 사용하지 않는다.

## 워크북 원본과 문항별 채점

과제는 별도 분리 엔진을 만들지 않고 `Homework.source_exam`이 가리키는 비노출
regular `Exam`을 원본 구조 소유자로 사용한다. 이 시험은 활성 시험 목록이나 학생
성적에 노출하지 않고 세션에도 연결하지 않는다. HWP/HWPX 미주 번호와 원본 그림,
PDF 분리, 교직원 검수·승인 계약은 시험 원본과 동일하다. 특히 미주 전체 이미지를
선생님 해설 정본으로 보존하고, 문제 영역 크롭은 승인 전 문항별로 조정한다.
원본 시험을 멱등 생성할 때는 `Homework` 본행만 `FOR UPDATE OF`로 잠근다.
nullable `source_exam`·`sheet` 조회 조인은 잠금 대상에서 제외하므로 PostgreSQL에서도
빈 워크북의 첫 원본 생성이 `500`으로 실패하지 않는다.

학생별 문항 표시는 1차 `HomeworkScore.meta.question_marks`에 문항 번호별로 저장한다.
기존 점수, 미제출 상태와 임의 확장 메타는 덮어쓰지 않는다.

| 화면 | `is_correct` | `include_in_wrong_note` | 통합 오답노트 |
|------|--------------|-------------------------|---------------|
| O | true | false | 제외 |
| X | false | true | 포함 |
| O·복습 | true | true | 포함 |
| 미입력 | 키 없음 | 키 없음 | 제외 |

X를 나중에 다시 맞힌 뒤에도 남기려면 O·복습으로 바꾼다. 대상 학생 배정,
워크북 문항 번호와 tenant가 모두 일치해야 전체 변경을 저장하며, 다른 학생·과제
또는 현재 원본에 없는 번호는 `400`으로 거부한다.

## API와 호환 경계

| Method | Path | 역할 |
|--------|------|------|
| GET/POST | `/homeworks/` | 차시 과제 목록·생성, `max_score` 반환·입력 |
| GET/PATCH | `/homeworks/{id}/` | 과제별 만점·합격 기준 조회/변경과 1차 점수 동기화 |
| POST | `/homeworks/{id}/source-exam/` | 워크북 분리용 비노출 원본 시험을 멱등 생성 |
| GET/PATCH | `/homeworks/{id}/question-grading/` | 배정 학생×워크북 문항 채점표 조회와 O/X/복습 저장 |
| GET/PATCH | `/homework/policies/` | 개별 기준 없는 기존 과제의 차시 기본 정책 |
| PATCH | `/homework/scores/quick/` | 성적표 셀 점수 저장. 서버 만점을 정본으로 사용 |
| GET | `/results/session-scores/` | 성적표 과제 메타와 각 셀에 과제별 만점 반환 |

`/homeworks/` 응답은 원시 오버라이드와 함께 `effective_cutline_mode`,
`effective_cutline_value`, `effective_round_unit_percent`,
`uses_session_cutline_default`를 반환한다. 기존 과제에 `default_max_score`가 없으면 100으로 읽는다. 이미 과제 메타에는
만점이 있지만 과거 점수 스냅샷이 100인 경우에도 성적표 조회는 즉시 과제
만점을 분모로 반환한다. 다음 점수 저장 또는 만점 재저장에서는 스냅샷과
판정을 정본 값으로 복구한다.

`/homeworks/`, 학생별 성적 요약, 차시 성적표 메타는 모두 `grading_mode`를
반환한다. 학생 상세의 단건 수정도 `/homework/scores/quick/`을 사용하며 먼저
기존 점수 편집 lease를 짧게 획득한다. 다른 화면이 같은 차시를 수정 중이면
`409 SCORE_EDIT_LOCKED`로 실패하고 값을 덮어쓰지 않는다.
학생 상세 응답은 배정된 각 과제의 `grading_mode`, `meta_status`, 정본
`max_score`를 반환한다. 완료형은 완료 여부, 점수형은 `점수/만점`을 표시하며
`NOT_SUBMITTED`와 미검사 `null`은 모두 숫자 0으로 바꾸지 않는다.

현재 운영 설정 화면은 조회 응답의 `updated_at`을
`X-Expected-Updated-At` 헤더로 보낸다. 서버는 과제 행을 잠근 뒤 같은
버전일 때만 저장하며, 다른 화면이 먼저 저장했으면 기존 입력을 덮어쓰지
않고 `409`, `code=stale_resource`, 현재 `updated_at`을 반환한다. 헤더가
없는 기존 클라이언트는 호환을 위해 기존 동작을 유지한다.

## 구현 위치와 검증

- 만점 정본·직렬화: `apps/domains/homework_results/models/homework.py`,
  `serializers/homework.py`
- 만점 변경 검증·동기화: `services/max_score_sync.py`,
  `views/homework_view.py`
- 점수 저장: `views/homework_score_viewset.py`
- 개별/기본 정책 계산: `apps/domains/homework/utils/homework_policy.py`,
  `apps/domains/homework/serializers/core.py`,
  `services/policy_recalc.py`
- 성적표 조회: `apps/domains/results/views/session_scores_view.py`
- 학생 상세 과제 합성: `apps/domains/results/views/admin_student_grades_view.py`,
  `apps/support/results/admin_student_grades_dependencies.py`
- 워크북 원본·채점표: `views/homework_view.py`,
  `tests/test_workbook_source_and_grading.py`
- Ymath 시험·워크북 통합 실자료 UAT:
  `../operations/runbooks/ymath-real-source-qa.md`,
  `scripts/ymath_realuse_scenario.py`

집중 회귀 검증:

```powershell
.venv\Scripts\python.exe -m pytest `
  apps/domains/homework/tests/test_homework_policy_api.py `
  apps/domains/homework_results/tests/test_homework_quick_patch_scope.py `
  apps/domains/results/tests/test_session_scores_roster_scope.py `
  -q --reuse-db
```
