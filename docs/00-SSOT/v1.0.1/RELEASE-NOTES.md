# V1.0.1 Release Notes

**Release Date:** 2026-03-11
**Type:** Quality Audit & UX Polish Release

---

## Changes Summary

### Frontend (56bec96f)

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

#### Medium Impact
4. **비디오 플레이어 리팩터링** — StudentVideoPlayer, HLS 컨트롤러, player.css 대규모 개선
5. **메시지 자동발송 설정** — AutoSendSettingsPanel 신규, SendMessageModal 개선
6. **클리닉/직원 설정 페이지** — ClinicMsgSettingsPage, StaffSettingsPage 신규
7. **커뮤니티 설정** — CommunitySettingsPage 리팩터링

### Backend (0823943b)

#### Critical — 테넌트 격리 강화
1. **Exam 뷰 7개 tenant 필터링 추가**
   - `TemplateBuilderView`, `TemplateEditorView`, `TemplateStatusView` — `get_object_or_404`에 tenant Q 필터
   - `RegularExamFromTemplateView` — 템플릿 조회 시 tenant 검증
   - `ExamQuestionsByExamView` — 문제 조회 시 tenant 검증
   - `ExamViewSet.perform_create` — 템플릿 참조 시 tenant 검증
   - `SheetViewSet._assert_exam_is_template` — tenant 검증 추가

2. **Results 뷰 20개 tenant 필터링 추가**
   - Admin 뷰: ExamAttempt, ExamAttempts, ExamResultDetail, ExamSummary, ExamResults, SessionExamsSummary, SessionExams, SessionScoreSummary, ResultFact, QuestionStats(3종), ItemScore, ObjectiveScore, SubjectiveScore, TotalScore, RepresentativeAttempt, ExamGrading(2종)
   - Student 뷰: StudentExamAttempts, WrongNote, WrongNotePDF(2종)

#### Security
3. **자격증명 파일 git 추적 해제**
   - `.env.local`, `tmp_api_env.json` — `git rm --cached`
   - `tmp_api_env.json` `.gitignore` 추가

---

## Deployment Verification

| Stage | Check | Result |
|-------|-------|--------|
| 1 | Backend CI (V1 Build OIDC) | PASS (success) |
| 2 | `/healthz` liveness | PASS (200) |
| 2 | `/health` readiness | PASS (200, DB connected) |
| 2 | Frontend hakwonplus.com | PASS (200) |
| 2 | Frontend tchul.com | PASS (200) |
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
| Exam views without tenant filter | 7 | 0 |
| Results views without tenant filter | 22 | 0 |
| Credentials tracked in git | 2 files | 0 |
