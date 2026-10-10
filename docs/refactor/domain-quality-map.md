# Academy 전체 도메인 점검 지도

- 요청: 2026-10-09 KST, 사용자가 모든 도메인의 숨은 버그·업무 결함을 순서대로 점검·수정하도록 위임.
- 추가 요청: 2026-10-10 KST, 전체 진행 승인을 재확인하고 기능을 찾기 어렵다는 사용자 평가에 따라 발견성·초심자 동선을 모든 도메인의 공통 점검 항목으로 추가했다.
- 실행 소유: `student-domain-20261009-01a10bcb`. 전체 실행 순서는 [PLAN](../PLAN.md), 현재 제품 정책은 각 도메인 정본이 소유한다.
- 단계: **22개 도메인 구조 분류·기록된 수정 운영 확인 / 기능 발견성·학생 삭제·복원·보안 후속 및 D01 파일 등록 연락처·재등록 수정 운영 완료**. 이전 실패와 수정 후 성공 증거는 별도로 보존한다. 완료 판정은 증거가 있는 하위 동선에만 적용한다.
- 최신 운영 묶음: backend `aa4529482fa189c52497281d22f5aa2aba5faabc`/run38002341219, frontend `b38f6a0f80fa0684a8bcec4d8b8bb3bb33290ad8`/run38016877898. 두 공식 실행 전체 성공, 성공 manifest·잠금 해제·hakwonplus/godmin/tchul 버전 대조 통과.
- 구조 기준: backend `58589aaae21b5051e5054e4a1c7a9c7234b93def`, frontend `a3290e20062a3acd5de9b3582400e7ebe82e629e`.

## 범위와 누락 확인

메뉴 이름만 세지 않고 관리자·선생님·학생·학부모·외부 방문자·플랫폼 운영자의
진입점, API, 작업 큐, 데이터 소비 화면을 함께 대조한다. 기능 플래그로 숨겨진
항목, 이전 URL의 별칭, 앱에 직접 연결되지 않은 서버 기능도 대상이다.

이번 구조 수집은 22개 업무 묶음, 145개 소스 영역, 49개 라우팅 소스 파일을
대조했다. 수집한 영역·라우팅 파일 중 미분류는 0개다. 이는 **구조 분류의 결과**이며
모든 URL·권한 조합이나 결함을 검증했다는 뜻이 아니다. 기존 frontend 테스트 파일
301개 역시 검사 후보 목록이지 현재 통과 증거가 아니다.

2026-10-10 제품 소스 재대조: backend `2862ca392`, frontend `2b6a8cd2c`
(병합 `81ebe997d`와 동일 tree)에서 22개 묶음·145개 영역·49개 라우팅 파일이
모두 유지됐다. 새로 누락된 영역/라우팅, 미분류, 사라진 경로는 0개이고 frontend
테스트 파일은 312개다. 검사 파일 개수의 증가는 실사용/운영 통과 개수와 다르다.

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

학생 상세에서 시작해 아래 순서로 1차 점검과 확인된 결함의 수리를 진행했다.
남은 하위 동선도 같은 순서로 추적하며, 재현된 정보 누출·데이터 손실·금액 오류·
핵심 업무 중단은 순서보다 우선한다. 각 행의 전체 항목을 완료한 것으로 확대하지 않는다.
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
9. 기능 발견성: 메뉴 이름만 보고 용도를 이해하는지, 일상 업무 표현 검색으로 세부 기능까지 도달하는지, 기능 모음/다음 행동/빈 화면 안내가 보이는지 확인한다. 원장·교사·학생·학부모별 권한과 기기에서 재검증하며 메뉴를 추가했다는 이유만으로 완료하지 않는다.

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

### 2026-10-10 D01 학생 파일 등록 후속

소유 `domain-student-import-20261010-01a10bcb`. backend
[PR615](https://github.com/guswls3028-art/academy-backend/pull/615)는 운영
`aa4529482fa189c52497281d22f5aa2aba5faabc`, 공식
[run38002341219](https://github.com/guswls3028-art/academy-backend/actions/runs/38002341219)
전체 성공으로 확인했다. frontend [PR727](https://github.com/guswls3028-art/academy-frontend/pull/727)의
최종 후보 `4a135b9b7a8966680a6c17b82a58425e5235dbde`는 전체 품질
run38006526789와 브라우저 run38006526784를 통과했다. 병합
`eceb76fd0dc2c32c183c7a533b905608b5f33b47`의 공식
[run38009522155](https://github.com/guswls3028-art/academy-frontend/actions/runs/38009522155)는
개발 실사용 26건 통과·1건 실패로 운영 승격 전에 중단됐다. 두 검증 테넌트의
tenant/user·R2·프로세스·리스너 잔여 0을 확인했다. 첫 Excel 업로드는 성공했으나
검사 코드가 자동으로 열린 결과창을 닫지 않고 두 번째 업로드를 눌렀다.
동일 `eceb76fd0` 산출물에서 원인을 재현하고 결과의 신규/중복/실패 건수와
원본 행을 확인한 뒤 창을 닫도록 고쳤다. 격리 집중 검증은 정상 통과했고,
실패 재현 tenant750·수정 검증 tenant751 모두 cleanup 및 후속 Inspect 0이다.
후속 [PR728](https://github.com/guswls3028-art/academy-frontend/pull/728)
`01e493cfff29bc073fb60478a1be0ed0600a9aae`는 품질 run38014449190과
브라우저 run38014448881을 통과했다. 세 mock 묶음 454/347/394건,
iPhone WebKit 87건(Chromium 전용 Web Locks 1건은 해당 브라우저에서 제외),
배포 번들 29건과 테마 1건이 통과했다. 병합
`b38f6a0f80fa0684a8bcec4d8b8bb3bb33290ad8`의 정식
[run38016877898](https://github.com/guswls3028-art/academy-frontend/actions/runs/38016877898)은
전체 성공했다. 동일 산출물 실사용 27건, 생략·flake·전송 재시도·mutation 재실행·
브라우저 결함 0을 확인했다. 기본/교차 테넌트 cleanup 및 후속 Inspect에서
tenant/user·R2·프로세스·리스너 잔여 0과 API digest/릴리스 일치가 모두 확인됐다.
후보 fingerprint는 `860d50fc2af9f618f5f53bab97e79e3228252e7fca544c91c00a13f58fc8e27e`다.
정확한 production 환경 승인(deployment6975371381) 후 동일 파일을 승격했고,
운영 읽기 전용 동선·공개 테넌트 가용성·세 도메인 버전 대조를 통과했다.
실제 배포 자산+합성 API로 PC1366/390px 파일 오류·교체 복구 2건도 재시도0으로
통과했다. 직접 본 PC의 원본 행·사유 안내와 두 화면의 오류 건수, 스크롤 본문·고정
등록 버튼, 가로 넘침 없음을 확인했다. 합성 화면 검사는 실제 서버 쓰기 실사용과
구분하며 인증/관찰 요청 0, 허용된 Cloudflare beacon 차단만 각 1건이었다.

- 잘못된 학생 연락처를 번호 없음으로 바꾸거나 학생 번호를 누락 학부모 번호로
  대신 사용하지 않는다. 숫자 Excel 연락처의 앞 0은 복구하고, 명시된 학교유형은
  보존한다. 0·불리언·문자·비ASCII 전화번호와 소수 학년 입력의 계정 생성도 막는다.
- 일부 오류는 실제 Excel 행 번호·이름·사유로 표시하며 정상 행 등록을 보존한다.
  새 파일 읽기 실패 시 이전 선택을 비우고, 닫기/재열기 뒤 늦게 끝난 파일 읽기의
  성공·실패·로딩 종료가 새 선택을 덮지 않도록 한다.
- backend 최종 CI의 SQLite 6,264건/하위 검사 1,416건, PostgreSQL
  6,510건/하위 검사 1,430건이 통과했다. 형제 재등록 시 기존 학생/학부모 비밀번호와
  다른 테넌트의 계정 분리를 검증했다. 격리 개발의 실제 Excel/PPT/문서 변환·R2,
  preprod 120요청/오류0·운영 DB 접근 거부·CDN을 통과하고 임시 인스턴스 종료를
  재조회했다. 운영 성공 manifest와 기존 화면의 세 운영 도메인 버전 대조도 통과했다.
- 화면 PC/390px 오류 표시·파일 교체 복구와 닫기/재열기 경합 3건은 재시도0,
  브라우저 결함0으로 통과했다. 경합은 이전 코드의 실제 실패를 확인한 회귀다.
  새 실사용 검사는 UI 업로드→worker 결과→수정 파일 재업로드→명부 reload→
  학생 로그인·기존 학부모 로그인/형제 연결까지 동일 산출물 집중 검증에서 통과했다.
  집중 검증의 통과를 정식 전체 실사용 게이트나 운영 승격으로 대신하지 않는다.

남은 D01 경계는 표지+명단/복수 시트의 화면·서버 선택 일치, 영문 헤더 및
학년 등 필드별 미리보기와 최종 판정 일치, 공유 파일 입력의 비활성 상태/키보드
동선이다. 현재 화면은 첫 시트만 읽고 서버는 후보 시트를 선택하므로 동일하다고
가정하지 않는다. 이번 연락처·재등록 하위 동선의 증거를 도메인 전체 완료로 확대하지 않는다.
현재 정책은 [학생 생성](../domain/student-creation.md)과
[화면 계정 흐름](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/ACCOUNT-CREDENTIAL-FLOWS.md)이 소유한다.

최종 PR 검사 전에 직원 KPI 필터 검사에서 무관한 상담 요약 요청이 끊기는 실패가
있었다. trace에서 `setOffline`이 해당 조회를 차단한 것을 확인하고, 필터 검사에서
필요한 reconnect 이벤트로 실제 재조회를 유도하도록 수정했다. 요청 수·KPI·0건
상태·선택 필터·엄격한 브라우저 오류 검사는 보존했고 로컬 3회와 전체 CI가 통과했다.
실패한 run38004929371의 로그/trace는 성공 증거와 구별해 보존했다. 급여 제품의
동작 변경은 아니다.

### 선행 운영 증거

선행 후속 확인(당시의 대기 상태를 대체하며, 최신 운영 묶음은 위 D01 후속 참조):

- 당시 화면은 PR726의 `81ebe997df816e951db3201cde65f54aa57929fd`다.
  [run37966598131](https://github.com/guswls3028-art/academy-frontend/actions/runs/37966598131)의
  동일 산출물 실사용 27건이 생략·불안정·요청 재시도·mutation replay 없이 통과했다.
  primary `qa-ymath-realuse-fe-37966598131-1-8de492e66276`, cross
  `qa-ymath-realuse-fe-37966598131-1-6439124cb35a`(tenant747)의 tenant/user/R2/
  process/listener가 모두 0이다. 공식 승인6967839024 후 전체 운영 읽기 검증이 통과했고,
  hakwonplus.com·godmin.kr·tchul.com 실제 버전과 서버 `0e4f6a21e` 배포 묶음이 일치했다.
  배포 bundle artifact11633802580의 SHA256은
  `8668db4f38d8f5a25c4b1f8dd828a63d5ce09de2f4f6248f41e66dd4f39d8d7e`이며,
  실사용 영수증의 내부 산출물 SHA256은
  `3a10f77182846caa7d02ded88beefb84c80d8a27f9af02706eff4044b0c73776`이다.

- PR725 화면 `61e92dbdbc00100c3e1594749ef914f7d4c83e31`의
  [run37940243283](https://github.com/guswls3028-art/academy-frontend/actions/runs/37940243283)는
  **운영 완료**다. artifact `11623940806`, 산출물 SHA256
  `90d9c58163385eb1cde6032dd00f11e252804b1100e8ec7cb69e1b261861927c`의 격리 실사용
  27건이 모두 통과했다. 생략/flaky/예상 밖 오류/변경 요청 재전송/strict browser
  결함은 0이다. 이전에 실패했던 평가 복사와 새 제출함·상담 메모/읽음·원장 설정의
  실제 API 저장/재조회도 포함한다. PC/모바일의 690초 영상 재생·토큰 갱신,
  같은 DOM/세션과 진도 보존을 확인했다. 두 exact QA 학원은 tenant/user/R2/
  프로세스/리스너 잔재 0이며, 서버 release `0e4f6a21e`와 digest 일치를 확인했다.
  공식 production 승인 `6963453708` 후 운영 읽기 검증 및 hakwonplus/godmin/tchul의
  서버/화면 배포 묶음 검사까지 통과했다. 재현한 실패와 검사 조건을 지우지 않았다.
- PR609/PR722 배포 묶음: 화면 run37912703522 **attempt 2 성공**. 동일 산출물
  실사용 27건, 생략/재시도/예상 밖 오류/업무 요청 재전송 0. 두 격리 tenant
  740/741의 tenant/user/R2/프로세스/리스너 잔재 0. 공식 승인·운영 읽기와
  hakwonplus/godmin/tchul의 exact 서버/화면 배포 묶음 검증 통과.
  첫 시도 실패와 tenant738 별도 복구 이력은 삭제하지 않는다.
- PR610 서버 run37923766151: **운영 완료**. 격리 개발 Excel/PPT/R2, preprod
  120건 오류 0, p95 51.5ms/p99 72.1ms, CDN 200/200/206,
  임시 i-0ada5341285f6d45b 종료 후 공식 승인·건강 기반 교체·runtime/manifest·
  잠금 해제 통과. 현재 FE722와 세 운영 도메인의 공식 배포 묶음 일치 확인.
- PR724 화면 후보: D08 관리자 시험·복사 원본의 전체 페이지 조회, 검색 결과만
  일괄 선택, 과목/차시 변경 시 이전 선택 제거, 읽기 실패 재시도 추가.
  소스 32건, 최종 번들 14건과 후속 6건, PC/390px 통과. 전체 CI에서 발견한
  상담 알림 fixture 누락과 매치업 정상 선택 유지 URL 검증은 `aa0d1b84b`에서
  수정하고 관련 16건 통과. 전체 CI 성공 후 `6d41a6ad2` main에 병합.
  run37929103430은 **26건 통과·평가 복사 1건 실패**로 운영 승격하지 않았다.
  양쪽 exact QA 학원은 tenant/user/R2/프로세스/리스너 0, release/digest 일치로
  정리 완료했다. 장시간 영상의 desktop/mobile 재생·갱신은 통과했지만 이 실패
  후보를 릴리스 성공으로 집계하지 않는다. 실패 원문을 복구했다고 주장하지 않으며,
  후속 조사에서 차시 수강생 등록이 만든 출결 때문에 조기 차시 삭제가 403이 되는
  정리 순서 충돌을 실제 API로 재현했다. 부모 시나리오가 소유한 수강 등록을 먼저
  제거하고 차시 삭제/GET 404를 확인하는 순서로 수정한 뒤 실제 API 회귀 통과.
  PR725에 원 오류/안전한 단계 진단과 정리 계약 회귀를 포함해 전체 27건을 다시 검증했고 통과했다.
- D17 상담 수신함 후속 `15bbf78ef`: 200건 이후 미확인 문의 누락과 메모 저장의
  읽음 상태 되돌림 재현·수정. 전체 집계·서버 페이지/미확인 필터·알림용 요약,
  입력 검증·명시 필드만 원자적 저장. 서버 연계 40건/하위 16건 및 마지막 쿼리
  파서 변경 후 8건/하위 13건 통과. 화면 최종 4건(390/1366px), 긴 메모 실패
  복구/저장/reload/필터와 44px 페이지 버튼 확인. 스키마·정적 검사 통과.
  동일 메모의 동시 편집 충돌 감지·공개 폼 나머지 입력 경계는 미완료이며,
  격리 실사용의 실제 상담 생성→메모 저장/reload→읽음/필터도 PR725에서 통과했다.
  **기록된 상담 수신함 동선 서버·화면 운영 완료**.
- D19 제출함 후속: 200건 절단과 완료/실패 필터 무시를 실제 요청/390px에서
  재현. 서버 상태/실패 분류 후 50개 페이지, 같은 시각의 ID 정렬, 전역 실패 집계,
  관리자/교사 페이지·필터 URL 보존과 조회 실패 복구를 추가. 현재 페이지의 유효한
  선택만 일괄 작업하며 상세 미리보기/학생 지정 흐름을 보존한다. 서버 최종 7건/
  하위 11건·기존 보안 회귀 56건, 소스 브라우저 9건 및 PC/390px 확인 통과.
  PR611/PR725에 통합했고 서버 `e3e3dc433`의 PostgreSQL/SQLite 전체 CI가 통과했다.
  화면 최종 제품 빌드는 관련 17건과 fixture 보완 후 미리보기 5건 통과, PC/390px
  확인. 조회 키 공통 관리와 두 화면의 최근 24시간 접수 기준 안내를 보완한
  `22d130dc2`의 최종 빌드 제출함/미리보기 9건, 타입/전체 린트, 배포·복구·E2E
  보호 검사 통과. 전체 품질 CI run37936045774와 브라우저 CI run37936045845가
  통과했고 동일 tree의 main `61e92dbdb`로 병합했다. run37940243283에서 실제
  제출/채점 결과의 관리자·선생님 제출함→reload와 전체 게이트를 통과해 운영 반영했다.
  서버는 PR611로 운영 완료했다.
  오래된 완료/실패의 전체 이력 탐색은
  기존 24시간 생성 범위를 유지하므로 별도 정책 검토 대상으로 남긴다.
- D00 계정 복구 입력 경계 후속 `16a7bdd1a`: 정본/기존 호환 3경로에서 배열·숫자
  본문의 500과 객체 전화번호의 문자열 변환 처리를 실제 URL에서 재현했다. 계정
  조회·발송·비밀번호 변경 전에 객체/문자열 입력을 검증한다. 정상 공백/하이픈 입력,
  학생·학부모 pending 로그인, 직원 재설정과 전달 실패 보상을 포함해 89건/하위
  80건 통과. SQLite에서 생략된 PostgreSQL 행 잠금 1건은 전체 PostgreSQL CI로
  확인하며 통과로 집계하지 않는다. 정적·migration·API 계약 검사 통과.
  PR611 `94a6265ec`의 SQLite/PostgreSQL 전체 CI run37933812818 통과 후
  `0e4f6a21e`로 병합했다. [run37936117345](https://github.com/guswls3028-art/academy-backend/actions/runs/37936117345)는
  여섯 immutable 후보 검사·격리 개발 Excel/PPT/R2·preprod DB/CDN·120건 오류 0
  (p95 39.7ms/p99 41.8ms)과 임시 `i-0070d1bd404ec5616` 종료를 확인했다.
  공식 승인 후 migration·건강 기반 API/worker 교체·runtime/manifest·공유 잠금
  해제까지 전체 성공했다. 기존 FE722와 세 운영 도메인 배포 묶음도 통과했다.
  **PR611 서버 수정 운영 완료**이며 새 화면 후보의 실사용 증거와는 구별한다.
- D01 일괄 삭제·복원/Excel 충돌 후속 [PR613](https://github.com/guswls3028-art/academy-backend/pull/613)
  `2862ca392`: 잘못된 ID를 제외하고 나머지 학생을 변경하는 일괄 입력, 객체가 아닌
  본문의 500, 소수 `2.5`가 학생 `2`를 복원하는 충돌 해결 경로와 내부 영구삭제의
  ID 절삭을 실제 JWT/API에서 재현했다. 전체 일괄 선택은 조회/변경 전에 검증하고
  충돌 해결은 잘못된 행만 실패로 남겨 다음 정상 행의 복원을 유지한다. 예상 밖
  삭제/충돌 예외의 내부 저장소 정보는 사용자 응답에 노출하지 않는다. 삭제→목록/상세
  재조회→복원→영구삭제→재시도, 다른 학원 보존을 포함한 149건·하위 105건 및
  smoke 27건·하위 5건, 정적·migration·API 계약 통과. SQLite에서 생략된
  PostgreSQL 잠금 13건은 전체 PostgreSQL CI로 확인했고, 필수 CI
  [run37942682856](https://github.com/guswls3028-art/academy-backend/actions/runs/37942682856)가
  전체 성공했다. 화면 운영 확인 후 같은 tree의 main `a931f3f9e`로 병합했고
  [run37946734823](https://github.com/guswls3028-art/academy-backend/actions/runs/37946734823)의
  이미지 검사에서 새 `CVE-2026-107778 / krb5 / 1.21.3-5+deb13u1` High가
  발견돼 **개발/운영 진입 전 차단**됐다. 해당 실패 시점에는 공유 잠금 해제와
  운영 `0e4f6a21e` 유지를 확인했고 화면 canary의 고정 개발 기준선도 바꾸지 않았다.
  후속 소유 `domain-krb5-security-20261010-01a10bcb`의
  [PR614](https://github.com/guswls3028-art/academy-backend/pull/614)는 기존 Debian
  소스와 실제 버전을 유지한 upstream 수정 후보다. 로컬 oracle/보안 게이트
  171건·린트·셸 문법·diff와 최종 `97bbf8548` 전체 CI/실제 ARM 원본 crash·수정
  protocol 오류/정상 입력·패키지 의존·ABI 검사가 통과했다. 후보 run37957602402
  attempt1은 Tools의 공식 Office archive checksum 불일치로 새 스캔 전에 중단됐다.
  공식 배포처 및 실제 ARM archive 208175884바이트의 SHA256이 기존 pin과 일치했다.
  실패 작업 재실행 attempt2는 이전 attempt artifact 재사용을 거부하여 중단됐다.
  전체 attempt3에서 base/API/AI/Messaging 새 스캔이 통과하고 Tools에서도 Kerberos는
  검출되지 않았다. 그러나 Tools `b6fb07daea26`의 GnuTLS Critical 95210/High 95209·95184로
  전체 후보는 실패했다. 후속 `aa5f3eccc`는 기존 CUPS와 Debian 패치를 유지하며 upstream
  OpenSSL 빌드 옵션으로 의존성을 교체하는 후보다. 선행 `e834aee4e` CI는 임시 빌드
  단계까지 운영 Perl 제거 조건을 검사해 1건 실패했다. 운영 시작 단계를 명시하고
  복사 대상을 확인하도록 계약을 수정했다. 로컬 보안/운영 계약 30건·린트·셸 문법은
  통과했다. 전체 CI run37966984637도 성공했다(SQLite 6,247/skip223,
  PostgreSQL 6,493/skip5, 실제 ARM base 포함). 후속 후보 run37969236636의 실제 ARM
  Tools에서 CUPS library tests·원래 ELF symbol·OpenSSL 연결·옵션 API·GnuTLS 물리
  부재까지 통과했지만, 한글 DOCX→PDF 내용 비교에서 실패해 전체 후보는 중단됐다.
  `2f91a0391` 전체 필수 CI 후 후보 run37973395491에서 실제 PDF를 확보했다.
  Poppler 렌더링은 한글·띄어쓰기가 정상이며 PyMuPDF 1.25.3이 공백을 별도 줄로
  추출해 검증기가 오판한 것이었다. `9d646893e`는 한글 공백 표현만 정규화하고
  글자·순서·영문 문장·1쪽 조건을 보존한다. 같은 추출 결과를 재현한 red 1건을
  고친 뒤 12건과 실제 ARM PDF의 내용 재검증이 통과했다. 기존 원격 Codespace는
  billing 402로 시작되지 않았고 정지 상태를 재확인했다. `9d646893e` 전체 CI
  run37975163370은 SQLite 6,252/skip223, PostgreSQL 6,498/skip5 및 실제 ARM
  base가 통과했다. 최종 후보 run37977520609는 5개 이미지 빌드·새 보안 스캔과
  실제 ARM Tools의 한글 DOCX→PDF 검증, 완전한 후보 영수증 생성까지 모두 성공했다.
  공식 QA 최종 승인 6969082999와 complete=true 영수증을 확인했고 PR614는
  `3a55a3b65983a5b8831178b2e5835412e3952777`로 정상 병합했다.
  [공식 릴리스 run37979753832](https://github.com/guswls3028-art/academy-backend/actions/runs/37979753832)의
  여섯 이미지 새 보안 검사와 격리 개발의 Excel/PPT/한글 문서/R2 실사용을 통과했다.
  개발 active instance는 `i-0a8a7f01ec8fafd23`이며 API/Tools/AI 모두 같은 릴리스다.
  preprod는 운영 DB 접근 거부, 120건 부하 오류 0(p95 41.2ms/p99 46.6ms),
  CDN master/variant/segment 200/200/206과 `i-0630481663ea63680` 종료를 확인했다.
  정확한 실행의 공식 production 승인 `6969955772` 뒤 migration, warm baseline을
  유지한 워커 교체, API 건강 기반 교체, 실제 runtime digest/학생 영상 체인,
  성공 manifest와 공유 잠금 해제까지 전체 성공했다. 이전 개발 인스턴스
  `i-0683d49cde082257b`도 terminated로 재조회했다. 화면 `81ebe997d`
  run37966598131과 hakwonplus/godmin/tchul의 공식 배포 묶음 검사도 통과했다.
  **PR613 학생 일괄 처리와 PR614 보안 수정 운영 완료**다. 미검증 하위 동선은
  이 결과와 구분한다.
  앞선 run37957602402 attempt3의 소유 QA 이미지 태그 5개는 다른 참조와
  운영 digest 불일치를 확인해 정리했고, 태그 잔여 0·운영 digest 보존을 재조회했다.
  최종 성공 후보 run37977520609의 소유 QA 태그 5개도 운영 반영 후 정확한
  repository/tag/digest·다른 참조 부재를 확인해 정리했다. 각 태그 잔여 0과
  현재 운영 latest digest 보존을 각각 재조회했다. 진단용 공개 Office archive
  208,175,884바이트도 SHA256 증거를 남기고 정리했으며 실제 PDF·로그는 보존했다.
  [보안 정본](../operations/container-image-security.md)의 통과 조건을
  유지하고 새 예외 추가·기존 상한/기한 완화로 통과시키지 않는다.

각 하위 동선은 `미착수 → 조사 → 재현/정상 확인 → 수정 → 집중 검증 → 필수 CI →
격리 실사용 → 운영 확인 → 완료`로 추적한다. 적용하지 않는 단계는 사유를 남긴다.
코드가 안 바뀐 정상 동선도 검사 조건·버전·증거 없이 완료로 표시하지 않는다.
재현되지 않은 의심은 후보로 남기며, 실패를 지우거나 테스트를 느슨하게 만들어 완료하지 않는다.

| 항목 | 2026-10-10까지 확인한 증거 | 다음 행동 |
|---|---|---|
| D01 학생 상세·학생별 기록 | backend [PR599](https://github.com/guswls3028-art/academy-backend/pull/599) `bf23b8f56704c56826bdfa46445ea795a45135be`, frontend [PR715](https://github.com/guswls3028-art/academy-frontend/pull/715)·[PR716](https://github.com/guswls3028-art/academy-frontend/pull/716) `a9cd3567ad0ef71facc82b9abac97993715c8ff8` 운영 반영. [frontend run37872808381](https://github.com/guswls3028-art/academy-frontend/actions/runs/37872808381) 동일 빌드 실사용 27건(생략·재시도 0), 양쪽 cleanup tenant/user 0, 운영 읽기 검증 통과. 서버·화면 배포 묶음 검사와 3개 운영 도메인 버전 일치. 배포 정적 자산+합성 API로 1366/1100/390px, 메모 저장→reload 확인, 예외/가로 넘침/예상 밖 업무 요청 0 | 해당 상세 동선 운영 확인 완료. 삭제·복원 입력 후속은 위 PR613/614 운영 증거 참조. 파일 연락처·오류 행·형제 재등록은 위 D01 후속의 운영 증거 참조. 복수 시트/헤더·필드별 미리보기 등 나머지 하위 동선은 별도 점검 |
| D16 연속 탭 이동·학부모 질문 작성 | 느린 CPU에서 작성창 소실 재현 후 location effect 경합 수정. PR716에 포함, 최종 번들 커뮤니티 5건·필수 CI·동일 빌드 실사용과 운영 승격 통과 | 연결된 질문 작성 동선 완료. D16 전체 게시/댓글/공개 자료 점검은 순서표대로 진행 |
| D00 명시적 학원 선택 | 잘못된/비활성 헤더의 다른 학원 대체와 대소문자 중복 코드 임의 선택 재현·수정. 새 회귀 14건+기존 23건=37건 및 PostgreSQL·SQLite 필수 CI 통과. [PR601](https://github.com/guswls3028-art/academy-backend/pull/601)·[PR603](https://github.com/guswls3028-art/academy-backend/pull/603), `cbfa0eb142e2b0a1bcae90e219c6da0275a92541`의 [run37882312061](https://github.com/guswls3028-art/academy-backend/actions/runs/37882312061) 운영 완료. 6개 이미지 보안 검사, 격리 개발 Excel/PPT/R2 실사용, preprod DB·CDN·부하, 임시 인스턴스 종료 후 건강 상태 기반 교체 통과. godmin/tchul 정상 조회·빈 값/잘못된 명시 선택 거부 9건 및 공식 서버/화면 배포 묶음·잠금 해제 확인 | 해당 선택 경계 운영 확인 완료. D00 전체 인증·역할·계정 복구 검사는 별도 유지. Cyrus High는 고정 버전·기한·실제 런타임 검증에 한정된 검토이며 영구 해결로 집계하지 않음 |
| D02 전체 목록·시급 변경 이력 실패 | 501번째 직원 누락, 동일 정렬키 페이지 불안정, 시급 태그/개별 배정의 이력 저장 실패에도 변경 성공하는 6개 경로를 재현·수정. backend [PR602](https://github.com/guswls3028-art/academy-backend/pull/602), frontend [PR718](https://github.com/guswls3028-art/academy-frontend/pull/718). 직원 서버 117건·하위 사례 19건, 화면 36건, 최종 번들의 페이지/복구·본인 근무 8건 통과. 기본 3.3%·분 단위 급여·0원 시급·기존 마감 및 시급 고정 회귀도 서버 묶음에서 통과 | 서버 `2fe2c1ef362d2b07a8a9da918221ce3a654e3640` run37890575883, 화면 `ea8381ed8509423eebd79b3d01b26416f679ab8a` [run37893211864](https://github.com/guswls3028-art/academy-frontend/actions/runs/37893211864) 운영 완료. 동일 빌드 실사용 27건·cleanup zero·공식 승인·운영 배포 묶음 통과. **해당 수정 동선 운영 확인, D02 전체 미완료** |
| D03 수납 목록·기한·중복 기록·조회 복구 | backend [PR604](https://github.com/guswls3028-art/academy-backend/pull/604) `2f29f2378`: 안정 정렬·UNPAID 묶음과 잠긴 청구서의 예상 누적 수납액 대조. 오래된 화면 거부→동일 키 결과 복구→새 부분납 성공을 포함한 45건 통과, 동시 두 담당자 PostgreSQL 회귀 추가. frontend [PR719](https://github.com/guswls3028-art/academy-frontend/pull/719) `d682a5caa`: 전체 목록, 한국 시간 말일, PC·모바일 수납 재시도·변경 잔액 재확인, 학생 상세/납부 조회 오류 복구. 관리자 제품 번들 9건·학생 5건, 390px 확인 통과 | 서버 PR604 `3b8fc7bb74420d6919102d7d75d5acaf445582e6` [run37898306230](https://github.com/guswls3028-art/academy-backend/actions/runs/37898306230), 화면 PR719 `7dff36f6deb466df45a0e6f9d3f9f760d869a0f9` [run37901659955](https://github.com/guswls3028-art/academy-frontend/actions/runs/37901659955) 운영 완료. 동일 산출물 실사용 27건(생략·재시도 0), 양쪽 tenant/user·R2·프로세스·리스너 잔재 0, 공식 승인 후 운영 읽기와 3개 도메인 배포 묶음 통과. **기록된 수정 동선 운영 확인, 도메인 전체 미완료** |
| D04 입금 신고의 오래된 미납 청구서 | 최근 12건 밖의 미납이 조회에서 사라져 다음 입금 신고를 막는 문제 재현·수정. backend [PR605](https://github.com/guswls3028-art/academy-backend/pull/605) `700dc0f5c`: 최근 12건 이력과 별도로 모든 미납/연체 유지. 조회→입금 신고→reload와 tenant 격리를 포함한 12건 및 필수 CI 통과 | 서버 PR604 `3b8fc7bb74420d6919102d7d75d5acaf445582e6` [run37898306230](https://github.com/guswls3028-art/academy-backend/actions/runs/37898306230), 화면 PR719 `7dff36f6deb466df45a0e6f9d3f9f760d869a0f9` [run37901659955](https://github.com/guswls3028-art/academy-frontend/actions/runs/37901659955) 운영 완료. 동일 산출물 실사용 27건(생략·재시도 0), 양쪽 tenant/user·R2·프로세스·리스너 잔재 0, 공식 승인 후 운영 읽기와 3개 도메인 배포 묶음 통과. **기록된 수정 동선 운영 확인, 도메인 전체 미완료** |
| D05 수강 등록·전체 명단 | 학생·강의·차시·수강 ID의 소수값 절삭 4개 경로와 선생님 명단의 첫 200명 잘림 재현·수정. backend [PR606](https://github.com/guswls3028-art/academy-backend/pull/606) `0028be10a`: 수강·강의 API 106건 및 필수 CI 통과. frontend [PR720](https://github.com/guswls3028-art/academy-frontend/pull/720) `e4d402dde`: 501명·연락처·오류/중복/누락/건수 변경→복구→reload, 관리자 차시 등록 연계 14건을 소스와 운영 빌드에서 각각 통과. 타입·린트·가드·빌드 및 390px/1366px 확인 | 서버 PR604 `3b8fc7bb74420d6919102d7d75d5acaf445582e6` [run37898306230](https://github.com/guswls3028-art/academy-backend/actions/runs/37898306230), 화면 PR719 `7dff36f6deb466df45a0e6f9d3f9f760d869a0f9` [run37901659955](https://github.com/guswls3028-art/academy-frontend/actions/runs/37901659955) 운영 완료. 동일 산출물 실사용 27건(생략·재시도 0), 양쪽 tenant/user·R2·프로세스·리스너 잔재 0, 공식 승인 후 운영 읽기와 3개 도메인 배포 묶음 통과. **기록된 수정 동선 운영 확인, 도메인 전체 미완료** |
| D06 출결 관계·이력·상태 표시 | 잘못 연결된 학원·강의 관계 5종의 요약 노출/차시 상세 허용과 파생 숨김 변경 재현. backend [PR607](https://github.com/guswls3028-art/academy-backend/pull/607) `3acff435f`: 출결·학생 앱 113건, 정상 종료·퇴원 이력과 읽기 무변경 검증. frontend [PR721](https://github.com/guswls3028-art/academy-frontend/pull/721) `8957b2e85`: 미입력·합산 라벨 수정, 이력 카드 보존과 현재 접근 가능한 차시만 연결. 학생·학부모 × 390px/1366px 소스·빌드 각 4건, 재시도·상세·뒤로가기·reload 및 정적/빌드 검사 통과 | 서버 PR604 `3b8fc7bb74420d6919102d7d75d5acaf445582e6` [run37898306230](https://github.com/guswls3028-art/academy-backend/actions/runs/37898306230), 화면 PR719 `7dff36f6deb466df45a0e6f9d3f9f760d869a0f9` [run37901659955](https://github.com/guswls3028-art/academy-frontend/actions/runs/37901659955) 운영 완료. 동일 산출물 실사용 27건(생략·재시도 0), 양쪽 tenant/user·R2·프로세스·리스너 잔재 0, 공식 승인 후 운영 읽기와 3개 도메인 배포 묶음 통과. **기록된 수정 동선 운영 확인, 도메인 전체 미완료** |
| D07 일정 조회·숨김 입력 경계 | 잘못된 자유 입력 시간 3종이 전체 일정 조회를 실패시키고 소수·불리언 ID가 다른 일정으로 잘리는 문제, 큰 ID의 DB 오류 재현. 해석 불가 시각은 null로 수업을 보존하고 숨김/되돌리기는 0이 아닌 정수 ID만 허용. 학생 앱 71건에서 정상 시간·일정 조회, 잘못된 입력의 무변경, 정수 문자열·음수 클리닉 ID·반복/되돌리기 보존 확인 | 서버 PR604 `3b8fc7bb74420d6919102d7d75d5acaf445582e6` [run37898306230](https://github.com/guswls3028-art/academy-backend/actions/runs/37898306230), 화면 PR719 `7dff36f6deb466df45a0e6f9d3f9f760d869a0f9` [run37901659955](https://github.com/guswls3028-art/academy-frontend/actions/runs/37901659955) 운영 완료. 동일 산출물 실사용 27건(생략·재시도 0), 양쪽 tenant/user·R2·프로세스·리스너 잔재 0, 공식 승인 후 운영 읽기와 3개 도메인 배포 묶음 통과. **기록된 수정 동선 운영 확인, 도메인 전체 미완료** |
| D08·D09 평가 목록과 성적 선택 | 교사 시험·과제의 첫 100건 이후 누락을 재현. frontend `453559330`: 필터·안정 정렬·학원/로그인 경계를 유지하는 전체 조회, 부분 실패/중복/누락 거절→재시도, 501번째 평가 상세와 성적 선택→reload. 소스 18건 및 운영 빌드의 제출 처리·종합 성적 연계 42건, 390px/1366px, 타입·린트·가드·빌드 통과 | 서버 PR604 `3b8fc7bb74420d6919102d7d75d5acaf445582e6` [run37898306230](https://github.com/guswls3028-art/academy-backend/actions/runs/37898306230), 화면 PR719 `7dff36f6deb466df45a0e6f9d3f9f760d869a0f9` [run37901659955](https://github.com/guswls3028-art/academy-frontend/actions/runs/37901659955) 운영 완료. 동일 산출물 실사용 27건(생략·재시도 0), 양쪽 tenant/user·R2·프로세스·리스너 잔재 0, 공식 승인 후 운영 읽기와 3개 도메인 배포 묶음 통과. **기록된 수정 동선 운영 확인, 도메인 전체 미완료** |
| D09·D10 과제 점수 숫자 경계 | 빠른 입력·상세 수정에서 비정상 숫자/음수 허용을 실제 API에서 재현(20 실패·5 오류). backend `4444a0c69`: 불리언·NaN·무한대·넘침·음수를 필드별 400으로 거절, 기존 행 전체 보존과 신규 행 미생성→정상 재입력, 0점/소수/미채점 유지. 점수·교사 완료·진도 47건 통과(로컬 PostgreSQL 전용 1건 생략), 정적/스키마 검사 통과 | PR604 통합 PostgreSQL·전체 회귀 통과, run37898306230 서버 운영 반영·배포 묶음 확인. 기존 사용자 데이터 일괄 변경 없음. 해당 숫자 입력 경계 서버 반영 완료, D09·D10 전체 미완료 |
| D03–D10 통합 릴리스 | backend PR604 `ca0c0766d` 필수 CI·PostgreSQL 전체 통과, 로컬 관련 332건(세 PostgreSQL 전용 생략)·smoke 27건 통과. frontend PR719 `fe517cac2` 필수 CI와 최종 번들 89건 통과. backend는 `3b8fc7bb74420d6919102d7d75d5acaf445582e6`으로 병합, [run37898306230](https://github.com/guswls3028-art/academy-backend/actions/runs/37898306230) 전체 성공. 6개 보안 검사, 격리 개발 Excel/PPT/R2, preprod 운영 DB 거부·부하 120건 오류 0·CDN200/200/206, 임시 인스턴스 종료·호환 migration·건강 기반 rolling·실제 digest/manifest·잠금 해제 확인. 세 홈페이지와 공식 배포 묶음 통과 | 서버 PR604 `3b8fc7bb74420d6919102d7d75d5acaf445582e6` [run37898306230](https://github.com/guswls3028-art/academy-backend/actions/runs/37898306230), 화면 PR719 `7dff36f6deb466df45a0e6f9d3f9f760d869a0f9` [run37901659955](https://github.com/guswls3028-art/academy-frontend/actions/runs/37901659955) 운영 완료. 동일 산출물 실사용 27건(생략·재시도 0), 양쪽 tenant/user·R2·프로세스·리스너 잔재 0, 공식 승인 후 운영 읽기와 3개 도메인 배포 묶음 통과. **기록된 수정 동선 운영 확인, 도메인 전체 미완료** |
| D11 클리닉 전체 일정·보고서 | 첫 20건 밖 일정 누락과 390px 월력의 시간·장소 잘림 확인. frontend [PR722](https://github.com/guswls3028-art/academy-frontend/pull/722) `bd2f8751b`: 관리자·강사 전체 목록, 중복/누락/건수 변경 오류→재시도, 선택 날짜 전체 일정과 장소·참여자 표시. 31개 동일 날짜 일정·키보드·월 변경·스크롤/하단 바 및 1366/390px 확인, 소스 신규 22건과 최종 빌드 33건, 타입·린트·가드·빌드 통과 | PR722 `3cdf19e82` 최종 빌드 152건·전체 CI 통과 후 `5930bb3c2c56ba810b0e10d49160893e68a8b00b` 병합. [run37912703522](https://github.com/guswls3028-art/academy-frontend/actions/runs/37912703522) attempt2에서 동일 산출물 실사용 27건·cleanup zero·운영 확인 통과. 포함된 PR723은 닫고 소스 이력을 보존했다. **기록된 일정/보고서 동선 운영 확인, 클리닉 전체 미완료** |
| D12 학생 영상 재생·참여 연결 | 이번 직원 화면 릴리스 run37893211864의 동일 산출물 실사용 27건에 장시간 영상 재생·갱신 시나리오가 포함되어 통과, cleanup 0. 동일 영상 코드의 PR718 exact-head WebKit 시청 동선 10건 및 직접 접근 경계 증거 재사용. 현재 소유 문서는 좋아요·댓글·재생목록·다음 영상·재시도 계약을 포함 | 해당 기존 경로의 증거 재사용이며 영상 업로드·인코딩·전체 권한/기기 점검까지 완료로 표시하지 않음 |
| D13·D14 저장소·매치업 역할과 로그인 실패 | 실제 JWT에서 전역 직원 표시가 남은 학생/학부모의 교직원 파일 노출, 강사의 다른 작성자 보고서 노출 및 제출 잠금 우회 재현. 현재 tenant 역할만 적용하고 매치업의 일반 직원 접근 제한 유지. 저장소 만료/취소 JWT의 500을 401 인증 응답으로 교정. 로컬 API·원본 보존·학생/선택 자녀 정상 수정/재조회·원장 검수·tenant 격리·기존 보고서/분리 검수 179건과 세부 24건 통과. backend PR609에 통합, 과거 전역 직원 우회를 기대한 단위검사 2건과 실제 membership 없는 직접 호출 fixture 3건도 현재 역할 규칙에 맞춤. 비활성 membership 및 실제 JWT 24건·세부 24건 재검증 통과. frontend [PR723](https://github.com/guswls3028-art/academy-frontend/pull/723) `d8e06172d`: 탭·직접 주소·직원 기본 파일 진입 포함 PC/390px 역할 동선 10건, 최종 빌드의 파일 이동/삭제 복구·직접 자르기·공개 게시 등 82건과 정적/빌드 검사 통과 | PR609 `4c69ae255072929de5570072211aad9d5447f73d` [run37908819030](https://github.com/guswls3028-art/academy-backend/actions/runs/37908819030) 전체 성공. 최신 여섯 이미지 스캔, 격리 개발 Excel/PPT/R2, preprod DB 거부·120건 오류 0·CDN200/200/206·임시 인스턴스 종료, 공식 승인·migration·건강 기반 교체·런타임/manifest·잠금 해제 통과. 화면 PR722 `5930bb3c2` [run37912703522](https://github.com/guswls3028-art/academy-frontend/actions/runs/37912703522) attempt2에서 동일 산출물 실사용 27건·cleanup zero·운영 읽기·3개 도메인 배포 묶음 통과. **기록된 수정 동선 운영 확인, 전체 하위 동선 미완료** |
| D15 문제 리뷰 분석 결과·수동 초안·버전 | 분석 완료/실패 뒤 GET의 DB 쓰기로 목록·상세 실패, 지연 분석의 이미 저장된 초안 덮어쓰기, 불리언/소수 version 절삭을 실제 JWT 및 DB로 재현(20 실패). `aaf5f1a9b`: 읽기는 소유 job 결과의 응답만 구성, 명시적 쓰기는 같은 analyzing/job/version일 때만 조건부 반영. 스키마의 양의 정수 version 검증 적용. 결과 읽기→교사 수정→저장→reload·정수 문자열·stale 409·소유 경계·검수/출력/공개 64건, 정적·migration·OpenAPI 통과 | PR609 `4c69ae255072929de5570072211aad9d5447f73d` [run37908819030](https://github.com/guswls3028-art/academy-backend/actions/runs/37908819030) 전체 성공. 최신 여섯 이미지 스캔, 격리 개발 Excel/PPT/R2, preprod DB 거부·120건 오류 0·CDN200/200/206·임시 인스턴스 종료, 공식 승인·migration·건강 기반 교체·런타임/manifest·잠금 해제 통과. 화면 PR722 `5930bb3c2` [run37912703522](https://github.com/guswls3028-art/academy-frontend/actions/runs/37912703522) attempt2에서 동일 산출물 실사용 27건·cleanup zero·운영 읽기·3개 도메인 배포 묶음 통과. **기록된 수정 동선 운영 확인, 전체 하위 동선 미완료** |
| D16 게시판 현재 역할·공개 범위 | 실제 JWT에서 잔존 전역 직원/관리자 표시로 비공개 질문·상담·댓글 열람, 좋아요 및 운영 필드/게시 대상 변경 우회 재현. 누락/null 대상이 기존 강의 제한을 전체 공개로 바꾸는 입력 결함도 확인. `85bf77f9a`: 현재 학원 역할 적용, 명시적 배열과 양의 정수 ID 검증, 잘못된 요청의 본문/대상 보존. 학생·선택 자녀 질문 정상 수정→reload 포함 community/공개 자료 228건·세부 94건 통과(로컬 PostgreSQL 전용 2건 생략, POSIX process-group 1건은 Windows 제외 후 Linux CI로 확인) | PR609 `4c69ae255072929de5570072211aad9d5447f73d` [run37908819030](https://github.com/guswls3028-art/academy-backend/actions/runs/37908819030) 전체 성공. 최신 여섯 이미지 스캔, 격리 개발 Excel/PPT/R2, preprod DB 거부·120건 오류 0·CDN200/200/206·임시 인스턴스 종료, 공식 승인·migration·건강 기반 교체·런타임/manifest·잠금 해제 통과. 화면 PR722 `5930bb3c2` [run37912703522](https://github.com/guswls3028-art/academy-frontend/actions/runs/37912703522) attempt2에서 동일 산출물 실사용 27건·cleanup zero·운영 읽기·3개 도메인 배포 묶음 통과. **기록된 수정 동선 운영 확인, 전체 하위 동선 미완료** |
| D17 공개 자료 읽기·수정 버전 | 기존 게시자·파일/본문 읽기·미발행 가시성 회귀 후 통합 검사에서 수정 버전의 같은 시각 경계 발견. 서버 시각이 멈추거나 되돌아가도 잠근 게시물 버전을 반드시 증가시켜 과거 편집의 덮어쓰기와 동일 재시도의 버전 변경을 방지. 고정 시각 실패 재현 후 공개 자료·문의함 관련 36건·세부 15건 통과. 기존 timestamp API·콘텐츠·게시자 설정·파일 보존 | PR609 `4c69ae255072929de5570072211aad9d5447f73d` [run37908819030](https://github.com/guswls3028-art/academy-backend/actions/runs/37908819030) 전체 성공. 최신 여섯 이미지 스캔, 격리 개발 Excel/PPT/R2, preprod DB 거부·120건 오류 0·CDN200/200/206·임시 인스턴스 종료, 공식 승인·migration·건강 기반 교체·런타임/manifest·잠금 해제 통과. 화면 PR722 `5930bb3c2` [run37912703522](https://github.com/guswls3028-art/academy-frontend/actions/runs/37912703522) attempt2에서 동일 산출물 실사용 27건·cleanup zero·운영 읽기·3개 도메인 배포 묶음 통과. **기록된 수정 동선 운영 확인, 전체 하위 동선 미완료** |
| D18 알림톡 현재 역할·설정 표시 | 실제 JWT에서 전역 관리자 표시가 남은 강사/직원의 학원 전체 알림 OFF·자동발송 설정 진입, 일반 직원의 수동 사전검사 우회 재현(7 실패 사례). `a60c6ea4b`: 설정과 can_manage_messaging은 현재 owner/admin, 수동 발송은 owner/admin/teacher만 허용. 정상 대표/관리자 저장→재조회·강사 사전검사·권한 회수·tenant 격리 및 기존 messaging 340건·세부 52건 통과(로컬 PostgreSQL 전용 8건 생략), 정적·migration 검사 통과 | PR609 `4c69ae255072929de5570072211aad9d5447f73d` [run37908819030](https://github.com/guswls3028-art/academy-backend/actions/runs/37908819030) 전체 성공. 최신 여섯 이미지 스캔, 격리 개발 Excel/PPT/R2, preprod DB 거부·120건 오류 0·CDN200/200/206·임시 인스턴스 종료, 공식 승인·migration·건강 기반 교체·런타임/manifest·잠금 해제 통과. 화면 PR722 `5930bb3c2` [run37912703522](https://github.com/guswls3028-art/academy-frontend/actions/runs/37912703522) attempt2에서 동일 산출물 실사용 27건·cleanup zero·운영 읽기·3개 도메인 배포 묶음 통과. **기록된 수정 동선 운영 확인, 전체 하위 동선 미완료** |
| D21 지원 문의함 후속 댓글·집계 | 통합 검사에서 같은 시각의 플랫폼 답변/문의자 댓글 순서가 사라지는 문제와 문의자 댓글 없는 완료 글의 필터 누락을 발견. 작성 시각+ID로 마지막 댓글을 판정해 상세 상태·필터·전체 집계 일치. 동시각 답변→후속 문의→재답변 고정 재현과 관련 36건·세부 15건 통과, 기록/첨부 수정 없음 | PR609 `4c69ae255072929de5570072211aad9d5447f73d` [run37908819030](https://github.com/guswls3028-art/academy-backend/actions/runs/37908819030) 전체 성공. 최신 여섯 이미지 스캔, 격리 개발 Excel/PPT/R2, preprod DB 거부·120건 오류 0·CDN200/200/206·임시 인스턴스 종료, 공식 승인·migration·건강 기반 교체·런타임/manifest·잠금 해제 통과. 화면 PR722 `5930bb3c2` [run37912703522](https://github.com/guswls3028-art/academy-frontend/actions/runs/37912703522) attempt2에서 동일 산출물 실사용 27건·cleanup zero·운영 읽기·3개 도메인 배포 묶음 통과. **기록된 수정 동선 운영 확인, 전체 하위 동선 미완료** |
| D19 운영 업무 전체 집계 | 질문·상담 첫 100개와 제출 첫 200개만 세는 누락 재현. 게시 상태·삭제 학생·내부 메모·학원/역할 경계를 유지한 서버 전체 집계로 전환, 실제 JWT 회귀 6건과 기존 연계 61건 통과. 관리자 활성 운영 시험은 양식 제외 서버 전체 count 사용. | 서버 PR610/611와 화면 PR725 `61e92dbdb`, run37940243283의 전체 실사용 27건·cleanup zero·운영 읽기·3개 도메인 배포 묶음 통과. 관리자 전체 평가 선택과 제출 상세 페이지도 같은 후보에서 확인했다. **기록된 수정 동선 운영 확인, D19 전체 미완료** |
| D20 설정 권한·조회/저장·초안 | 전역 관리자 표시가 남은 교직원의 원장 편집 메뉴 노출, 학원/사업자 정보 조회 실패를 빈 양식으로 처리하는 경로를 실제 브라우저에서 재현. 현재 역할 적용, 최초 조회 복구 후 편집, 실패 시 입력·기존 분원 보존, 다른 섹션 저장 시 미리보기·라벨 초안 보존. 실제 처리 권한이 있는 admin의 홈페이지 상담 알림 복원. 소스 24건 통과 | PR724의 수정은 PR725 `61e92dbdb`로 승격했다. 분리된 owner fixture의 저장→reload→학생 홈 반영을 포함해 전체 실사용 27건·cleanup zero·운영 확인 통과. 버전 기반 동시 사용자 충돌과 나머지 개인 설정·가이드·테마 하위 동선은 별도 점검 대상 |
| D21 빌드 의존성·보안 예외 수명 | Dependabot #20의 개발용 source-map-js 1.2.1을 공식 1.2.2로 고정, 잠금파일의 해당 그래프만 갱신. Tailwind 실제 의존성에서 정상 map과 잘못된 offset 4종의 신속 거절 확인. 최신 여섯 이미지에서 사라진 GCC 두 예외는 삭제하고 High 상한을 3→1로 낮춤. 정책 회귀 161건 통과 | 서버 PR610 및 화면 PR725로 운영 반영했고 Dependabot #20도 fixed로 재조회했다. Cyrus exact identity·2026-10-10 UTC 종료 HOLD/기한 유지, upstream 해결 또는 도달 불가 보장으로 집계하지 않는다. **기록된 수정 범위 운영 확인, 플랫폼 전체 미완료** |
| 나머지 및 위 도메인의 미검증 하위 동선 | 구조 분류 완료, 이번 순차 점검 **미착수 또는 조사 단계**. 이전 릴리스나 테스트 존재를 현재 완료로 승격하지 않음 | 현재 코드/정책·실사용 흐름을 하위 동선별로 좁혀 점검 |

### 선행 검증과 복구 이력

D13 인벤토리는 기존 main에서 배열 JSON의 AttributeError,
참거짓/소수 ID의 정수 파일 선택, 0/음수 URL 만료를 재현했다. `dedbbb936`은 JSON
객체·문자열·양의 bigint ID·만료 범위를 검증하고 숫자 문자열/루트/fileId 별칭을
유지한다. API·학생/학부모·삭제 보상·성적표 회귀 114건·세부 105건, 보강한 입력
회귀 6건·세부 79건, 정적/DB 모델/경계 검사 통과. PR610에 통합해
run37923766151로 **운영 반영 완료**했다. 나머지 저장소 동선의 완료 증거로 확대하지 않는다.

D19–D21 통합 화면 빌드 76건이 통과했다. 후속 시각 점검에서 모바일 학원 연락처
말줄임을 발견해 줄바꿈을 수정하고 최종 빌드 설정 14건과 PC/390px 시각 재확인을
통과했다. D20은 기존 admin을 승격하지 않는
별도 일회용 owner fixture와 저장→reload→학생 홈 반영 실사용 검사를 추가했다.
scenario 56건·세부 51건(로컬 PostgreSQL 전용 3건 생략), SSM 경계 42건 통과.
이 로컬 증거 자체는 원장 화면의 격리 실사용 통과가 아니다. 뒤의 PR724 검증에서
해당 원장 설정 흐름은 통과했으나 전체 후보는 평가 복사 실패로 승격하지 않았다.
PR610 CI의 PostgreSQL 복구
검사에서 NOWAIT 테이블 잠금 충돌 1건이 발생하여 동일 코드의 이전 성공과 대조하고
실패 job을 1회 재실행하여 전체 PostgreSQL 회귀가 통과했다. 제품 잠금과 거부 조건은
변경하지 않았다. 추가 입력·owner fixture 통합 후보에는 새 전체 CI를 적용했다.

선행 frontend PR722 run37912703522 **attempt1은 격리 실사용 실패로 운영 승격되지 않았다**. 이후 attempt2 성공과 운영 확인은 위에 기록했다.
26건 통과·1건 실패·skip/flaky 0이며 긴 영상 재생 POST의 전송 시간 초과가 발생했다.
기존 교차 tenant 검증 데이터는 0으로 정리됐고, 주 QA tenant738은 cleanup_database의
OperationalError 이후 남은 17사용자·2영상을 별도 정확 대상 복구 검토로 정리했다.
run37912703522 attempt1·개발 인스턴스·불변 release/digest·생성 seal23885·행 수를
다시 확인한 후 SSM `f27d75b6-2fd7-48f6-bcab-dc5bcbd8529f`에서 기존 destroy 경로로
소유 합성 데이터만 제거했다. tenant/user/video 상태·R2·process/listener 0과 원래
생성 seal 보존을 재조회했다. 운영 자료·런타임·인증 capability 변경은 없다.
비식별 진단에서 gunicorn worker timeout 1건을 관찰했으나 최초 전송 시간 초과의
원인을 확정하지 않는다. 정리 0 확인 뒤 공식 동일 산출물 attempt2를 시작했으며,
검사·timeout·보호 조건·변경 요청 재전송 정책은 바꾸지 않았다. 운영은 재검증과
운영 확인을 끝낼 때까지 이전 화면을 유지했다.
위 표는 attempt2 최종 운영 결과로 갱신했으며 attempt1 실패 artifact를
성공 증거로 재사용하지 않는다. 소유 복구 증거는 작업 artifact의
`qa738-recovery-result.json`에 보존한다.

### 남은 범위와 판정 기준

발견성 후속 소유 `domain-navigation-usability-20261010-01a10bcb`는 frontend
`61e92dbdb`에서 시작했다. 현재 코드 진단으로 모바일 기능 모음이 `PC 버전`이라는
이름의 지원 메뉴에 있고, 현재 화면에 따라 같은 메뉴가 기능 모음을 거치지 않고
PC 상세로 이동하며, 빠른 검색은 주로 상위 메뉴만 포함하는 것을 확인했다.
기능 모음의 명확한 진입·세부 업무 검색·역할별 잘못된 목적지를 수정하고 소스 대상
합성 API 28건과 추가 복귀·격리 3건, 390/1100/1366px 표시를 검증했다. 상담 업무 검색→
메모 저장→reload가 포함된다. [PR726](https://github.com/guswls3028-art/academy-frontend/pull/726)
`2b6a8cd2c` 전체 타입/린트/빌드/guard CI와 후보 preview, 전체 브라우저 CI
run37961409345가 통과했다. main `81ebe997d`로 정상 병합하고 run37966598131에서
동일 산출물 개발 실사용 27건·양쪽 cleanup zero·운영 읽기 검증·세 운영 도메인
배포 묶음까지 통과했다. **확인한 교직원 기능 발견성 동선은 운영 반영 완료**다.
학생앱은 하단 5개 항목 외에 홈의 성적·시험·과제 바로가기와 펼침 메뉴를 함께 제공한다.
최종 로컬 빌드에서 학생/학부모의 할 일→학습 기록·성적 실패 복구·게시판/자료실 연결
5건과 PC/390px 표시를 확인했다. 홈의 8개 바로가기는 다른 카드 아래 있고 보관함이
메뉴에서는 `내 인벤토리`로 표시된다. 명칭·위치의 개선 후보이며 전체 발견성 완료로
확대하지 않는다. 교직원 최종 로컬 빌드 업무 6건과 전체 guard도 추가 통과했다.

기존 후속 큐였던 D08 전체 평가 선택, D17 상담 페이지/미열람 합계, D19 제출 상세
페이지, D20 원장 설정은 위 PR724/725의 실제 실행·승격 결과로 판정한다. 코드를
고쳤거나 helper를 작성했다는 이유만으로 통과 처리하지 않는다.

D01의 복수 시트/헤더·필드별 미리보기 판정·나머지 계정 그래프, D13의 나머지 형식/소유권/경합,
D17의 다양한 HWP/PDF 원본·모바일 읽기·SEO 및 같은 메모의 동시 편집,
D20의 동시 사용자 설정 저장 충돌은 별도 하위 동선으로 남긴다. D19 완료·실패의
오래된 전체 이력은 현재 24시간 접수 범위 정책 밖이므로 명시적인 정책 검토 대상으로
구별한다. 위 순서표의 그 밖의 미검증 하위 동선도 계속 추적한다.

source-map-js Dependabot #20은 2026-10-09T12:17:56Z `fixed`, `dismissed_at=null`로
API 재조회했다. 새 의존성이 포함된 화면 운영 승격과 구별하며, 이 개발 빌드 의존성
문제를 운영 브라우저 노출로 추정하지 않는다. Cyrus 예외 기한/HOLD와 Toss/card
HOLD는 이 수정으로 해제하지 않는다.

작업 카드에는 재현 조건, 영향 역할/tenant, 심각도, 원인, 수정 파일, 파생 영향,
정상·실패·복구 검사, PR/커밋/CI, 실사용 산출물과 정리 결과, 운영 SHA/시각을 남긴다.
정확한 tenant/object 승인 없이 사용자 데이터를 삭제하거나 운영에서 합성 변경을
실행하지 않는다. 미해결 항목은 이유·다음 행동을 보존하고 전체 무결함을 보장하지 않는다.

### 2026-10-10 두 번째 전 도메인 점검 후보

소유 `domain-quality-round2-20261010-01a10bcb`. 사용자 요청에 따라 D01 후속을
이어 인증·금액·학습 운영·콘텐츠/소통·설정/플랫폼 순으로 재점검한다. 시작 기준은
backend `3aca81de1b9d0d1e704e761333d766d4107ab327`, frontend
`b38f6a0f80fa0684a8bcec4d8b8bb3bb33290ad8`이다. 아래는 재현·수정한 후보이며
CI와 격리 실사용 및 운영 readback 전에는 운영 완료로 판정하지 않는다.

| 범위 | 재현 및 변경 | 정상·실패·복구 확인 |
|---|---|---|
| D00 학생 지원 열람 | 여러 학원에 소속된 교직원의 기본 학원이 다르면 정당한 지원 세션도 401이던 조건 제거. 요청 학원의 현재 활성 소속·세션·학생·만료 경계를 유지 | 다른 기본 학원으로 지원 열람 성공, 요청 학원 소속 해제/복구, 세션 종료 후 차단 |
| D02 근무·환급 | 오래된 근무시간으로 휴게 변경을 검증해 급여가 0이 되는 경합 방지. 환급 승인/반려 감사 저장 실패는 같은 거래에서 롤백 | 최신 시간 기준 유효 수정·금액 보존, 잘못된 휴게 거부, 삭제된 행 404, 감사 실패 503→재시도·감사 1건. 실제 PostgreSQL 경합 검사 추가 |
| D03 수강료 | 기간 변경이 다른 담당자의 할인 변경을 되돌리는 부분 저장 경합 방지. 학생 잠금 순서와 청구 생성 경계를 유지 | 할인+기간 보존→청구서 실제 할인 금액, 오래된 기간의 역전 거부→정정 성공. PostgreSQL 동시 수정 추가 |
| D05 강의·차시 | 오래된 강의 기간으로 저장해 500이 되던 경합 및 존재하지 않는 달력 날짜의 500 수정 | 최신 기간 검증→400→정정 성공, 윤년 정상 조회, 차시 일괄 생성에서 잘못된 날짜 전부 무변경→정정 성공 |
| D09/D10 교재 채점 | 실수/불리언 ID가 다른 학생·문항으로 강제 변환되고 숫자 정오 값이 복습 판정을 깨뜨리는 경로 거부 | 혼합 일괄 요청 무변경·기존 점수/메타 보존, 올바른 ID/불리언으로 저장→reload |
| D20 학원 설정 | 표시 이름 저장이 동시에 변경된 구독 해지·브랜드를 덮어쓰는 경합 방지. 공개 조회를 깨뜨리는 배열·문자열 설정 저장 거부 | 최신 해지·브랜드 보존, 저장 응답과 reload 일치, 잘못된 JSON 400→객체로 재시도. 같은 JSON 필드의 버전 충돌 표시는 별도 잔여 범위 |
| D21 QA 발견 범위 | pytest가 빠뜨리던 clinic/problem_studio/problem_solver/ppt의 `tests.py` 네 모듈을 기본 수집에 포함 | 네 모듈과 실제 클리닉 취소 outbox 회귀 162건 통과. 기존 두 클리닉 테스트는 현재 학부모+학생 durable outbox 계약에 맞춤; 제품 발송 정책은 그대로 유지 |

관련 검증: 인증·직원·수강료 192건, 평가·과제·성적·제출/진도 955건, 설정·구독·결제
259건이 로컬 SQLite에서 통과했다. 중복된 집합을 전 도메인 고유 검사 수로 합산하지
않으며 PostgreSQL 전용 skip은 통과로 세지 않는다. 학습/청구 묶음의 오래된 클리닉
기대값 실패는 위 162건 재검증으로 해소했다. 콘텐츠/운영 확대 검사는 로컬 환경의
지연·실패를 포함해 별도 진단 중이며 전체 성공으로 기록하지 않는다. 최초 화면 검사의
로컬 연결/개발 모듈 로딩 실패도 운영 결함으로 단정하지 않는다. 소스·정책·화면·실사용의
증거 수준을 구분하고 필수 전체 CI 및 정확한 배포 후보의 실사용 판정을 이어간다.
