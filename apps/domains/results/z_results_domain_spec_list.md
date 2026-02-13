# PATH: docs/contracts/results_domain_spec_list.md
# 🧾 RESULTS 도메인 스펙 나열 (최소 설명)

아래는 “results 도메인”이 제공하는 스펙(규칙/endpoint/SSOT)을 최소 설명으로 나열한다.

---

## 1) 단일 진실(SSOT)

- 결과 스냅샷: Result (+ ResultItem)
- 변경 로그: ResultFact (append-only)
- 재시험/대표: ExamAttempt (대표 attempt 1개 invariant)
- clinic_required: progress.ClinicLink(is_auto=True) + resolved_at is null
- 시험-세션 매핑 SSOT:
  - get_exams_for_session(session)
  - get_sessions_for_exam(exam_id)
  - get_primary_session_for_exam(exam_id)
  - get_session_ids_for_exam(exam_id)

- 통계/집계 중복 방어 SSOT:
  - latest_results_per_enrollment(target_type, target_id)

---

## 2) Admin/Teacher API

- 시험 결과 테이블:
  - GET /api/v1/results/admin/exams/{exam_id}/results/

- 시험 요약:
  - GET /api/v1/results/admin/exams/{exam_id}/summary/

- 시험 결과 상세(단일 학생):
  - GET /api/v1/results/admin/exams/{exam_id}/enrollments/{enrollment_id}/

- Attempt 목록(단일 학생):
  - GET /api/v1/results/admin/exams/{exam_id}/enrollments/{enrollment_id}/attempts/

- 대표 attempt 교체:
  - POST /api/v1/results/admin/exams/{exam_id}/representative-attempt/

- 문항 수동 채점(점수 수정):
  - PATCH /api/v1/results/admin/exams/{exam_id}/enrollments/{enrollment_id}/items/{question_id}/

- Fact 디버그:
  - GET /api/v1/results/admin/facts/?exam_id=&enrollment_id=&limit=

- 세션→시험 목록:
  - GET /api/v1/results/admin/sessions/{session_id}/exams/

- 세션 기준 시험 요약:
  - GET /api/v1/results/admin/sessions/{session_id}/exams/summary/

- 세션 점수 탭(시험+과제):
  - GET /api/v1/results/admin/sessions/{session_id}/scores/

- 세션 성적 요약:
  - GET /api/v1/results/admin/sessions/{session_id}/score-summary/

- 클리닉 대상자:
  - GET /api/v1/results/admin/clinic-targets/

- 문항 통계:
  - GET /api/v1/results/admin/exams/{exam_id}/questions/
  - GET /api/v1/results/admin/exams/{exam_id}/questions/top-wrong/?n=
  - GET /api/v1/results/admin/exams/{exam_id}/questions/{question_id}/wrong-distribution/

---

## 3) Student API

- 대표 결과:
  - GET /api/v1/results/me/exams/{exam_id}/

- attempt 히스토리:
  - GET /api/v1/results/me/exams/{exam_id}/attempts/

- 오답노트:
  - GET /api/v1/results/wrong-notes/?enrollment_id=...

- 오답노트 PDF job 생성/상태:
  - POST /api/v1/results/wrong-notes/pdf/
  - GET  /api/v1/results/wrong-notes/pdf/{job_id}/

---

## 4) Worker API (WrongNote PDF)

- 제거됨. 오답노트 PDF 생성은 AI CPU 워커로 통합됨.
