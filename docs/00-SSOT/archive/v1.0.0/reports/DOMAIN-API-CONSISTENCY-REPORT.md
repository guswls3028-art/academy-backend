# 도메인별 백엔드·프론트엔드 API 정합성 검사 보고서

**생성일:** 2026-03-07  
**범위:** api/v1 기준 도메인별 엔드포인트 ↔ 프론트 API 호출 경로 매칭 및 불일치 조치

---

## 1. 검사 방법

- **백엔드:** `apps/api/v1/urls.py` include 기준으로 각 도메인 `urls.py` 및 ViewSet `@action` 경로 수집.
- **프론트:** `src/features/**/api/*.ts`, `src/student/**/api/*.ts`, `src/dev_app/api/*.ts` 내 `api.get/post/patch/put/delete` 호출 경로 수집 (baseURL `/api/v1` 가정).
- **매칭:** 경로·메서드 일치 여부 확인, 불일치 시 백엔드 SSOT 기준으로 프론트 수정 또는 문서화.

---

## 2. 조치한 불일치 (수정 완료)

| 도메인 | 프론트 (수정 전) | 백엔드 SSOT | 조치 |
|--------|------------------|-------------|------|
| **results** | `GET /sessions/${sessionId}/score-summary/` | `GET /results/admin/sessions/<session_id>/score-summary/` | `sessionScoreSummary.ts` 경로를 `/results/admin/sessions/...` 로 변경 |
| **staffs** | `POST /staffs/payroll-snapshots/export-excel/`, `GET .../export-pdf/` | `POST /staffs/export-excel/`, `GET /staffs/export-pdf/` (StaffViewSet 루트 @action) | `payrollSnapshots.api.ts`, `payrollSnapshotPdf.api.ts` 경로를 `/staffs/export-excel/`, `/staffs/export-pdf/` 로 변경 |
| **submissions** | `GET /submissions/exams/${examId}/` | `GET /submissions/submissions/exams/<exam_id>/` | `adminSubmissionsApi.ts` 경로를 `/submissions/submissions/exams/...` 로 변경 |

---

## 3. 도메인별 정합성 요약

| 도메인 | 상태 | 비고 |
|--------|------|------|
| **core** | 일치 | me, program, profile, tenant-branding, tenants, job_progress 등 프론트 경로와 일치 |
| **auth (token)** | 일치 | POST /token/ → config/urls.py |
| **students** | 일치 | list/create/detail, registration_requests, tags, password_find, bulk_* 등 일치 |
| **lectures** | 일치 | lectures/, sessions/, attendance/, matrix, bulk_create, excel 등 |
| **attendance** | 일치 | lectures/ prefix 하위 attendance 경로 |
| **enrollments** | 일치 | session-enrollments, bulk_create, excel_job_status 등 |
| **progress** | 일치 | (관리자 기능 위주, 프론트 호출 적음) |
| **staffs** | 일치 | 위 조치 후 export-excel/export-pdf 포함 |
| **teachers** | 일치 | CRUD 경로 일치 |
| **exams** | 대부분 일치 | recalculate 엔드포인트는 백엔드 미구현(프론트에서 not implemented 처리) |
| **results** | 일치 | 위 조치 후 score-summary 포함, admin/sessions, admin/exams, wrong-notes 등 |
| **homework** | 일치 | policies, scores, quick, assignments 등 |
| **homeworks** | 일치 | homework_results 도메인, list/detail/quick |
| **submissions** | 일치 | 위 조치 후 exam 목록 경로 포함, admin/omr-upload, retry 등 |
| **clinic** | 일치 | settings, sessions, participants, set_status, idcard 등 |
| **assets** | 일치 | omr/objective/meta, omr/pdf 등 (프론트 materials에서 호출) |
| **storage** | 일치 | inventory, quota, folders, upload, presign, move 등 |
| **community** | 일치 | scope-nodes, block-types, posts, replies, admin/posts 등 |
| **messaging** | 일치 | info, log, send, templates, auto-send, verify-sender 등 |
| **media (video)** | 대부분 일치 | videos, playback, folders, stats, policy-impact 등 (아래 예외 참고) |
| **jobs (ai)** | 일치 | GET /jobs/<job_id>/ 등 |
| **student (학생앱)** | 일치 | student/me, dashboard, sessions, exams, results, video 등 |

---

## 4. 알려진 격차 (백엔드 미구현 또는 상이)

| 구분 | 프론트 호출 | 백엔드 | 권장 조치 |
|------|-------------|--------|-----------|
| **interactions** | `GET/POST /interactions/material-categories/`, `/interactions/materials/` | api/v1에 `/interactions` 경로 없음 | 기존 주석(SSOT) 유지. 백엔드에 material-categories·materials API 추가 시 경로 일치 |
| **media (admin)** | `GET /media/admin/videos/<videoId>/sessions/`, `GET /media/admin/playback-sessions/<sessionId>/events/` | media/ 하위에 admin 경로 미정의 | 영상 재생 감사(playback audit)용. 백엔드에 admin 전용 뷰 추가 시 경로 확정 |
| **exams recalculate** | `POST /exams/<examId>/recalculate/` | 해당 path 없음 | 프론트에서 이미 "not implemented" 처리. 백엔드 구현 시 경로 통일 |

---

## 5. 참고 문서

- **인프라·배포 정합성:** `docs/00-SSOT/v1/reports/INFRA-BACK-FRONT-CONSISTENCY-REPORT.md`
- **Front-Backend 연결 사실:** `docs/00-SSOT/v1/reports/front-backend-fact-report.md`
- **API 루트:** `apps/api/config/urls.py`, `apps/api/v1/urls.py`

---

## 6. 수정된 파일 목록 (이번 검사 기준)

- `academyfront/src/features/sessions/api/sessionScoreSummary.ts` — 경로를 `/results/admin/sessions/...` 로 변경
- `academyfront/src/features/staff/api/payrollSnapshots.api.ts` — export-excel 경로를 `/staffs/export-excel/` 로 변경
- `academyfront/src/features/staff/api/payrollSnapshotPdf.api.ts` — export-pdf 경로를 `/staffs/export-pdf/` 로 변경
- `academyfront/src/features/submissions/api/adminSubmissionsApi.ts` — 제출 목록 경로를 `/submissions/submissions/exams/...` 로 변경, SSOT 주석 추가

정합성 검사 후 문서와 일치 여부를 반영했으며, 위 알려진 격차는 백엔드 확장 시 경로만 맞추면 됨.

---

## 7. 2026-03-09 정합성 보강 (백엔드 구조적 수정)

| 구분 | 내용 | 수정 |
|------|------|------|
| **submissions** | 프론트 `POST /submissions/submissions/admin/omr-upload/` 호출에 대응하는 백엔드 액션 없음 | `SubmissionViewSet`에 `@action(detail=False, url_path="admin/omr-upload")` 추가. form-data: enrollment_id, target_id, file → 제출 생성·R2 업로드·dispatch |
| **assets/omr** | 프론트 `GET /api/v1/assets/omr/objective/meta/?question_count=10\|20\|30` 호출에 대응하는 라우트 없음 | `ObjectiveOMRMetaView` 추가, `build_objective_template_meta()` 구현(OmrObjectiveMetaV1 형식, mm 단위 roi). `assets/omr/urls.py`에 `objective/meta/` 경로 추가 |
| **results score-summary** | 백엔드 응답은 flat(participant_count, avg_score, …), 프론트는 total/offline/online 그룹 기대 | 백엔드 SSOT 유지. `sessionScoreSummary.ts`에서 응답을 total/offline/online 형태로 매핑하는 어댑터 적용 |

**수정된 파일 (2026-03-09)**  
- `backend/apps/domains/submissions/views/submission_view.py` — admin_omr_upload 액션 추가  
- `backend/apps/domains/assets/omr/services/meta_generator.py` — build_objective_template_meta 구현  
- `backend/apps/domains/assets/omr/views/omr_list_views.py` — ObjectiveOMRMetaView 추가  
- `backend/apps/domains/assets/omr/urls.py` — objective/meta/ 경로 추가  
- `frontend/src/features/sessions/api/sessionScoreSummary.ts` — 백엔드 flat 응답 → total/offline/online 매핑
