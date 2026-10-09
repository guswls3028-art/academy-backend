# 전체 도메인 점검 구조 인벤토리

2026-10-09 KST 구조 스냅샷. [점검 지도](domain-quality-map.md)의 범위 대조 자료이며 테스트 통과나 운영 완료 증거가 아니다.

- backend: `58589aaae21b5051e5054e4a1c7a9c7234b93def`
- frontend: `a3290e20062a3acd5de9b3582400e7ebe82e629e`
- 업무 묶음 22 / 소스 영역 145 / 라우팅 소스 49 / 기존 frontend 테스트 파일 301
- 미분류 0 / 없는 소스 경로 0

## 소스 영역

| 저장소 | 경로 | 업무 묶음 |
|---|---|---|
| backend | `apps/domains/ai` | 문서 도구·AI 작업·타이머 |
| backend | `apps/domains/assets` | 시험·문항·OMR·응시, 자료·저장소·인벤토리 |
| backend | `apps/domains/attendance` | 출결·등원·보강 |
| backend | `apps/domains/clinic` | 클리닉 대상·예약·통과 |
| backend | `apps/domains/community` | 공지·게시판·질문·상담 |
| backend | `apps/domains/enrollment` | 강의·수강·분반·차시 |
| backend | `apps/domains/exams` | 시험·문항·OMR·응시 |
| backend | `apps/domains/fees` | 수강료·청구·수납 |
| backend | `apps/domains/homework` | 과제·제출·첨부·처리 |
| backend | `apps/domains/homework_results` | 과제·제출·첨부·처리 |
| backend | `apps/domains/inventory` | 자료·저장소·인벤토리 |
| backend | `apps/domains/landing_public` | 홈페이지·공개 자료·홍보·문의 |
| backend | `apps/domains/lectures` | 강의·수강·분반·차시 |
| backend | `apps/domains/matchup` | 매치업·분석·적중 리포트 |
| backend | `apps/domains/messaging` | 알림톡·알림·발송 기록 |
| backend | `apps/domains/parents` | 학생·학부모 계정·등록·상세, 인증·테넌트·역할 권한 |
| backend | `apps/domains/progress` | 일정·진도·학습 할 일 |
| backend | `apps/domains/results` | 채점·성적·성적표·오답노트 |
| backend | `apps/domains/schedule` | 일정·진도·학습 할 일 |
| backend | `apps/domains/staffs` | 직원·강사·근태·급여·경비 |
| backend | `apps/domains/student_app` | 학생·학부모 계정·등록·상세, 일정·진도·학습 할 일 |
| backend | `apps/domains/students` | 학생·학부모 계정·등록·상세 |
| backend | `apps/domains/submissions` | 시험·문항·OMR·응시, 과제·제출·첨부·처리 |
| backend | `apps/domains/teacher_app` | 직원·강사·근태·급여·경비, 일정·진도·학습 할 일 |
| backend | `apps/domains/teachers` | 직원·강사·근태·급여·경비, 인증·테넌트·역할 권한 |
| backend | `apps/domains/tools` | 문서 도구·AI 작업·타이머, 매치업·분석·적중 리포트 |
| backend | `apps/domains/video` | 영상 업로드·처리·권한·시청 |
| backend | `apps/support/ai` | 문서 도구·AI 작업·타이머 |
| backend | `apps/support/analytics` | 대시보드·통계·사용 분석 |
| backend | `apps/support/attendance` | 출결·등원·보강 |
| backend | `apps/support/clinic` | 클리닉 대상·예약·통과 |
| backend | `apps/support/community` | 공지·게시판·질문·상담 |
| backend | `apps/support/enrollment` | 강의·수강·분반·차시 |
| backend | `apps/support/exams` | 시험·문항·OMR·응시 |
| backend | `apps/support/fees` | 수강료·청구·수납 |
| backend | `apps/support/homework` | 과제·제출·첨부·처리 |
| backend | `apps/support/homework_results` | 과제·제출·첨부·처리 |
| backend | `apps/support/inventory` | 자료·저장소·인벤토리 |
| backend | `apps/support/landing_public` | 홈페이지·공개 자료·홍보·문의 |
| backend | `apps/support/lectures` | 강의·수강·분반·차시 |
| backend | `apps/support/matchup` | 매치업·분석·적중 리포트 |
| backend | `apps/support/messaging` | 알림톡·알림·발송 기록 |
| backend | `apps/support/omr` | 시험·문항·OMR·응시 |
| backend | `apps/support/progress` | 일정·진도·학습 할 일 |
| backend | `apps/support/results` | 채점·성적·성적표·오답노트 |
| backend | `apps/support/staffs` | 직원·강사·근태·급여·경비 |
| backend | `apps/support/student_app` | 학생·학부모 계정·등록·상세, 일정·진도·학습 할 일 |
| backend | `apps/support/students` | 학생·학부모 계정·등록·상세 |
| backend | `apps/support/submissions` | 시험·문항·OMR·응시, 과제·제출·첨부·처리 |
| backend | `apps/support/teacher_app` | 직원·강사·근태·급여·경비, 일정·진도·학습 할 일 |
| backend | `apps/support/tools` | 문서 도구·AI 작업·타이머, 매치업·분석·적중 리포트 |
| backend | `apps/support/video` | 영상 업로드·처리·권한·시청 |
| frontend | `src/app_admin/domains/admin-notifications` | 알림톡·알림·발송 기록 |
| frontend | `src/app_admin/domains/clinic` | 클리닉 대상·예약·통과 |
| frontend | `src/app_admin/domains/community` | 공지·게시판·질문·상담 |
| frontend | `src/app_admin/domains/counseling` | 공지·게시판·질문·상담 |
| frontend | `src/app_admin/domains/dashboard` | 대시보드·통계·사용 분석 |
| frontend | `src/app_admin/domains/developer` | 개발자 운영·유지보수·배포 |
| frontend | `src/app_admin/domains/enrollment` | 강의·수강·분반·차시 |
| frontend | `src/app_admin/domains/exams` | 시험·문항·OMR·응시 |
| frontend | `src/app_admin/domains/fees` | 수강료·청구·수납 |
| frontend | `src/app_admin/domains/guide` | 조직 설정·테마·가이드·약관 |
| frontend | `src/app_admin/domains/homework` | 과제·제출·첨부·처리 |
| frontend | `src/app_admin/domains/landing-public` | 홈페이지·공개 자료·홍보·문의 |
| frontend | `src/app_admin/domains/lectures` | 강의·수강·분반·차시 |
| frontend | `src/app_admin/domains/legal` | 조직 설정·테마·가이드·약관 |
| frontend | `src/app_admin/domains/maintenance` | 개발자 운영·유지보수·배포 |
| frontend | `src/app_admin/domains/materials` | 자료·저장소·인벤토리 |
| frontend | `src/app_admin/domains/messages` | 알림톡·알림·발송 기록 |
| frontend | `src/app_admin/domains/notice` | 공지·게시판·질문·상담 |
| frontend | `src/app_admin/domains/profile` | 학생·학부모 계정·등록·상세, 직원·강사·근태·급여·경비, 인증·테넌트·역할 권한 |
| frontend | `src/app_admin/domains/results` | 채점·성적·성적표·오답노트 |
| frontend | `src/app_admin/domains/scores` | 채점·성적·성적표·오답노트 |
| frontend | `src/app_admin/domains/sessions` | 강의·수강·분반·차시, 일정·진도·학습 할 일 |
| frontend | `src/app_admin/domains/settings` | 조직 설정·테마·가이드·약관 |
| frontend | `src/app_admin/domains/staff` | 직원·강사·근태·급여·경비 |
| frontend | `src/app_admin/domains/storage` | 자료·저장소·인벤토리 |
| frontend | `src/app_admin/domains/students` | 학생·학부모 계정·등록·상세 |
| frontend | `src/app_admin/domains/submissions` | 시험·문항·OMR·응시, 과제·제출·첨부·처리 |
| frontend | `src/app_admin/domains/tools` | 문서 도구·AI 작업·타이머, 매치업·분석·적중 리포트 |
| frontend | `src/app_admin/domains/videos` | 영상 업로드·처리·권한·시청 |
| frontend | `src/app_teacher/domains/assistant` | 문서 도구·AI 작업·타이머 |
| frontend | `src/app_teacher/domains/attendance` | 출결·등원·보강 |
| frontend | `src/app_teacher/domains/clinic` | 클리닉 대상·예약·통과 |
| frontend | `src/app_teacher/domains/comms` | 알림톡·알림·발송 기록 |
| frontend | `src/app_teacher/domains/counseling` | 공지·게시판·질문·상담 |
| frontend | `src/app_teacher/domains/developer` | 개발자 운영·유지보수·배포 |
| frontend | `src/app_teacher/domains/exams` | 시험·문항·OMR·응시 |
| frontend | `src/app_teacher/domains/fees` | 수강료·청구·수납 |
| frontend | `src/app_teacher/domains/guide` | 조직 설정·테마·가이드·약관 |
| frontend | `src/app_teacher/domains/lectures` | 강의·수강·분반·차시 |
| frontend | `src/app_teacher/domains/notifications` | 알림톡·알림·발송 기록 |
| frontend | `src/app_teacher/domains/profile` | 학생·학부모 계정·등록·상세, 직원·강사·근태·급여·경비, 인증·테넌트·역할 권한 |
| frontend | `src/app_teacher/domains/results` | 채점·성적·성적표·오답노트 |
| frontend | `src/app_teacher/domains/scores` | 채점·성적·성적표·오답노트 |
| frontend | `src/app_teacher/domains/settings` | 조직 설정·테마·가이드·약관 |
| frontend | `src/app_teacher/domains/staff` | 직원·강사·근태·급여·경비 |
| frontend | `src/app_teacher/domains/storage` | 자료·저장소·인벤토리 |
| frontend | `src/app_teacher/domains/students` | 학생·학부모 계정·등록·상세 |
| frontend | `src/app_teacher/domains/today` | 일정·진도·학습 할 일 |
| frontend | `src/app_teacher/domains/tools` | 문서 도구·AI 작업·타이머, 매치업·분석·적중 리포트 |
| frontend | `src/app_teacher/domains/videos` | 영상 업로드·처리·권한·시청 |
| frontend | `src/app_student/domains/attendance` | 출결·등원·보강 |
| frontend | `src/app_student/domains/clinic` | 클리닉 대상·예약·통과 |
| frontend | `src/app_student/domains/clinic-idcard` | 클리닉 대상·예약·통과 |
| frontend | `src/app_student/domains/community` | 공지·게시판·질문·상담 |
| frontend | `src/app_student/domains/dashboard` | 대시보드·통계·사용 분석 |
| frontend | `src/app_student/domains/exams` | 시험·문항·OMR·응시 |
| frontend | `src/app_student/domains/fees` | 수강료·청구·수납 |
| frontend | `src/app_student/domains/grades` | 채점·성적·성적표·오답노트 |
| frontend | `src/app_student/domains/guide` | 조직 설정·테마·가이드·약관 |
| frontend | `src/app_student/domains/inventory` | 자료·저장소·인벤토리 |
| frontend | `src/app_student/domains/notices` | 공지·게시판·질문·상담 |
| frontend | `src/app_student/domains/notifications` | 알림톡·알림·발송 기록 |
| frontend | `src/app_student/domains/profile` | 학생·학부모 계정·등록·상세, 직원·강사·근태·급여·경비, 인증·테넌트·역할 권한 |
| frontend | `src/app_student/domains/sessions` | 강의·수강·분반·차시, 일정·진도·학습 할 일 |
| frontend | `src/app_student/domains/settings` | 조직 설정·테마·가이드·약관 |
| frontend | `src/app_student/domains/submit` | 시험·문항·OMR·응시, 과제·제출·첨부·처리 |
| frontend | `src/app_student/domains/video` | 영상 업로드·처리·권한·시청 |
| frontend | `src/app_dev/domains/automation` | 개발자 운영·유지보수·배포 |
| frontend | `src/app_dev/domains/billing` | 플랫폼 구독·카드·결제 |
| frontend | `src/app_dev/domains/dashboard` | 대시보드·통계·사용 분석 |
| frontend | `src/app_dev/domains/inbox` | 개발자 운영·유지보수·배포 |
| frontend | `src/app_dev/domains/maintenance` | 개발자 운영·유지보수·배포 |
| frontend | `src/app_dev/domains/productAnalytics` | 대시보드·통계·사용 분석 |
| frontend | `src/app_dev/domains/tenants` | 인증·테넌트·역할 권한, 개발자 운영·유지보수·배포 |
| backend | `apps/core` | 인증·테넌트·역할 권한, 조직 설정·테마·가이드·약관, 개발자 운영·유지보수·배포, 홈페이지·공개 자료·홍보·문의 |
| backend | `apps/billing` | 플랫폼 구독·카드·결제 |
| backend | `apps/api` | 인증·테넌트·역할 권한, 개발자 운영·유지보수·배포 |
| backend | `apps/shared` | 인증·테넌트·역할 권한, 개발자 운영·유지보수·배포 |
| backend | `academy/domain` | 문서 도구·AI 작업·타이머, 시험·문항·OMR·응시 |
| backend | `academy/application` | 문서 도구·AI 작업·타이머, 영상 업로드·처리·권한·시청, 시험·문항·OMR·응시 |
| backend | `academy/adapters` | 자료·저장소·인벤토리, 문서 도구·AI 작업·타이머, 영상 업로드·처리·권한·시청, 알림톡·알림·발송 기록, 개발자 운영·유지보수·배포 |
| backend | `academy/framework` | 인증·테넌트·역할 권한, 개발자 운영·유지보수·배포 |
| frontend | `src/auth` | 인증·테넌트·역할 권한, 학생·학부모 계정·등록·상세, 직원·강사·근태·급여·경비 |
| frontend | `src/core` | 인증·테넌트·역할 권한, 개발자 운영·유지보수·배포 |
| frontend | `src/shared` | 인증·테넌트·역할 권한, 조직 설정·테마·가이드·약관, 개발자 운영·유지보수·배포 |
| frontend | `src/landing` | 홈페이지·공개 자료·홍보·문의, 공지·게시판·질문·상담, 매치업·분석·적중 리포트, 자료·저장소·인벤토리 |
| frontend | `src/app_promo` | 홈페이지·공개 자료·홍보·문의, 플랫폼 구독·카드·결제 |
| frontend | `src/features/staff-clock` | 직원·강사·근태·급여·경비 |
| frontend | `src/app_admin/app` | 인증·테넌트·역할 권한, 개발자 운영·유지보수·배포 |
| frontend | `src/app_teacher/app` | 인증·테넌트·역할 권한, 개발자 운영·유지보수·배포 |
| frontend | `src/app_student/app` | 인증·테넌트·역할 권한, 개발자 운영·유지보수·배포 |
| frontend | `src/app_dev/app` | 인증·테넌트·역할 권한, 개발자 운영·유지보수·배포 |
| frontend | `functions` | 홈페이지·공개 자료·홍보·문의, 인증·테넌트·역할 권한, 개발자 운영·유지보수·배포 |

## 라우팅 진입 소스

파일 단위 목록이다. 동적 경로·하위 라우트·권한·기능 플래그의 실제 조합은 해당 도메인 점검에서 대조한다.

| 저장소 | 경로 |
|---|---|
| frontend | `src/app_admin/app/AdminRouter.tsx` |
| frontend | `src/app_admin/domains/clinic/ClinicRoutes.tsx` |
| frontend | `src/app_admin/domains/materials/MaterialsRoutes.tsx` |
| frontend | `src/app_admin/domains/messages/MessagesRoutes.tsx` |
| frontend | `src/app_admin/domains/staff/StaffRoutes.tsx` |
| frontend | `src/app_admin/domains/storage/StorageRoutes.tsx` |
| frontend | `src/app_admin/domains/tools/ToolsRoutes.tsx` |
| frontend | `src/app_dev/app/DevAppRouter.tsx` |
| frontend | `src/app_promo/app/PromoRouter.tsx` |
| frontend | `src/app_student/app/StudentRouter.tsx` |
| frontend | `src/app_teacher/app/TeacherRouter.tsx` |
| frontend | `src/core/router/AppRouter.tsx` |
| frontend | `src/core/router/AuthRouter.tsx` |
| frontend | `src/landing/app/LandingRouter.tsx` |
| frontend | `src/shared/ui/assessment/StudentScoreTrendChart.tsx` |
| backend | `apps/api/config/urls.py` |
| backend | `apps/api/v1/internal/ai/urls.py` |
| backend | `apps/api/v1/urls.py` |
| backend | `apps/billing/urls.py` |
| backend | `apps/core/auth_urls.py` |
| backend | `apps/core/urls.py` |
| backend | `apps/domains/ai/urls.py` |
| backend | `apps/domains/assets/omr/urls.py` |
| backend | `apps/domains/assets/urls.py` |
| backend | `apps/domains/attendance/urls.py` |
| backend | `apps/domains/clinic/urls.py` |
| backend | `apps/domains/community/api/urls.py` |
| backend | `apps/domains/enrollment/urls.py` |
| backend | `apps/domains/exams/urls.py` |
| backend | `apps/domains/fees/urls.py` |
| backend | `apps/domains/homework/urls.py` |
| backend | `apps/domains/homework_results/urls.py` |
| backend | `apps/domains/inventory/urls.py` |
| backend | `apps/domains/landing_public/api/urls.py` |
| backend | `apps/domains/lectures/urls.py` |
| backend | `apps/domains/matchup/urls.py` |
| backend | `apps/domains/messaging/urls.py` |
| backend | `apps/domains/progress/urls.py` |
| backend | `apps/domains/results/urls.py` |
| backend | `apps/domains/staffs/urls.py` |
| backend | `apps/domains/student_app/urls.py` |
| backend | `apps/domains/students/urls.py` |
| backend | `apps/domains/submissions/urls.py` |
| backend | `apps/domains/teacher_app/urls.py` |
| backend | `apps/domains/teachers/urls.py` |
| backend | `apps/domains/tools/urls.py` |
| backend | `apps/domains/video/urls.py` |
| backend | `apps/domains/video/urls_internal.py` |
| backend | `apps/support/analytics/urls.py` |
