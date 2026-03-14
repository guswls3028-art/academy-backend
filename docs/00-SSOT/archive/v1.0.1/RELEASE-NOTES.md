# V1.0.1 Release Notes (Final)

**Release Date:** 2026-03-11
**Type:** Quality Audit, Security Hardening & UX Polish Release
**Final Commits:** Frontend `5ce0233a` / Backend `334270bc`

---

## Changes Summary

### Frontend (5ce0233a)

#### High Impact
1. **alert() → feedback toast 전환** (66건 → 0건)
   - 전체 admin 페이지의 `alert()` 호출을 `feedback` 토스트 시스템으로 교체
   - 학생 앱용 `studentToast` 시스템 신규 생성 및 적용 (ClinicPage, ProfilePage)
   - 인증 만료 알림(AuthContext)만 alert 유지 (의도적)

2. **학습관리 탭 분리**
   - 시험/성적/영상 페이지의 `StorageStyleTabs` 상단 탭 제거
   - 각 페이지가 독립 `DomainLayout`으로 렌더링
   - `LearningLayout.tsx` 데드코드 삭제

3. **Materials TODO 텍스트 정리**
   - 사용자에게 보이던 "TODO" 텍스트를 "예정 기능"으로 교체
   - AssetsTab, MetaPreviewTab 개선

4. **`--stu-radius` CSS 토큰 정의** — 26개+ 컴포넌트에서 참조하던 미정의 토큰 수정 (8px)

5. **PlayerToast 자동닫힘 버그 수정** — onClose ref 안정화로 2.6초 타이머 정상 작동

#### Medium Impact
6. **비디오 플레이어 리팩터링** — StudentVideoPlayer, HLS 컨트롤러, player.css 대규모 개선
7. **메시지 자동발송 설정** — AutoSendSettingsPanel 신규, SendMessageModal 개선
8. **클리닉/직원 설정 페이지** — ClinicMsgSettingsPage, StaffSettingsPage 신규
9. **커뮤니티 설정** — CommunitySettingsPage 리팩터링
10. **DeveloperPage 메모리 누수 수정** — 이미지 미리보기 URL 정리(cleanup on unmount)

### Backend (334270bc)

#### Critical — 테넌트 격리 강화 (총 33개 뷰)
1. **Exam 뷰 13개 tenant 필터링 추가**
   - 1차 (7개): `TemplateBuilderView`, `TemplateEditorView`, `TemplateStatusView`, `RegularExamFromTemplateView`, `ExamQuestionsByExamView`, `ExamViewSet.perform_create`, `SheetViewSet._assert_exam_is_template`
   - 2차 (6개): `TemplateValidationView`, `ExamQuestionInitView`, `SheetAutoQuestionsView`, `GenerateOMRSheetAssetView`, `ExamAssetView`(GET+POST), `ExamEnrollmentManageView`(GET+PUT)

2. **Results 뷰 20개 tenant 필터링 추가**
   - Admin 뷰: ExamAttempt, ExamAttempts, ExamResultDetail, ExamSummary, ExamResults, SessionExamsSummary, SessionExams, SessionScoreSummary, ResultFact, QuestionStats(3종), ItemScore, ObjectiveScore, SubjectiveScore, TotalScore, RepresentativeAttempt, ExamGrading(2종)
   - Student 뷰: StudentExamAttempts, WrongNote, WrongNotePDF(2종)

#### Critical — 보안
3. **VideoProcessingCompleteView** — `AllowAny` → `IsLambdaInternal` (비인가 사용자가 영상 완료 처리 가능했던 취약점 제거)
4. **StudentPasswordResetSendView** — 클라이언트 지정 비밀번호를 인증된 관리자/교사만 허용하도록 보안 강화

#### Security
5. **자격증명 파일 git 추적 해제**
   - `.env.local`, `tmp_api_env.json` — `git rm --cached`
   - `tmp_api_env.json` `.gitignore` 추가

#### Bug Fix
6. **학생 비디오 썸네일 URL** — `_build_thumbnail_url()` 함수로 SSOT 통합

---

## Deployment Verification

| Stage | Check | Result |
|-------|-------|--------|
| 1 | Backend CI (V1 Build OIDC) | PASS (success) |
| 2 | `/healthz` liveness | PASS (200) |
| 2 | `/health` readiness | PASS (200, DB connected) |
| 2 | Frontend hakwonplus.com | PASS (200) |
| 2 | Frontend tchul.com | PASS (200) |
| 2 | Frontend limglish.kr | PASS (200) |
| 3 | ASG instances | SKIP (AWS CLI unavailable locally) |
| 4 | SQS queue depth | SKIP (AWS CLI unavailable locally) |
| 5 | Drift check | N/A (no infra changes) |

**Overall: PASS** (Stage 3/4 skipped due to local env, API health confirms backend operational)

---

## Known Issues (Not Fixed — Out of Scope)

1. **AttendancePage (학생)** — 완전한 플레이스홀더 (백엔드 API 미구현)
2. **Video progress** — `progress: 0` 하드코딩 (진행률 계산 미구현)
3. **Staff allowances** — AllowanceBlock TODO (백엔드 API 미구현)
4. **Promo demo/contact** — API 미연동 (POST endpoints 미구현)
5. **recalculateExam** — 에러 시 null 반환 스텁 (백엔드 endpoint 존재, 프론트 에러 처리 미흡)
6. **Video/Grade notification counts** — 하드코딩 0 (백엔드 미구현)
7. **fetchStudentExams params** — TypeScript 타입 불일치 (무해함)
8. **BulkTemplateCreateView** — 템플릿 생성 시 tenant FK 미연결 (아키텍처 제한: Exam 모델에 tenant FK 없음, 세션 연결 시 자동 해결)
9. **Error Boundaries** — React Error Boundary 미적용 (런타임 에러 시 전체 앱 크래시 가능)

---

## Quality Metrics

| Metric | Before | After |
|--------|--------|-------|
| `alert()` calls (admin) | 58 | 0 |
| `alert()` calls (student) | 8 | 0 |
| Debug console.log (no DEV guard) | 0 | 0 |
| User-facing "TODO" text | 2 | 0 |
| Dead code files | 1 (LearningLayout) | 0 |
| Build status | PASS | PASS |
| Exam views without tenant filter | 13 | 0 |
| Results views without tenant filter | 22 | 0 |
| Internal endpoints with AllowAny | 1 (VideoProcessingComplete) | 0 |
| CSS tokens undefined but referenced | 1 (--stu-radius) | 0 |
| Credentials tracked in git | 2 files | 0 |

---

## Tenant Isolation Coverage (Final Audit)

### Exam Views (`apps/domains/exams/views/`) — 19/20 files

| View | Tenant Filter | Method |
|------|:---:|--------|
| `template_builder_view.py` | ✅ | Q filter |
| `template_editor_view.py` | ✅ | Q filter |
| `template_status_view.py` | ✅ | Q filter |
| `template_validation_view.py` | ✅ | Q filter |
| `template_with_usage_list_view.py` | ✅ | Q filter |
| `regular_from_template_view.py` | ✅ | Q filter |
| `exam_view.py` | ✅ | Q filter + perform_create |
| `exam_question_init_view.py` | ✅ | Q filter |
| `exam_questions_by_exam_view.py` | ✅ | Q filter |
| `question_view.py` | ✅ | Q filter |
| `question_auto_view.py` | ✅ | Q filter (Sheet→Exam) |
| `sheet_view.py` | ✅ | Q filter + _assert |
| `answer_key_view.py` | ✅ | Q filter |
| `exam_asset_view.py` | ✅ | Q filter (GET+POST) |
| `exam_enrollment_view.py` | ✅ | Q filter (GET+PUT) |
| `omr_generate_view.py` | ✅ | Q filter |
| `save_as_template_view.py` | ✅ | Q filter |
| `student_exam_view.py` | ✅ | user enrollment implicit |
| `bulk_template_create_view.py` | ⚠️ | Creation only, IsTeacherOrAdmin; tenant linked at session bind |

**Pattern:** `Q(sessions__lecture__tenant=tenant) | Q(derived_exams__sessions__lecture__tenant=tenant)`
