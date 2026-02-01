# PATH: docs/contracts/frontend_api_spec_results.md
# 📗 FRONTEND API SPEC (RESULTS 중심) — 고정 계약

이 문서는 프론트가 “계약만 보고” 개발할 수 있도록,
results 도메인 기준 endpoint/의미만 간단히 고정한다.

원칙
- 프론트는 상태 + 대표 결과만 신뢰
- 조회 API는 부수효과 없음
- 결과/통계는 results SSOT만 사용
- Clinic 대상자/통과율은 progress 단일진실(ClinicLink/SessionProgress)로 계산된 결과만 조회

---

## A) 학생(Student)

### A-1) 대표 결과(시험)
GET /api/v1/results/me/exams/{exam_id}/
- 반환: Result 스냅샷 + items
- 포함: allow_retake, max_attempts, can_retake
- 포함: clinic_required (ClinicLink(is_auto=True) 기준)

### A-2) 재시험 히스토리(선택)
GET /api/v1/results/me/exams/{exam_id}/attempts/
- 반환: attempt_id, attempt_index, is_retake, is_representative, status, created_at

### A-3) 오답노트 조회
GET /api/v1/results/wrong-notes/?enrollment_id=&exam_id=&lecture_id=&from_session_order=&offset=&limit=
- 반환: count, next, prev, results[]

### A-4) 오답노트 PDF 생성 Job
POST /api/v1/results/wrong-notes/pdf/
Body: { enrollment_id, lecture_id?, exam_id?, from_session_order? }

### A-5) 오답노트 PDF Job 상태
GET /api/v1/results/wrong-notes/pdf/{job_id}/
- 반환: status + file_url(DONE 시)

---

## B) 관리자/교사(Admin/Teacher)

### B-1) 시험 결과 테이블
GET /api/v1/results/admin/exams/{exam_id}/results/

### B-2) 시험 요약(평균/최소/최대/합불/클리닉)
GET /api/v1/results/admin/exams/{exam_id}/summary/

### B-3) 시험 문항 통계
GET /api/v1/results/admin/exams/{exam_id}/questions/
GET /api/v1/results/admin/exams/{exam_id}/questions/top-wrong/?n=
GET /api/v1/results/admin/exams/{exam_id}/questions/{question_id}/wrong-distribution/

### B-4) Attempt 목록(특정 시험+특정 enrollment)
GET /api/v1/results/admin/exams/{exam_id}/enrollments/{enrollment_id}/attempts/

### B-5) 대표 Attempt 교체(스냅샷 재빌드 + progress 트리거)
POST /api/v1/results/admin/exams/{exam_id}/representative-attempt/
Body: { enrollment_id, attempt_id }

### B-6) 문항 점수 수동 수정(append-only Fact + progress 트리거)
PATCH /api/v1/results/admin/exams/{exam_id}/enrollments/{enrollment_id}/items/{question_id}/
Body: { score }

### B-7) 세션 기준 시험 요약(1 Session : N Exams)
GET /api/v1/results/admin/sessions/{session_id}/exams/summary/

### B-8) 세션 → Exams 목록
GET /api/v1/results/admin/sessions/{session_id}/exams/

### B-9) 세션 점수 탭(시험+과제 조합)
GET /api/v1/results/admin/sessions/{session_id}/scores/

### B-10) 세션 성적 요약(대시보드 입력용)
GET /api/v1/results/admin/sessions/{session_id}/score-summary/

### B-11) 클리닉 대상자(관리자 패널)
GET /api/v1/results/admin/clinic-targets/

---

## C) 내부 Worker (WrongNote PDF)

Bearer token 인증 필요

- GET  /api/v1/internal/wrong-note-worker/next/
- GET  /api/v1/internal/wrong-note-worker/{job_id}/data/
- POST /api/v1/internal/wrong-note-worker/{job_id}/prepare-upload/
- POST /api/v1/internal/wrong-note-worker/{job_id}/complete/
- POST /api/v1/internal/wrong-note-worker/{job_id}/fail/
