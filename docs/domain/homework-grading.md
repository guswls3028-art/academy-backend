# 과제 만점·합격 정책·성적 저장 — SSOT

## 목적과 사용자 흐름

원장·선생님·채점 권한이 있는 직원이 차시마다 여러 과제를 만들고, 과제별
문제 수에 맞는 만점과 과제별 합격 기준을 설정한 뒤 학생 점수를 입력하는
현재 계약이다.

1. **강의 → 차시 → 과제**에서 과제를 만들고 각 과제의 만점을 지정한다.
2. 각 과제의 **과제별 합격 기준**에서 퍼센트 또는 원점수 커트라인을 지정한다.
3. **차시 → 성적**에서 학생별 점수를 입력한다. 성적표 분모와 합격률 계산은
   해당 과제의 만점을 사용한다.
4. 만점이나 정책이 바뀌면 기존 1차 점수의 합격·클리닉 판정을 다시 계산한다.

프런트 화면 계약은
[frontend/docs/HOMEWORK-SCORING.md](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/HOMEWORK-SCORING.md)가
소유한다.

## 두 설정의 소유 범위

| 설정 | 범위 | 저장·응답 계약 |
|------|------|----------------|
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

## 정책과 만점의 불변 규칙

새로 만들거나 설정 화면에서 저장한 과제는 각자의 기준을 사용한다. 기존 과제에
개별 기준이 없으면 배포 전과 동일하게 차시 `HomeworkPolicy`를 기본값으로 읽는다.
차시 정책을 바꿔도 이미 개별 기준이 있는 과제는 덮어쓰지 않는다.

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

## API와 호환 경계

| Method | Path | 역할 |
|--------|------|------|
| GET/POST | `/homeworks/` | 차시 과제 목록·생성, `max_score` 반환·입력 |
| GET/PATCH | `/homeworks/{id}/` | 과제별 만점·합격 기준 조회/변경과 1차 점수 동기화 |
| GET/PATCH | `/homework/policies/` | 개별 기준 없는 기존 과제의 차시 기본 정책 |
| PATCH | `/homework/scores/quick/` | 성적표 셀 점수 저장. 서버 만점을 정본으로 사용 |
| GET | `/results/session-scores/` | 성적표 과제 메타와 각 셀에 과제별 만점 반환 |

`/homeworks/` 응답은 원시 오버라이드와 함께 `effective_cutline_mode`,
`effective_cutline_value`, `effective_round_unit_percent`,
`uses_session_cutline_default`를 반환한다. 기존 과제에 `default_max_score`가 없으면 100으로 읽는다. 이미 과제 메타에는
만점이 있지만 과거 점수 스냅샷이 100인 경우에도 성적표 조회는 즉시 과제
만점을 분모로 반환한다. 다음 점수 저장 또는 만점 재저장에서는 스냅샷과
판정을 정본 값으로 복구한다.

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

집중 회귀 검증:

```powershell
.venv\Scripts\python.exe -m pytest `
  apps/domains/homework/tests/test_homework_policy_api.py `
  apps/domains/homework_results/tests/test_homework_quick_patch_scope.py `
  apps/domains/results/tests/test_session_scores_roster_scope.py `
  -q --reuse-db
```
