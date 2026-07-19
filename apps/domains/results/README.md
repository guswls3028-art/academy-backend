Results Domain SSOT
===================

이 문서는 `apps/domains/results/`의 현재 책임 경계를 설명한다.
코드와 문서가 충돌하면 변경자는 먼저 실제 코드 경로를 재측정하고,
문서를 갱신한 뒤 코드를 수정한다.

Scope
-----

`results` 도메인은 학원 시험(Exam) 결과와, 증빙 확인이 필요한 학생 제출 외부 성적을 소유한다.

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

Student-submitted School / Mock Scores
--------------------------------------

`StudentReportedScore`는 학생이 성적표 원본과 함께 자발적으로 제출한 학교 내신·모의고사
성적의 검수 상태를 소유한다. 학원 시험 `Result`와는 별도 기록이며 서로 대체하지 않는다.

- 학교 내신은 학년도·1/2학기 아래 1차(중간)·2차(기말) 지필평가와 수행평가,
  학교별 기타 평가를 함께 수용한다. 수행/기타 평가는 성적표 기재 시험명과 시험일을
  필수로 저장해 학교별 3차 지필평가 같은 실제 명칭을 잃지 않는다.
- 한 `StudentReportedScore`는 한 과목의 성적이지만, 한 `InventoryFile` 원본에 최대
  20과목을 연결해 학생은 성적표 한 장을 한 번만 올린다. 생성·묶음 검수는 원자적으로
  처리하고 과목별 상승선은 `subject_summaries`에서 계속 분리한다.
- 학교 성적은 기존 9등급과 2025학년도 고1부터 적용되는 5등급을 구분하며,
  성취도(A~E), 과목 평균, 표준편차, 수강자 수도 원본에 표시된 경우 보존한다.
- 모의고사는 교육청 전국연합학력평가와 평가원 수능 모의평가를 구분한다. 시행 월은
  1~12월에서 성적표에 적힌 값을 저장한다. 평가원 하반기 모의평가 월처럼 공식 일정이
  학년도별로 바뀔 수 있으므로 6월·9월 같은 고정 allowlist를 계약으로 두지 않는다.
- 학생 입력값은 `pending`으로 생성되며, 교직원이 원본을 확인해 `verified`로 바꾼
  값만 누적 통계에 포함한다. 등급이 입력된 원본은 관리자가 5/9등급 체계를 명시적으로
  재확인해야 승인된다. `rejected`와 사유 필수 `voided`는 통계에서 제외한다.
- 동일 학생·시험 분류·과목의 정정 제출을 승인하면 기존 승인값은 대체 상태로 반려한다.
  수행·기타 평가는 같은 이름이 반복될 수 있으므로 시험일까지 같을 때만 정정본으로 본다.
- `evidence_file`은 학생 인벤토리 원본과 다대일로 연결한다. `pending`/`verified` 동안은
  단건·재귀 삭제와 덮어쓰기를 차단하며 DB 제약도 활성 상태의 null 증빙을 거부한다.
  `rejected`/`voided` 뒤에는 원본만 명시적으로 삭제할 수 있다. R2 삭제가 실패하면 DB
  연결을 유지하고 502로 재시도를 요구하며, adapter 부재는 503으로 중단한다. 검수 이력이
  있는 원본은 이동·재귀 폴더 삭제·덮어쓰기에서 제외해 반드시 단건 삭제를 거친다. 성적 행·
  검수자·사유는 원본과 분리된 감사 기록으로 남는다.
- 관리자 성적 콘솔은 300ms 지연 검색·강의·학년·출처·과목·득점구간·추세·정렬 조건을
  서버에 전달하고 최대 100명 단위 학생 페이지와 20장 단위 검토 큐 페이지를 사용한다.
  동일 조건의 1분 자동 갱신은 관련 tenant 데이터의 건수·최종 수정 시각이 같을 때만
  5분 버전 캐시를 사용하며, 새 시험·승인·학생 변경 시 즉시 새 키로 재계산한다.
- 업로드·검수·성적 콘솔 조합은 cross-domain support인
  `apps/support/results/student_reported_scores.py`와
  `apps/support/results/student_performance_console.py`가 담당한다.
- 운영 왕복 검증은 `scripts/post_deploy_smoke/reported_score_chain.py --cleanup-remote`로
  제출→묶음 승인→차트 반영→통계 제외→증빙 삭제→UUID가 붙은 `[E2E-*]` 감사행 정리까지
  수행한다. 업로드 응답이 불명확해도 exact marker 기반 `--recover-active` 정리가 실행된다.

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
