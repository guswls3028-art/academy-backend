# Academy 전체 도메인 점검 지도

- 요청: 2026-10-09 KST, 사용자가 모든 도메인의 숨은 버그·업무 결함을 순서대로 점검·수정하도록 위임.
- 실행 소유: `student-domain-20261009-01a10bcb`. 전체 실행 순서는 [PLAN](../PLAN.md), 현재 제품 정책은 각 도메인 정본이 소유한다.
- 단계: **학생 상세·연결 질문 동선 운영 확인 / 학원 선택 경계 출시 검증 / 직원 조회·시급 변경 실패 복구 검증**. 각 도메인 전체가 점검을 통과한 것으로 간주하지 않는다.
- 구조 기준: backend `58589aaae21b5051e5054e4a1c7a9c7234b93def`, frontend `a3290e20062a3acd5de9b3582400e7ebe82e629e`.

## 범위와 누락 확인

메뉴 이름만 세지 않고 관리자·선생님·학생·학부모·외부 방문자·플랫폼 운영자의
진입점, API, 작업 큐, 데이터 소비 화면을 함께 대조한다. 기능 플래그로 숨겨진
항목, 이전 URL의 별칭, 앱에 직접 연결되지 않은 서버 기능도 대상이다.

이번 구조 수집은 22개 업무 묶음, 145개 소스 영역, 49개 라우팅 소스 파일을
대조했다. 수집한 영역·라우팅 파일 중 미분류는 0개다. 이는 **구조 분류의 결과**이며
모든 URL·권한 조합이나 결함을 검증했다는 뜻이 아니다. 기존 frontend 테스트 파일
301개 역시 검사 후보 목록이지 현재 통과 증거가 아니다.

[영역·라우팅 파일 전체 목록](domain-quality-map.inventory.md)에 구조 대조 결과를 보존한다.
구조 대조의 원본은 아래 실행 소스다. 각 작업 착수 시 최신 main과 차이를 확인하고
새 진입점/도메인은 먼저 지도에 추가한다. `apps/support/*` 호환 경로는 같은 이름의
도메인과 함께 검증하며 별도 완료로 중복 집계하지 않는다.

- Backend: `apps/api/v1/urls.py`, `apps/core/{urls,auth_urls}.py`, `apps/billing/`,
  `apps/domains/*`, `apps/support/*`, `academy/{domain,application,adapters,framework}`.
- Frontend: `src/core/router/`, `src/auth/`, `src/app_{admin,teacher,student,dev}/app/`,
  각 역할의 `domains/*`, `src/landing/`, `src/app_promo/`, `src/features/staff-clock/`, `functions/`.
- 하위 경로: 관리자 `StaffRoutes`, `MaterialsRoutes`, `StorageRoutes`, `ClinicRoutes`,
  `MessagesRoutes`, `ToolsRoutes`; 역할별 라우터·메뉴에 실제 연결됐는지도 대조한다.
- 검사 진입점: frontend `e2e/suites.mjs`, `playwright.development-release.config.ts`,
  [실사용 검사 목록](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/REAL-USE-E2E-INVENTORY.md),
  backend 도메인 tests와 공식 CI. 파일 존재, 테스트 통과, 격리 실사용, 운영 확인을 구별한다.

## 작업 순서와 도메인별 완료 범위

현재 학생 작업을 끝낸 뒤 아래 순서로 진행한다. 재현된 정보 누출·데이터 손실·금액
오류·핵심 업무 중단은 순서보다 우선한다. 각 행 안의 하위 항목도 모두 상태를 기록한다.
역할별로 지원하지 않는 기능은 서버의 실제 권한 정책을 확인해 해당 없음으로 기록한다.

| 순서·ID | 도메인·주요 사용자 | 빠뜨리지 않을 동선·경계 | 소유 모듈·정본 / 연결 검사 |
|---|---|---|---|
| 선행 D01 | 학생·학부모 등록/계정/상세 — 교직원·학생·학부모 | 등록·승인·중복·일괄 업로드·정보 수정·태그·메모·삭제/복원·형제 연결·대리보기·질문/클리닉 이력·390px | `students`, `parents`, `student_app`, 역할별 `students/profile`; [student-core](../domain/student-core.md), [student-creation](../domain/student-creation.md), [student-lifecycle](../domain/student-lifecycle.md), [parent-account](../domain/parent-account.md); 계정/학생 상세/대리보기 E2E |
| 1 D00 | 인증·테넌트·역할 — 모든 사용자 | 최초 로그인·비밀번호/계정 복구·만료·로그아웃·역할 변경·테넌트/자녀 전환·직접 URL·객체 권한·캐시/초안 격리 | `core`, `auth`, tenant middleware/permissions, `teachers/parents`; [account-first-use](../domain/account-first-use.md), [account-recovery](../domain/account-recovery.md); 권한/tenant·계정 실사용 |
| 2 D02 | 직원·강사·근태·급여·경비 — 대표·직원 | 직원 계정·시급·근무시간·자정/월말·수정/중복·세전/공제/세후 합계·월 확정/취소·경비·내보내기. 사용자 지정 시급제·기본 3.3%와 개별 설정을 구분 | `staffs`, `teachers`, `staff`, `profile`, `features/staff-clock`; [staff-operations](../domain/staff-operations.md); 직원 UI/급여 계산·동시성 |
| 3 D03 | 수강료·청구·수납 — 대표·교직원·학부모 | 금액/할인·청구 생성·중복·부분 납부·취소/환불·미납·월/기간 경계·학생 연결·권한·집계 | `fees`; [fees](../domain/fees.md); 수강료 생성→납부→reload→학생/학부모 확인 |
| 4 D04 | 플랫폼 구독·결제 — 대표·운영자 | 가입/변경·카드 등록 callback·실패/재시도·중복 webhook·해지·서비스 접근·결제 내역 | `apps/billing`, 관리자 settings/billing, 개발자 billing, promo; billing tests/결제 경계. 실제 과금은 합성 검증과 명확히 구분 |
| 5 D05 | 강의·수강·분반·차시 — 관리자·교사·학생 | 생성/수정·수강 추가/종료·강의 종료·정규/보강·삭제 제약·시간표·이전 URL·권한 연쇄 | `lectures`, `enrollment`, `sessions`; [lecture-sessions](../domain/lecture-sessions.md); 학생·출결·영상·평가 연결 |
| 6 D06 | 출결·등원·보강 — 교직원·학생·학부모 | 명단·상태 변경·정정·일괄 처리·중복·같은 날 다회차·취소·자정·선택 학생/차시 유지·알림 결과 | `attendance`, 선생님 attendance, 학생 attendance; [attendance](../domain/attendance.md), [arrival-operations](../domain/arrival-operations.md); 출결·클리닉·알림 연계 |
| 7 D07 | 일정·진도·학습 할 일 — 학생·학부모·교사 | 현재/종료/미래 강의·마감·완료·재개·누락/중복·시간대·자정·오늘/전체 집계·종료 방학특강 노출 | `progress`, `schedule`, `sessions`, `today`, `student_app`; [state-transitions](../domain/state-transitions.md); 대시보드와 실제 상세 상태 대조 |
| 8 D08 | 시험·문항·OMR·응시 — 교사·학생 | 원본·문항/배점·시험 배정·응시/재응시·미제출·OMR 인식/수정·부분 실패·파일 재처리·채점 권한 | `exams`, `assets/omr`, `submissions`, `academy/domain/omr`; [exam-grading](../domain/exam-grading.md), [omr](../domain/omr.md); OMR/학생 응시 실사용 |
| 9 D09 | 과제·제출·첨부 — 교직원·학생·학부모 | 공지/배정·파일/사진·다중 첨부·재제출·늦은 제출·제출자·조회/처리·실패 복구·부분 성공 | `homework`, `homework_results`, `submissions`, `submit`; [homework-grading](../domain/homework-grading.md); 학생 제출→교직원 처리→학생 결과 |
| 10 D10 | 채점·성적·성적표·오답노트 — 교사·학생·학부모 | 0점/미채점/미제출 구분·저장/재채점·만점 변경·통과/되돌리기·평균/누적·수동 판단 보존·공개 범위·출력 | `results`, `scores`, `grades`, `homework_results`; [student-performance-console](../domain/student-performance-console.md), [student-grade-report](../domain/student-grade-report.md); 성적→클리닉/할 일/학부모 |
| 11 D11 | 클리닉 대상·예약·통과 — 교직원·학생 | 대상 산출·현재 수강 자격·같은 날 다중 시간대·정원/경합·예약/취소·출석·통과 취소·이력 페이지 | `clinic`, `clinic-idcard`; [clinic-targets](../domain/clinic-targets.md), [clinic-booking](../domain/clinic-booking.md); 성적/예약/등원 교차 검증 |
| 12 D12 | 영상 — 관리자·교사·학생 | 업로드→Batch 처리→노출·수강/공개 권한·YouTube/HLS·탐색/건너뛰기/속도·장시간/갱신/복원·좋아요/댓글/재생목록·종료 강의 | `video/videos`, `academy/application/video`; [session-video-access](../domain/session-video-access.md), [direct-video-access](../domain/direct-video-access.md); 실제 CDN·시청 갱신 검사 |
| 13 D13 | 자료·저장소·인벤토리 — 교직원·학생 | 폴더/소유권·용량·PDF/HWP/HWPX/문서/이미지·업로드/미리보기/다운로드·이름/유형·만료·삭제 보상·다중 파일 | `inventory/storage/materials/assets`, R2 adapter; [inventory-storage](../domain/inventory-storage.md); 사용자 파일 보존·공개 자료 연결 |
| 14 D14 | 매치업·분석·적중 리포트 — 교사·공개 방문자 | 원본/연도/학기·문항 분리/수정·분석 제안/승인·리포트 편집·공개 공유·대체 업로드·페이지 누락 | `matchup`, tools/storage 관련 화면; [matchup](../domain/matchup.md); 원본→수동 검토→보고서→홈페이지 열람 |
| 15 D15 | 문서 도구·AI·타이머 — 교직원 | PDF/PPT/HWPX 변환·문제 생성/리뷰·원본 보존·진행/실패/재시도·취소·작업 귀속·멱등성·Beta 한계·타이머 배포 | `tools`, `ai`, `academy/{domain,application,adapters}`, teacher assistant; [problem-studio](../domain/problem-studio.md), [ppt-question-generator](../domain/ppt-question-generator.md), [problem-review-report](../domain/problem-review-report.md), [timer-distribution](../domain/timer-distribution.md) |
| 16 D16 | 공지·게시판·질문·상담 — 교직원·학생·학부모 | 작성/편집/삭제·본문/첨부·읽기 권한·댓글/좋아요·검색/페이지·작성 초안·자녀 전환·답변/알림·직접 링크 | `community`, `counseling`, `notice/notices`; [community](../domain/community.md), frontend STUDENT-PARENT-APP-CONTRACT; 초안/커뮤니티 실사용 |
| 17 D17 | 홈페이지·공개 자료·홍보·문의 — 외부인·게시자·대표 | godmin/tchul/다른 tenant 라우팅·공개 본문·PDF 등 읽기·모바일·SEO/미리보기·게시자 권한·미발행 자료·공개 상담/후기 | `landing_public`, `src/landing`, `landing-public`, `app_promo`, Pages functions; [public-resource-board](../domain/public-resource-board.md); 외부 무로그인 열람과 내부 작성 |
| 18 D18 | 알림톡·앱 알림·발송 기록 — 교직원·학생·학부모 | 실제 기능/표시/승인 템플릿 일치·수신자·예약/취소·중복·발송/도착 구분·실패/과금·읽음·권한 | `messaging/messages/comms/notifications/admin-notifications`; [messaging policy](../ssot/messaging-policy.md), [messaging-delivery-log](../domain/messaging-delivery-log.md); 승인 공통 채널·정확한 템플릿, SMS/LMS fallback 금지 |
| 19 D19 | 대시보드·통계·사용 분석 — 대표·교사·학생·운영자 | 집계 원천과 목록 일치·기간/시간대·0건/오류·진행 상태·자녀/tenant·자동 분석은 제안·오래된 캐시 | `dashboard`, analytics/productAnalytics, support analytics; [product-usage-analytics](../domain/product-usage-analytics.md); 업무 결과와 대시보드 합계 대조 |
| 20 D20 | 조직·개인 설정·테마·가이드·약관 — 모든 역할 | 저장/복원·역할 접근·연락처·테넌트 설정·기능 플래그·390px/접근성·가이드와 실제 UI 일치·약관 링크 | `settings`, `profile`, `guide`, `legal`, shared program/theme; frontend 역할별 owning docs; 공개/관리/학생 화면 일관성 |
| 21 D21 | 플랫폼 운영·유지보수·배포 — 운영자 | tenant 설정·문의함·장애/유지보수·작업 큐/재시도·캐시·health·API 계약·관측·릴리스/복구·인증 경계 | `developer/maintenance`, app_dev, core, framework/adapters, workflows/functions; [deployment-modes](../operations/deployment-modes.md), [state-integrity-monitor](../operations/state-integrity-monitor.md); 현재 HOLD와 공식 게이트 유지 |

## 모든 도메인에서 확인할 공통 조합

1. 역할·테넌트·객체: 허용 사용자 성공, 다른 사용자/자녀/tenant 거부, 역할 변경·탈퇴·만료 뒤 재진입.
2. 생명주기: 없음/초기/진행/완료/마감/종료/취소/삭제/복원. 과거·미래 자료와 현재 업무를 혼합하지 않는다.
3. 금액·날짜·수치: 0/음수/경계값/소수·반올림·NULL, 한국 시간 자정/월말/연말, 화면과 서버 합계 일치.
4. 요청 순서: 느린 응답·역순 도착·중복 클릭·동시 편집·화면 이동·새로고침·뒤로가기·세션 변경.
5. 실패와 복구: 네트워크 단절·403/404/409/429/5xx·부분 성공·응답 유실·재시도 후 중복 방지·입력 보존.
6. 조회: 정렬/검색/필터/페이지·50건 이후·중복/누락·빈 결과와 실패 구별·수정 후 모든 소비 화면 갱신.
7. 파일·비동기: 이름/크기/형식·긴 처리·worker 재실행·만료 링크·삭제 보상·원본/수동 작성 데이터 보존.
8. 사용성: 실제 역할의 진입→행동→결과→reload→다른 역할 확인, desktop/390px·키보드·포커스·로딩/오류 상태.

## 연결 업무 검사

| 원천 변경 | 반드시 같이 확인할 결과 |
|---|---|
| 학생/계정 연결·삭제·복원 | 로그인·학부모 자녀 선택·수강·학생별 기록·수신자·대리보기 |
| 수강/강의 종료·차시 날짜 | 영상 권한·할 일·출결·클리닉 자격·홈 요약·알림 |
| 시험/과제 점수·만점·재채점 | 성적표·통과/미완료·오답노트·클리닉 대상·학생/학부모 표시 |
| 근태/시급/공제·수납/취소 | 세전/세후·월 합계·확정 내역·내보내기·역할별 조회 |
| 업로드·비동기 결과·공개 전환 | 게시 본문/첨부·R2/CDN·외부 열람·테넌트/게시자 권한 |
| 메시지 생성·취소·worker 결과 | 화면의 발송 상태·실제 수신자/템플릿·중복 방지·집계·개인정보 노출 |

## 상태와 종료 증거

각 하위 동선은 `미착수 → 조사 → 재현/정상 확인 → 수정 → 집중 검증 → 필수 CI →
격리 실사용 → 운영 확인 → 완료`로 추적한다. 적용하지 않는 단계는 사유를 남긴다.
코드가 안 바뀐 정상 동선도 검사 조건·버전·증거 없이 완료로 표시하지 않는다.
재현되지 않은 의심은 후보로 남기며, 실패를 지우거나 테스트를 느슨하게 만들어 완료하지 않는다.

| 항목 | 2026-10-09 현재 증거 | 다음 행동 |
|---|---|---|
| D01 학생 상세·학생별 기록 | backend [PR599](https://github.com/guswls3028-art/academy-backend/pull/599) `bf23b8f56704c56826bdfa46445ea795a45135be`, frontend [PR715](https://github.com/guswls3028-art/academy-frontend/pull/715)·[PR716](https://github.com/guswls3028-art/academy-frontend/pull/716) `a9cd3567ad0ef71facc82b9abac97993715c8ff8` 운영 반영. [frontend run37872808381](https://github.com/guswls3028-art/academy-frontend/actions/runs/37872808381) 동일 빌드 실사용 27건(생략·재시도 0), 양쪽 cleanup tenant/user 0, 운영 읽기 검증 통과. 서버·화면 배포 묶음 검사와 3개 운영 도메인 버전 일치. 배포 정적 자산+합성 API로 1366/1100/390px, 메모 저장→reload 확인, 예외/가로 넘침/예상 밖 업무 요청 0 | 해당 상세 동선 운영 확인 완료. 등록·일괄 업로드·삭제/복원 등 D01의 나머지 동선은 별도 점검 필요 |
| D16 연속 탭 이동·학부모 질문 작성 | 느린 CPU에서 작성창 소실 재현 후 location effect 경합 수정. PR716에 포함, 최종 번들 커뮤니티 5건·필수 CI·동일 빌드 실사용과 운영 승격 통과 | 연결된 질문 작성 동선 완료. D16 전체 게시/댓글/공개 자료 점검은 순서표대로 진행 |
| D00 명시적 학원 선택 | 잘못된/비활성 헤더의 다른 학원 대체와 대소문자 중복 코드 임의 선택 재현·수정. 새 회귀 14건+기존 23건=37건 및 PostgreSQL·SQLite 필수 CI 통과. [PR601](https://github.com/guswls3028-art/academy-backend/pull/601)·[PR603](https://github.com/guswls3028-art/academy-backend/pull/603), `cbfa0eb142e2b0a1bcae90e219c6da0275a92541`의 [run37882312061](https://github.com/guswls3028-art/academy-backend/actions/runs/37882312061) 운영 완료. 6개 이미지 보안 검사, 격리 개발 Excel/PPT/R2 실사용, preprod DB·CDN·부하, 임시 인스턴스 종료 후 건강 상태 기반 교체 통과. godmin/tchul 정상 조회·빈 값/잘못된 명시 선택 거부 9건 및 공식 서버/화면 배포 묶음·잠금 해제 확인 | 해당 선택 경계 운영 확인 완료. D00 전체 인증·역할·계정 복구 검사는 별도 유지. Cyrus High는 고정 버전·기한·실제 런타임 검증에 한정된 검토이며 영구 해결로 집계하지 않음 |
| D02 전체 목록·시급 변경 이력 실패 | 501번째 직원 누락, 동일 정렬키 페이지 불안정, 시급 태그/개별 배정의 이력 저장 실패에도 변경 성공하는 6개 경로를 재현·수정. backend [PR602](https://github.com/guswls3028-art/academy-backend/pull/602), frontend [PR718](https://github.com/guswls3028-art/academy-frontend/pull/718). 직원 서버 117건·하위 사례 19건, 화면 36건, 최종 번들의 페이지/복구·본인 근무 8건 통과. 기본 3.3%·분 단위 급여·0원 시급·기존 마감 및 시급 고정 회귀도 서버 묶음에서 통과 | 최신 main을 포함한 backend `99403555a` 필수 CI 통과 후 `2fe2c1ef3`으로 병합. [run37890575883](https://github.com/guswls3028-art/academy-backend/actions/runs/37890575883) 공식 배포 검증 진행. frontend `8a20020b9` CI 통과. 서버→화면 격리 실사용·운영 확인 전이며 D02 전체 완료로 집계하지 않음 |
| D03 수납 목록·기한·중복 기록·조회 복구 | backend [PR604](https://github.com/guswls3028-art/academy-backend/pull/604) `2f29f2378`: 안정 정렬·UNPAID 묶음과 잠긴 청구서의 예상 누적 수납액 대조. 오래된 화면 거부→동일 키 결과 복구→새 부분납 성공을 포함한 45건 통과, 동시 두 담당자 PostgreSQL 회귀 추가. frontend [PR719](https://github.com/guswls3028-art/academy-frontend/pull/719) `d682a5caa`: 전체 목록, 한국 시간 말일, PC·모바일 수납 재시도·변경 잔액 재확인, 학생 상세/납부 조회 오류 복구. 관리자 제품 번들 9건·학생 5건, 390px 확인 통과 | 서버·화면 최신 필수 CI 모두 통과→직원 배치 완료→backend 선행 배포→같은 frontend 산출물 격리 실사용·운영 확인. **운영 미반영, D03 전체 미완료** |
| D04 입금 신고의 오래된 미납 청구서 | 최근 12건 밖의 미납이 조회에서 사라져 다음 입금 신고를 막는 문제 재현·수정. backend [PR605](https://github.com/guswls3028-art/academy-backend/pull/605) `700dc0f5c`: 최근 12건 이력과 별도로 모든 미납/연체 유지. 조회→입금 신고→reload와 tenant 격리를 포함한 12건 및 필수 CI 통과 | 수납 배치 이후 정상 backend 배포·운영 영향 확인. 외부 카드 결제 HOLD 유지. **운영 미반영, D04 전체 미완료** |
| D05 수강 등록·전체 명단 | 학생·강의·차시·수강 ID의 소수값 절삭 4개 경로와 선생님 명단의 첫 200명 잘림 재현·수정. backend [PR606](https://github.com/guswls3028-art/academy-backend/pull/606) `0028be10a`: 수강·강의 API 106건 및 필수 CI 통과. frontend [PR720](https://github.com/guswls3028-art/academy-frontend/pull/720) `e4d402dde`: 501명·연락처·오류/중복/누락/건수 변경→복구→reload, 관리자 차시 등록 연계 14건을 소스와 운영 빌드에서 각각 통과. 타입·린트·가드·빌드 및 390px/1366px 확인 | frontend 필수 CI 후 선행 배치 완료→서버·화면 순서로 격리 실사용·운영 확인. **운영 미반영, D05 전체 미완료** |
| D06 출결 관계·이력·상태 표시 | 잘못 연결된 학원·강의 관계 5종의 요약 노출/차시 상세 허용과 파생 숨김 변경 재현. backend [PR607](https://github.com/guswls3028-art/academy-backend/pull/607) `3acff435f`: 출결·학생 앱 113건, 정상 종료·퇴원 이력과 읽기 무변경 검증. frontend [PR721](https://github.com/guswls3028-art/academy-frontend/pull/721) `8957b2e85`: 미입력·합산 라벨 수정, 이력 카드 보존과 현재 접근 가능한 차시만 연결. 학생·학부모 × 390px/1366px 소스·빌드 각 4건, 재시도·상세·뒤로가기·reload 및 정적/빌드 검사 통과 | 필수 CI→선행 배치 완료→서버 선행 배포→화면 동일 산출물 실사용·운영 확인. **운영 미반영, D06 전체 미완료** |
| D07 일정 조회·숨김 입력 경계 | 잘못된 자유 입력 시간 3종이 전체 일정 조회를 실패시키고 소수·불리언 ID가 다른 일정으로 잘리는 문제, 큰 ID의 DB 오류 재현. 해석 불가 시각은 null로 수업을 보존하고 숨김/되돌리기는 0이 아닌 정수 ID만 허용. 학생 앱 71건에서 정상 시간·일정 조회, 잘못된 입력의 무변경, 정수 문자열·음수 클리닉 ID·반복/되돌리기 보존 확인 | 최초 PR 및 필수 CI 후 순차 배포·운영 확인. 할 일·진도 등 D07 나머지 동선은 별도 점검. **운영 미반영** |
| 나머지 및 위 도메인의 미검증 하위 동선 | 구조 분류 완료, 이번 순차 점검 **미착수 또는 조사 단계**. 이전 릴리스나 테스트 존재를 현재 완료로 승격하지 않음 | 현재 코드/정책·실사용 흐름을 하위 동선별로 좁혀 점검 |

후속 조사 큐(완료 증거 아님): D21 저장소 Dependabot #20의 개발 의존성
`source-map-js` High(`CVE-2026-93749`, 수정 1.2.2)는 영향 범위·업데이트 검증을
기다린다. 이 진단을 현재 운영 브라우저의 노출로 추정하거나 해결 완료로 표시하지 않는다.

작업 카드에는 재현 조건, 영향 역할/tenant, 심각도, 원인, 수정 파일, 파생 영향,
정상·실패·복구 검사, PR/커밋/CI, 실사용 산출물과 정리 결과, 운영 SHA/시각을 남긴다.
정확한 tenant/object 승인 없이 사용자 데이터를 삭제하거나 운영에서 합성 변경을
실행하지 않는다. 미해결 항목은 이유·다음 행동을 보존하고 전체 무결함을 보장하지 않는다.
