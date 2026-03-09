# 프론트-백엔드 스펙 불일치 감사 (Backend Refactor 가이드)

**작성일:** 2026-03-09  
**목적:** 프론트와 백엔드 API 경로·계약 불일치를 정리하고, 백엔드 전면 리팩터 시 우선순위와 방향을 제시한다.

---

## 1. 요약

| 구분 | 내용 |
|------|------|
| **즉시 수정(프론트)** | 제출(submissions) 단건 조회·시험별 목록 경로가 백엔드와 다름 → 404 가능 |
| **백엔드 리팩터 권장** | URL 이중 prefix 제거(`/submissions/submissions/`), lecture=null 500 방지, 응답 필드 SSOT 정리 |

---

## 2. 도메인별 불일치

### 2.1 Submissions (제출)

**백엔드 실제 경로 (v1 prefix 제외)**

- 앱 마운트: `path("submissions/", include(submissions.urls))`
- 앱 내부: `path("submissions/exams/<exam_id>/", ...)`, `router.register("submissions", SubmissionViewSet)`
- **실제 URL:**  
  - 목록(시험별): `/api/v1/submissions/submissions/exams/<exam_id>/`  
  - 단건: `/api/v1/submissions/submissions/<pk>/`  
  - 재시도: `/api/v1/submissions/submissions/<pk>/retry/`  
  - 수동 수정: `/api/v1/submissions/submissions/<pk>/manual-edit/`  
  - OMR 배치: `/api/v1/submissions/submissions/exams/<exam_id>/omr/batch/`  
  - OMR 단건: `/api/v1/submissions/submissions/exams/<exam_id>/omr/`  

**프론트 호출**

| 용도 | 현재 호출 | 백엔드 실제 | 불일치 |
|------|-----------|-------------|--------|
| 시험별 제출 목록 (admin) | `GET /submissions/submissions/exams/:examId/` | 동일 | 없음 |
| 시험별 제출 목록 (materials) | `GET /submissions/exams/:examId/` | `.../submissions/exams/...` | **경로 누락 → 404** |
| 제출 단건 (폴링) | `GET /submissions/:id/` | `.../submissions/:id/` | **경로 누락 → 404** |
| 재시도 / 수동 수정 | `.../submissions/submissions/:id/...` | 동일 | 없음 |

**리팩터 제안 (백엔드)**

- submissions 앱에서 `submissions/` 중복 제거:  
  - `path("exams/<int:exam_id>/", ExamSubmissionsListView)`  
  - `path("exams/<int:exam_id>/omr/", ...)`, `path("exams/<int:exam_id>/omr/batch/", ...)`  
  - ViewSet은 `router.register("", SubmissionViewSet)` 또는 `"submissions"` 한 단계만 사용  
- 결과: `/api/v1/submissions/exams/<id>/`, `/api/v1/submissions/<pk>/` 등 단일 prefix로 통일.

---

### 2.2 Lectures / Sessions

**백엔드**

- `path("lectures/", include(lectures.urls))`  
- router: `lectures`, `sessions` → `/api/v1/lectures/lectures/`, `/api/v1/lectures/sessions/`

**프론트**

- `GET /lectures/lectures/:lectureId/`, `GET /lectures/sessions/?lecture=:lectureId` 사용 → **일치**

**문제**

- 커뮤니티 등에서 `lecture=null` 로 호출 시 500 발생 가능.  
- **리팩터:** 쿼리 `lecture`가 null/비어 있으면 400 응답 또는 필터 생략으로 처리해 500 방지.

---

### 2.3 Results (성적/시험 결과)

**백엔드**

- `path("results/", include(results.urls))`  
- 예: `admin/sessions/<id>/score-summary/`, `admin/sessions/<id>/scores/`, `me/exams/<id>/`, `wrong-notes/pdf/<job_id>/`

**프론트**

- `/results/admin/sessions/:sessionId/score-summary/`, `/results/me/exams/:examId/`, `/results/wrong-notes/pdf/:jobId/` 등 사용 → **일치**

---

### 2.4 Community

**백엔드**

- `path("community/", include(community.api.urls))`  
- `scope-nodes/`, `block-types/`, `posts/`, `admin/posts/` 등

**프론트**

- `/community/scope-nodes/`, `/community/block-types/`, `/community/posts/`, `/community/admin/posts/` 등 사용 → **일치**  
- block_types 응답이 `results` 페이지네이션인 경우와 배열인 경우 모두 처리 중.

---

### 2.5 Student App

**백엔드**

- `path("student/", include(student_app.urls))`  
- `dashboard/`, `sessions/me/`, `sessions/<pk>/`, `exams/`, `results/me/exams/<id>/`, `grades/`, `video/...` 등

**프론트 (학생앱)**

- `/student/dashboard/`, `/student/sessions/me/`, `/student/exams/:id/`, `/student/results/me/exams/:id/`, `/student/grades/`, `/media/playback/...` 등 사용 → **일치** (이미 playback 경로 수정 반영)

---

### 2.6 Video / Media

**백엔드**

- `path("media/", include(support.video.urls))`  
- `videos/`, `playback/heartbeat/`, `playback/refresh/`, `playback/end/`, `playback/events/` 등

**프론트**

- `/media/videos/:id/`, `/media/playback/...` 사용 → **일치**

---

### 2.7 Homework / Homeworks

**백엔드**

- `path("homework/", ...)` → policies, scores, assignments  
- `path("homeworks/", ...)` → HomeworkViewSet (CRUD)

**프론트**

- `/homework/policies/`, `/homework/scores/`, `/homeworks/:id/` 등 사용 → **일치**

---

## 3. 즉시 수정 권장 (프론트) — 적용 완료

1. **제출 단건 조회 (폴링)**  
   - 파일: `frontend/src/features/scores/api/pollingSubmission.ts`  
   - 변경: `GET /submissions/${submissionId}/` → `GET /submissions/submissions/${submissionId}/` ✅

2. **시험별 제출 목록 (materials)**  
   - 파일: `frontend/src/features/materials/sheets/components/submissions/submissions.api.ts`  
   - 변경: `GET /submissions/exams/${examId}/` → `GET /submissions/submissions/exams/${examId}/` ✅  
   - 동일 파일: `uploadOmrBatchApi` → `POST /submissions/submissions/exams/${examId}/omr/batch/` ✅

3. **OMR 배치 업로드 (AdminOmrBatchUploadBox)**  
   - `POST /submissions/exams/...` → `POST /submissions/submissions/exams/.../omr/batch/` ✅

4. **OMR 단건 업로드 (exams AdminOmrUploadSection)**  
   - `POST /submissions/exams/.../omr/` → `POST /submissions/submissions/exams/.../omr/` ✅

5. **OMR 업로드 후보 경로 (submissions AdminOmrUploadSection)**  
   - 후보 배열 맨 앞에 `/submissions/submissions/exams/:id/omr/`, `.../omr/batch/` 추가 ✅

이렇게 하면 현재 백엔드 URL 구조와 맞아서 404가 사라진다. 이후 백엔드에서 prefix를 정리하면 프론트는 한 번만 경로를 다시 맞추면 된다.

---

## 4. 백엔드 전면 리팩터 시 우선순위

1. **Submissions URL 정리**  
   - 이중 `submissions/` 제거, `exams/<id>/`·`<pk>/` 단일 prefix로 통일.  
   - 프론트는 위 2곳 수정 후, 리팩터 완료 시 새 경로로 재변경.

2. **Lectures/Sessions**  
   - `lecture=null` (또는 빈 값) 쿼리 시 500 대신 400 또는 빈 목록 반환으로 처리.

3. **응답 계약 SSOT**  
   - 페이지네이션: `results` vs 배열 중 하나로 통일하고, 프론트는 한 형태만 처리하거나 호환 레이어 유지.  
   - block_types, community posts 등 이미 프론트에서 둘 다 처리 중이면, 백엔드에서 한 형태로 고정하는 것이 유지보수에 유리.

4. **학생/관리자 경로 분리**  
   - `/student/*` vs `/results/me/*` 중복 노출 정리 (선택).  
   - 학생 전용은 `/student/*`만 사용하도록 백엔드·프론트 정책 통일.

---

## 5. 참고

- 프론트 baseURL: `/api/v1` (axios).
- 백엔드 v1 진입점: `apps/api/v1/urls.py`.
- 제출 도메인: `apps/domains/submissions/urls.py`, ViewSet은 `SubmissionViewSet`.

이 문서는 “백엔드 전면 리팩터” 시 스펙 정합성 체크리스트로 사용할 수 있다.
