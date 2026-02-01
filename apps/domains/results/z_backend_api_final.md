# PATH: docs/contracts/backend_api_final.md
# 📘 BACKEND API FINAL (EXAMS · SUBMISSIONS · RESULTS) — 봉인본

기준
- Django REST Framework
- 상태 머신: Submission.Status
- 채점 단일 진실: grade_submission()
- 결과 단일 진실: results 도메인
- Worker: HTTP polling + callback

---

## 0️⃣ 공통 규칙 (중요)

### Submission 상태 흐름 (고정)
SUBMITTED
 → DISPATCHED
 → EXTRACTING
 → ANSWERS_READY
 → GRADING
 → DONE
 → FAILED (retry 가능)

### 절대 규칙
- ❌ 프론트는 submissions.answers 직접 해석 금지
- ✅ 결과는 results API만 조회
- ✅ 채점은 grade_submission() 단일 진입점
- ✅ worker는 backend의 internal endpoint 계약만 사용

---

## 1️⃣ 시험 (Exams)

### 1-1. 시험 목록 (학생)
GET /student/exams/

### 1-2. 시험 상세
GET /student/exams/{exam_id}/

### 1-3. 시험지(OMR PDF) 생성
POST /exams/{exam_id}/omr/generate/

결과:
- PDF URL
- sheet_id 포함

---

## 2️⃣ 제출 (Submissions)

### 2-1. OMR 시험 제출 (핵심 시작점)
POST /submissions/exams/{exam_id}/omr/

Body
{
  "enrollment_id": 123,
  "sheet_id": 45,
  "file_key": "uploads/omr/scan1.jpg"
}

Backend
- Submission 생성
- status = SUBMITTED → DISPATCHED
- AI job dispatch

Response
{
  "submission_id": 1001,
  "status": "dispatched"
}

### 2-2. 일반 제출 생성 (범용)
POST /submissions/
(source = online / homework 등)

### 2-3. 제출 목록 조회
GET /submissions/

### 2-4. 제출 상세 조회 (polling 용)
GET /submissions/{submission_id}/

중요 필드
- status
- meta.ai_result
- meta.omr / homework 결과

### 2-5. 실패 제출 재시도
POST /submissions/{submission_id}/retry/

조건
- status == FAILED

### 2-6. OMR 수동 수정 (교사용)
POST /submissions/{submission_id}/manual-edit/

Body
{
  "identifier": "manual",
  "answers": [
    { "exam_question_id": 10, "answer": "B" },
    { "exam_question_id": 11, "answer": "D" }
  ],
  "note": "teacher fix"
}

효과
- SubmissionAnswer overwrite
- status → ANSWERS_READY
- 즉시 재채점

---

## 3️⃣ AI 결과 콜백 (Worker → Backend)

### 3-1. AI 결과 수신 (내부)
POST /internal/ai/result/

Body (예시)
{
  "submission_id": 1001,
  "status": "DONE",
  "result": { ... },
  "error": null
}

Router
- apply_ai_result_for_submission()

분기
- OMR → answers 저장 → ANSWERS_READY → 채점 대상
- Homework video/image → meta 저장 → DONE

---

## 4️⃣ 채점 (Results – 내부 SSOT)

### 4-1. 채점 진입점 (직접 호출 ❌ / 내부 사용)
grade_submission(submission_id)

보장
- Idempotent
- Attempt / Result 1개로 수렴

### 4-2. 자동 enqueue (Celery)
enqueue_grading_if_ready(submission)

조건
- status == ANSWERS_READY

---

## 5️⃣ 결과 조회 (Results)

### 5-1. 학생 시험 결과 목록
GET /student/results/exams/

### 5-2. 학생 시험 결과 상세
GET /student/results/exams/{exam_id}/

포함
- total_score
- objective / subjective
- breakdown
- pass/fail

### 5-3. 시험 시도(Attempt) 조회
GET /results/exam-attempts/

### 5-4. 시험 결과 요약 (관리자)
GET /results/admin/exam-summary/

### 5-5. 문항 통계
GET /results/question-stats/

---

## 6️⃣ 오답노트 PDF

### 6-1. 오답노트 PDF 생성 (비동기)
POST /results/wrong-notes/pdf/

### 6-2. 오답노트 PDF 상태 조회 (polling)
GET /results/wrong-notes/pdf/{job_id}/

Response
{
  "job_id": 12,
  "status": "DONE",
  "file_url": "https://..."
}

---

## 7️⃣ 숙제 (Homework – AI 판별)

### 7-1. 영상 숙제 제출
POST /submissions/
source: HOMEWORK_VIDEO

### 7-2. 영상 숙제 AI 결과
meta:
{
  "homework_video_result": {
    "has_content": true,
    "filled_ratio": 0.42,
    "too_short": false
  }
}

※ 채점 ❌, DONE 처리

---

## 8️⃣ 상태 조회 요약 (프론트 Polling)

프론트는 이 3가지만 보면 됨
- GET /submissions/{id}/  → status 확인
- status == DONE
- GET /student/results/exams/{exam_id}/

---

## 🔒 최종 봉인 선언
- 이 문서는 현재 코드 기준 최종 API 계약(요약본)
- Submission / Results / Grading 단일 진실 일치
- 프론트·워커·백엔드 분리 완성

