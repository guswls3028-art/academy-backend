Results Domain SSOT
===================

이 문서는 `apps/domains/results/`의 현재 책임 경계를 설명한다.
코드와 문서가 충돌하면 변경자는 먼저 실제 코드 경로를 재측정하고,
문서를 갱신한 뒤 코드를 수정한다.

Scope
-----

`results` 도메인은 시험(Exam) 결과만 소유한다.

- `Enrollment`가 결과의 주체다. `Result`는 `student_id`가 아니라 `enrollment_id`를 키로 사용한다.
- 숙제 결과는 `apps/domains/homework_results/`가 소유한다.
- 진척/클리닉 화면처럼 시험과 숙제를 함께 보여줘야 하는 경우는 각 도메인의 결과를 읽어 조합한다.
- 시험지/문항/정답 정의는 `apps/domains/exams/`가 소유한다.
- 답안 원본은 `apps/domains/submissions/`가 소유한다.

Canonical Records
-----------------

운영 화면과 통계의 기준은 아래 네 모델이다.

- `ExamAttempt`: 시험 시도, 대표 시도, 재응시 상태
- `ResultFact`: append-only 원시 이벤트
- `ResultItem`: 문항 단위 채점 스냅샷
- `Result`: 학생/학부모/관리자 화면의 대표 결과 스냅샷

`ExamResult`는 SSOT가 아니다. 이 모델은 과거 `Submission` OneToOne 채점 계약,
임시 점수 상태 확인, 오래된 API 호환을 위해 유지하는 legacy compatibility
snapshot이다. 신규 기능은 `Result` 계열 모델을 기준으로 작성한다.

Scoring Flow
------------

기본 OMR 채점 흐름은 다음 순서를 따른다.

1. `apps/domains/submissions/services/grading_dispatcher.py`
2. `apps/domains/results/services/grading_service.py::grade_submission`
3. `apps/domains/results/services/exam_grading_service.py::auto_grade_objective`
4. `apps/domains/results/services/sync_result_from_submission.py::sync_result_from_exam_submission`
5. progress dispatch

`ExamGradingService`는 legacy `ExamResult` 객관식 스냅샷만 만든다.
학생/관리자 화면에 노출되는 대표 결과는 `sync_result_from_exam_submission`
단계에서 `Result` / `ResultItem`으로 동기화된다.

OMR Score Shape
---------------

OMR 배점 구조의 SSOT는 `apps/support/omr/score_shape.py`다.

- 객관식과 실제 서술형 배점은 `ExamQuestion.score`와 sheet/template 구조로 계산한다.
- 0점 서술형은 장식용 서술형으로 취급한다.
- 장식용 서술형은 OMR 레이아웃에는 남을 수 있지만 `Result.max_score`와 채점 분모에는 들어가지 않는다.
- 20문항 객관식 + 5문항 장식용 서술형 시험지는 objective max 100, subjective max 0으로 계산되어야 한다.

Manual Scoring
--------------

현행 수동 성적 입력은 view/service 단위로 나뉜 관리자 API가 처리한다.

- `admin_exam_total_score_view.py`
- `admin_exam_objective_score_view.py`
- `admin_exam_subjective_score_view.py`
- `admin_exam_item_score_view.py`

죽은 legacy serializer/service override 경로는 사용하지 않는다. 수동 입력은 반드시
`Result`, `ResultItem`, `ExamAttempt.meta`를 일관되게 갱신해야 하며,
objective + subjective 합산과 문항별 만점 검증을 깨면 안 된다.

Aggregation
-----------

집계/해석은 `apps/domains/results/aggregations/` 또는 명시된 BFF view에서만 수행한다.
모델, serializer, 단순 CRUD view에 새로운 집계 로직을 넣지 않는다.

운영 성적 분석의 canonical BFF는
`apps/support/results/enterprise_analytics.py`다.
이 서비스는 `Result`, `ResultFact`, `ResultItem`, `Submission`을 함께 읽어
성적 분포, 기간별 추이, 수동 성적 입력, 자동채점 사용량을 tenant scope 안에서 집계한다.
`[E2E-*]`, `LOCAL-DEMO`, `DEMO-*` 구조화 prefix로 식별되는 합성 시험은 기본 분석에서 제외한다.
`주간 테스트`, `Level Test`처럼 실제 운영에서 쓰는 일반 시험명은 분석에 포함한다.
노출 엔드포인트는 교사용 `GET /results/admin/analytics/`,
학생/학부모용 `GET /student/grades/analytics/`이며, 학생/학부모는 선택된 학생 1명만 조회한다.
학생/학부모 분석의 `date_range.days`는 시험 수·득점률·합격률·미응시·오답·과제
지표 전체에 동일하게 적용되며, 기간을 판정할 기록 시각이 없는 행은 기간 분석에서 제외한다.

학생별 누적 시험 추이는 관리자·선생 공용 BFF
`GET /results/admin/student-grades/?student_id=<id>`와 학생·학부모 공용 BFF
`GET /student/grades/`의 동일한 `exam_trend`, `exam_summary` 계약을 사용한다.
학생·학부모 응답은 `get_request_student`가 확정한 본인/선택 자녀의 활성 수강만
대상으로 하며, 잘못된 자녀 헤더는 다른 자녀로 fallback하지 않는다. 한 점은 동일
시험의 재응시가 아니라 서로 다른 정규
시험의 대표 `Result` 한 건이다. 유효한 점수가 입력된 시험만 정규 차시 날짜·순서 기준으로
`1회차..N회차`가 자동 부여되며, 만점이 다른 시험은 `score_pct`로 정규화한다.
`NOT_SUBMITTED`는 목록에는 남지만 0점으로 바꾸지 않고 추이·평균 분모에서 제외한다.
보관된 정규 시험 결과는 이력에 유지하고 `archived=true`로 구분한다.
여러 강의에 연결된 시험은 해당 `Result.enrollment`의 강의 차시만 사용하고 시스템 강의는
제외한다. 음수·비유한 점수나 0 이하 만점은 추이에서 제외하며 가산점에 따른 100% 초과는 유지한다.

주의할 예외:

- `session_scores_view.py`는 실사용 BFF라서 시험 결과와 숙제 제출 상태를 함께 읽을 수 있다.
  이 경우에도 숙제 결과를 results 도메인이 소유한다는 뜻은 아니다.

Change Rules
------------

- 신규 채점 규칙은 먼저 OMR score shape와 Result 동기화 경로에 반영한다.
- `ExamResult`에 새 기능을 추가하지 않는다. 호환 때문에 유지할 뿐이다.
- 레거시 import나 죽은 serializer/view/service를 되살리지 않는다.
- 배점, tenant scope, submission scope, representative attempt, manual score 합산을 바꾸는 변경은 focused test와 운영 검증 대상이다.
- "이미 완성" 같은 선언보다 재현 가능한 검증 결과를 우선한다.
