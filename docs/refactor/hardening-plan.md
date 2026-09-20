# 안정화·사용 편의성 실행 계획

**상태:** active — 1차 운영 검증 뒤 사용자가 안정화 완료까지 계속 진행하도록 지시했다. 새 주관식 자기 점유 민원, 자정 이후 클리닉과 교직원 직접 등록, 기존 업무별 미검증 및 배포 선행 조건을 §8에서 처리한다. 전체 안정화 완료가 아니다.
**갱신 기준:** 2026-09-20 KST. 현재 운영 SHA/run은 §2, 작업 출발점은 §7.
**범위:** backend, frontend, 기존 사용자 업무, 관련 검증·운영·문서.
**요청:** 장기 안정성과 사용자 편의성을 위해 필요한 리팩토링까지 수행하고,
작업자가 바뀌어도 근거와 다음 작업을 이어갈 수 있게 기록한다.

이 파일이 현재 실행 순서와 인수인계의 소유 문서다. 제품 정책은 각 도메인 문서,
배포 절차는 운영 문서, 완료된 증거는 PR·CI·release와 `reports/`가 소유한다.
새로운 병렬 계획이나 에이전트 메모 체계를 만들지 않는다.
이전 7월 계획의 수치·실행 결과는 [갱신 전 기록](https://github.com/guswls3028-art/academy-backend/blob/9ff14e4d4d02d43b1daca0c26052c920bdff8fba/docs/refactor/hardening-plan.md)이며 현재 완료 판단에 재사용하지 않는다.

## 1. 완료 기준과 작업 경계

- 사용자가 진입점을 찾고 정상 동작을 끝내며, 저장 결과가 새로고침과 다른 역할의 화면에도 반영된다.
- 실패·대기·빈 결과를 구분한다. 입력 보존, 중복 실행 방지, 재시도와 안전한 이탈을 확인한다.
- PC와390px에서 주요 동작, 긴 한국어, 키보드·초점, 터치 영역과 상태 전환을 확인한다.
  오류 안내와 버튼은 다른 레이어에 가리지 않아야 한다. DOM visibility만으로 화면 검토를 대체하지 않는다.
- 원점수·수동 채점·사용자 작성 자료, tenant/역할 경계와 기존 호환성을 보존한다.
- 실제 원인을 재현하는 회귀 검사와 공식 실행 결과를 연결한다. 검사 파일 존재·개수·skip은 성공 증거가 아니다.
- 반복되는 중복 정책·변환·상태 관리가 확인되면 소유 경계에서 리팩토링한다. 성공 조건과 복구 경로를 먼저 정한다.
- 각 변경은 현재 기능 문서, 집중 검사, 필요한 CI 및 배포·실사용 증거까지 완료한다.

상세 완료 조건은 [변경 위험·실사용 계약](../operations/change-risk-and-release-bundle.md)과
각 저장소 `AGENTS.md`를 따른다. 정상 동작을 감추거나 버튼을 막는 것은 해결이 아니다.
기존 유효 HOLD는 정확한 대상과 해제 조건을 그대로 유지한다.

## 2. 확인된 출발점

| 구분 | 확인된 근거 | 아직 보장하지 않는 범위 |
|---|---|---|
| Backend 운영 | `f2ded2cafa11f2bc3b24f1fd3dcecf8e16a4e264`, [release35459301485](https://github.com/guswls3028-art/academy-backend/actions/runs/35459301485), 독립 canary30 PASS | 모든 기능·데이터 조합의 무결함 |
| Frontend 운영 | `d33c4c645868b8924719507524e2f03ea8d02d96`, [release35474667732](https://github.com/guswls3028-art/academy-frontend/actions/runs/35474667732), 격리 실사용21 PASS/0 FAIL/0 SKIP 및 운영 read-only PASS | 전체 업무를 망라하는 커버리지 |
| 버전 일치 | 두 release와 manifest, godmin.kr/hakwonplus.com 버전 및 lock 해제 확인 | 이후 배포에 대한 자동 보증 |
| 클리닉 | godmin700행 서버 내부10,960→3,644ms, SQL4,037→461회. 단일 전후 측정 | 브라우저 p95, 피크 동시성, 모든 학원 데이터 |
| 시험·숙제 | 재채점/수동 점수 보존, 학생 업로드→실제 조교의 자동 발견·PNG 미리보기→reload·PC/390px 통과 | 직원 채점 UI, 모든 파일·장애·재시도 조합 |
| 영상 | PC/mobile 각690초 재생·갱신·진도 복원, 검사 오류0 | 실제 사용자의 모든 네트워크 버퍼링 |
| 도구 | 생성 API 타입, schema check, 경계 검사, 동일 산출물 개발 canary 존재 | 추가한 회귀 검사의 올바른 실행 경로 포함 |

운영 자료는 읽기 전용 집계로만 확인했다. 실사용 mutation은
[격리 개발 런타임](../operations/persistent-development-runtime.md)의 합성 `qa-*`
자료로 수행하고 tenant/user/storage/process 잔여0을 확인한다.
과거 Tenant1 운영 쓰기 QA 기록은 새 테스트 실행 허가나 현재 절차가 아니다.

## 3. 실행 순서

| 단계 | 상태 | 산출물·종료 조건 | 재개 지점 |
|---|---|---|---|
| S0 정리·계획 | 정리·인계 정돈 완료 / 최종 문서·QA CI 및 병합 영수증은 PR480 | 정리 대상/보존 이유/개수·결과, 현재 문서 경로 정정, required CI, 커밋·PR 인계 | §4·§7, 양쪽 docs 인덱스 |
| S1 업무별 결함·증거 정리 | 5업무 공식 검사 대조, 첫 배치 실사용21 PASS | 아래5업무의 실행 테스트·미검증 조건·재현 결과 대조, 기존 결함 후보 현재성 확인 | §5 및 failure-transparency-stabilization.md |
| S2 수리·필요 리팩토링 | 영상 목록 오류/빈 상태 수정, 클리닉 공통 mutation 통합·모바일 탭 압축 수정 완료 | P0/P1부터 정상 업무 단위로 실패 재현→수리→회귀/소비자 확인 | S1에서 재현된 최우선 항목 |
| S3 규모·복구·편의성 | 영상14/클리닉14/숙제2 focused PASS. 실제 조교·교사 목록·운영 콘솔 포함 공식21 PASS | 혼합 합성 데이터, 중복/재시도/부분 실패, PC/390px, 응답시간·조회량, 정상 빈 결과 구분 | 각 변경의 도메인 문서·검사 |
| S4 배포·재발 확인 | 첫 실패를 수리한 후21 PASS·cleanup0·운영 승격·버전/bundle readback PASS | exact SHA/동일 산출물, required gate, 운영 버전·업무 readback, cleanup0, 한계 기록 | §7의 실패·복구와 기존 release-bundle 절차 |

S1~S4는 기능별 작은 변경 단위로 반복한다. 테스트 변경은 어느 공식 job에서
어떤 업무 조건을 실제 실행했는지 확인한다. 재현된 P0/P1을 미해결로 둔 채
다음 업무로 넘어가지 않는다. 범위가 바뀌면 계획과 PR 설명도 최종 범위로 갱신한다.

## 4. S0 정리 결정

| 대상 | 결정·현재 상태 | 안전/복구 기준 |
|---|---|---|
| main 커밋·release/report 이력 | 보존 | 공유 이력 재작성 금지. 동일 audit snapshot도 시점별 증거일 수 있음 |
| 현재 계획·인덱스 | 이 계획 재사용, 낡은 지시·중복 운영 설명 정리 및 독립 검토 완료 | 과거 판단은 시점·근거 표시 후 현재 owner 연결 |
| 로컬 브랜치 | 완전 병합·등록 worktree 미사용 `codex/*`28개 로컬 참조 정리 완료(backend5/frontend23) | exact ref/SHA 기록, 실행 직전 병합·비사용 재확인, expected-SHA 삭제·사후 부재 확인 |
| 등록 작업공간·미반영 변경 | 외부 소유 보존 | Git 메타데이터257개 중 dirty24. 나이만으로 삭제하지 않음 |
| 임시 의존성·캐시 | 생성 캐시2개·8파일·962bytes 삭제가 실행 전 정책 차단됨. 삭제0/보존 확인 | 사용자 자료·산출물·비밀·복구 기록 제외, 삭제/보관 구분 |
| 이전 Windows 잔여 파일 | 755,322,528 logical bytes/40,861파일 보관. 물리 회수량 미주장 | 이전 삭제 거부 기록 보존. 외부 junction target을 따라 삭제하지 않음 |

세부 근거는 `C:\academy\_artifacts\stability-foundation-0920`에 저장한다.
완료 결과와28개 exact ref/SHA는 [정리 보고서](../reports/history/20260920-stability-foundation-cleanup.md)에 기록했다.
`.secrets`, `materials`, 사용자 산출물, `dnfm`, `dnfm-group`은 정리 대상이 아니다.

## 5. 핵심 업무와 검증 기준

| 업무 | 사용자 성공 경로 | 현재 증거·우선 빈틈 | 소유 문서/검사 진입점 |
|---|---|---|---|
| 학생 등록·계정 | 입력→등록/승인→로그인→교직원/학생/보호자 조회 | 공식 account 검사는 미리 생성한 계정의 복구 요청·기존 로그인·프로필/보호자 조회다. 신규 가입→승인 UI 검증과 혼동하지 않으며 중복·저장 실패 포함 범위를 추가 확인 | [student-creation](../domain/student-creation.md), [student-core](../domain/student-core.md), frontend `student-parent-account-realuse.spec.ts` |
| 시험·재채점 | 답안/배점 저장→채점→성적·클리닉·학생 화면→reload | OMR 재채점/수동 점수 통과. 미제출·재응시·부분 실패·재시도 조합 확인 | [exam-grading](../domain/exam-grading.md), frontend `omr-review-realuse.spec.ts` |
| 숙제 | 제출→파일 저장→교직원 조회→명시적 처리→학생 결과 | 실제 staff 역할·첫 안내 확인·열린 상세의 새 제출 발견·PNG 미리보기·reload·PC/390px 통과. 채점은 관리자 API이므로 직원 처리 UI와 다른 파일/실패 조합은 별도 검증 필요 | [homework-grading](../domain/homework-grading.md), frontend `student-parent-homework-realuse.spec.ts` |
| 클리닉 | 조회→통과/되돌리기→학생 상태→예약/취소→reload | 공식 검사는 교직원 UI 통과, API 되돌리기·학생 projection, 목록 reload를 포함한다. 되돌리기/학생 결과 전체 UI 여정과 혼동하지 않으며 700행 OMR/수동/재응시 조합·시간 기준 확인 | [clinic-booking](../domain/clinic-booking.md), `test_clinic_target_bulk_reads.py`, frontend `student-clinic-required-cancel-realuse.spec.ts` |
| 영상 | 준비/업로드→처리→학생 재생→갱신·진도·복원 | 장시간 재생 및 실제 목록/reload 통과. 목록 오류/빈 상태·재시도는 route-mock 검증. 실제 네트워크별 버퍼링은 별도 관측 | [실패 은폐 후보](failure-transparency-stabilization.md), frontend `video-playback-renewal.realuse.spec.ts` |

frontend 공식 목록은 `playwright.development-release.config.ts`와
`scripts/run-development-release-canary.mjs`의 실행 계약에서 확인한다.
표에 이름이 있다고 실행됐다고 판정하지 않는다. 합성 fixture는 운영 개인정보를 복사하지 않는다.
전체 민원율·재발률·응답시간 p95는 아직 기준선 미수집이며 수치를 추정해서 쓰지 않는다.

### 공식 검사와 다음 최소 검증의 연결

2026-09-20 처음 코드 대조에서 확인한 공백과 이번 배치의 실제 실행 결과를 구분한다.
검사 이름·다른 도메인 통과로 공백을 대체하지 않는다. 수리 source는 frontend PR #552·553이며
실제 실행 SHA/run은 §7에 기록한다.

| 우선순위 | 이미 검증하는 조건 | 다음 최소 검증 및 중단 기준 |
|---|---|---|
| 1. 숙제 역할·발견성 | `student-parent-homework-realuse.spec.ts`의 실제 `staff` 계정 안내 확인→상세 먼저 열기→학생 PNG 제출→자동 발견·디코딩·reload, API92점 채점 뒤 결과·재로그인 | 발견성·역할 검증은 이번 배치 통과. 남은 직원 처리 UI→학생 결과와 다른 파일/실패 조합을 확인하며 권한 거부·상태 불일치 재현 시 우선 수리 |
| 2. 가입·승인 | `createQaFamily`는 관리자 API로 사전 생성. account spec은 복구 요청과 기존 비밀번호 로그인, 관리자 API의 보호자 임시 비밀번호, 프로필 편집 취소를 검사 | 격리 tenant의 가입 UI→직원 승인 UI→첫 로그인·보호자 연결. 중복 신청 또는 저장 실패→입력 보존/재시도 한 경계를 추가하고 다른 tenant 승인·객체 접근 거부 확인 |
| 3. 재채점 | `omr-review-realuse.spec.ts`의 전체 재채점 UI→수동 서술형 점수 유지→390px reload→학생/보호자 성적·분석 반영 | 정답/배점 변경 후 기대 점수·만점·클리닉의 일치, 일부 실패와 재시도. 단순 같은 답안 재실행의 성공과 구분 |
| 4. 클리닉 | `student-clinic-required-cancel-realuse.spec.ts`의 운영 콘솔390px 통과→PC reload·항목 소멸, 원점수20 유지, API 되돌리기·항목 복원, 학생 projection·예약/취소 | 실제 되돌리기·학생 결과 UI, 혼합700행에서 요청/브라우저 시간과 재시도·중복 클릭. 단일 서버 측정으로 브라우저 p95를 주장하지 않음 |
| 5. 영상 | PC/390px690초 재생·갱신·진도 복원, 교사 목록 실서버 조회/reload. 목록 실패/재시도는 공식 route-mock에서 통과 | 네트워크별 버퍼링과 구버전 활성 화면 연속성은 별도 관측·재현 |

공통 `qa-*` 요청 경계·실발송 금지·cleanup0은 유지한다. `ymath-qa-teacher` fixture는
이름과 달리 membership이 `admin`이다(`setup_ymath_realuse_scenario.py`); 교사 화면에서
그 계정을 사용한 성공은 실제 조교 권한 성공이 아니다. 기존 테스트의 결과·역할을 바꿔
서술하지 말고 필요한 역할 fixture와 거부 검사를 추가한다.

이번 배치에서 확인한 재발 경로는 화면별로 갈라진 동일 통과 처리와 실제 조교 대신
관리자로 수행한 역할 검증이다. 기존 공통 mutation 재사용과 `/core/me/` 역할 readback을
포함한 조교 시나리오로 보강·검증했다. 시각 검토는 DOM visibility, 실제 포인터, 알림의 표시·
퇴장 애니메이션과 촬영 시점을 구분한다. 캡처 중 잘린 메시지를 현재 제품의 레이어
결함으로 단정하지 않는다. 이를 프로젝트의 모든 버그 원인으로 일반화하지 않는다.

## 6. 미검증 후보와 보존할 결정

- 첫 공개영상 GET provisioning 후보는 현재 backend에서 GET 조회/POST 준비로 이미 수리됐다.
  `test_public_session_safe_method.py`와 frontend `public-video-preparation.mock.spec.ts`가 존재한다.
  코드·기존 회귀의 확인이며 새 실사용 실행은 아니다. [현재 계약](../domain/public-video-session.md)을 따른다.
- 교사 영상 목록 API 실패가 실제 빈 목록으로 표시되는 결함은 PR552에서 수리·병합됐다.
  수정 전 최초503 실패를 재현했고, 후보 수정의 정상0건/최초실패→재시도/기존 목록 갱신
  실패·reload14개 회귀가 PC/390px에서 통과했다. 운영 완료는 §7의 exact release로 판정한다.
- 클리닉 전체 미통과 화면과 운영 drawer의 수동 통과가 서로 다른 저장 후 처리 코드를
  사용했다. 운영 화면에서는 POST200 뒤에도 느린 재조회가 끝나기까지 처리 항목이 남는
  결함을 재현했다. 기존 공통 hook으로 확정 응답의 캐시 반영과 background 재조회를
  통합했고, 함께 발견한390px 탭 압축·클릭 가림도 수정·검증했다.
- [실패 은폐 후보](failure-transparency-stabilization.md)의 설정 저장 실패·이동, 활성 사용자
  복구는 최초 발견과 현재 재현을 구분한다. CI만으로 runtime 완료로 바꾸지 않는다.
- 과거 영상/OMR의 만료 `ScoreEditDraft` 삭제 차단·403 표시, playback POST 응답 단절 및
  업로드 미도달은 역사 관찰이다. 최신 재현·서버 증거 전에는 현재 장애/해결로 단정하지 않는다.
- 2026-06-07 학생 launch GO/감사 결과는 역사 자료다. 최종/임시 점수, 다자녀,
  중복 제출의 당시 미완료 조건은 현재 정책·검사에서 다시 판정한다.
- Dev Alerts Cron 설정 누락은 별도 미해결 운영 항목이다. 서비스 canary 성공과 구분한다.
  새 알림 수신처/외부 발송을 임의로 설정하지 않는다.
- 2026-09-20 UTC부터 기존 ECR critical/high 위험 수락의 `2026-09-19` 만료가 적용된다.
  **다음 backend 이미지 승격의 선행 조건:** [컨테이너 보안](../operations/container-image-security.md)의
  exact digest·취약점·패키지·도달성 근거로 수정/수락을 재검토하고 현재 날짜 게이트를 통과한다.
  이번 테스트 수리는 수락 기간을 연장하지 않는다. 기존 성공 배포 영수증을 다음 후보의
  보안 승인으로 재사용하지 않는다. 운영 장애 발생을 의미하는 판정은 아니다.
- 유효 HOLD는 [배포 연속성](../operations/deployment-modes.md)과 frontend
  `docs/DEPLOYMENT-OPERATIONS.md`의 현재 scope·해제 조건을 보존한다.

## 7. 인수인계와 재개

현재 배치는 `stability-foundation-0920` 소유 worktree에서 S0와 첫 안정화 수리를 완료했다.
다른 작업자는 아래 상태와 PR/CI를 확인하고 Git branch/worktree로 소유권을 확인한다.
canonical의 기존 dirty 변경을 가져오거나 덮어쓰지 않는다.
후속 작업은 완료된 배치의 작업공간을 재사용하지 않고 현재 `origin/main`에서 새 소유 세션으로 시작한다.

| 항목 | 현재 상태 |
|---|---|
| 목표·허용 범위 | 장기 안정성·편의성, 필요한 리팩토링. 기존 자료·tenant/권한 보호 |
| base | backend `9ff14e4d4`, frontend `a705cb81a` |
| 변경/판단 | 기존 계획·문서 정돈, 로컬 브랜치28개 정리. 교사 목록 오류 은폐, 클리닉 운영 콘솔의 느린 재조회 종속과 모바일 탭 압축 재현·수리 |
| PR/커밋 | [backend480](https://github.com/guswls3028-art/academy-backend/pull/480)(문서·QA), [frontend552](https://github.com/guswls3028-art/academy-frontend/pull/552) 병합 `3cb33cee37f2426c630ec0c2d4fff79bc99f3f2a`; 후속 [frontend553](https://github.com/guswls3028-art/academy-frontend/pull/553) 후보 `62ebd07cb56bfcf5bb565d37cd4142c5862474c7`→병합·운영 `d33c4c645868b8924719507524e2f03ea8d02d96` |
| 로컬 검증 | 영상14/클리닉14/숙제2 PASS, 알림 노출2 PASS 및 PC/390px 시각 검토, typecheck/lint/guards/budget/build PASS, 개발 canary 계약120 PASS. backend lifecycle/change-risk/release-bundle 계약3개 PASS. 독립 리뷰 완료 |
| 공식 검증 | PR552 quality35468854237·E2E35468854197 PASS(Chromium695, iPhone36 PASS; Chromium 전용1개는 WebKit에서 제외). main35471220443의 격리20 PASS/1 FAIL은 승격 차단. 후속 quality35472858971·E2E35472859043 PASS, main35474667732 전체 SUCCESS. backend 중간 문서ebcd82dfd CI35472920726 PASS; 후속35476371096의 무작위 암호문 검사 오탐은 아래 원인·수정 기록 참고. 최종 문서·QA exact head CI·병합 기록은 PR480에서 확인 |
| 배포 | §2의 정확한 backend/frontend pair로 bundle PASS, godmin.kr/hakwonplus.com 버전 일치·manifest·lock 해제 확인. 격리21 PASS·cleanup0 확인 후 exact run의 production 환경을 공식 API로 승인했고 운영 read-only도 통과. 문서·QA 검사만 변경한 backend는 제품 배포 대상이 아님 |
| 다음 한 작업 | 신규 가입 UI→직원 승인 UI→첫 로그인·보호자 연결을 격리 tenant에서 재현하고, 자동승인 저장 실패의 안내·입력 보존·재시도 경계를 확인한다. 이후 §5의 직원 처리 UI·정답/배점 변경 재채점·혼합 클리닉 데이터 검증을 이어간다 |
| 주의 | 외부 dirty 변경 보호, Windows 삭제 거부, 역사 후보와 현재 장애 구분 |

각 배치 종료 시 exact commit/PR·실행 결과·운영 반영 여부·남은 재현·다음 한 작업을
갱신한다. 같은 입력의 통과 검사는 재사용하고 기능 정책은 도메인 문서에 반영한다.
required CI와 executable 변경의 개발/운영 gate는 유지한다.

첫 격리 실행 [35471220443](https://github.com/guswls3028-art/academy-frontend/actions/runs/35471220443)은
실제 조교 역할, 학생 제출과 열린 상세의 파일 자동 발견까지 통과한 뒤 미리보기 클릭에서
멈췄다. 신규 조교의 `first_login_guide_required=true` 안내가 본문 위에 있었는데 seeded-auth
테스트가 확인 단계를 누락했다. 동일 조건의 focused 재현에서 버튼은 visible/enabled였고
안내 overlay가 포인터를 가로챘다. 실제 안내 확인 버튼→`/core/me/` 상태 false→미리보기→
reload 검증으로 수정했으며 숙제 mock2 PASS, PC/390px 시각 검토·lint·guards·독립 리뷰를
통과했다. 제품 권한·모달 정책을 바꾸거나 강제 클릭으로 우회하지 않았다. 후속 PR553을
반영한 [35474667732](https://github.com/guswls3028-art/academy-frontend/actions/runs/35474667732)의
격리 재실행은21 PASS/0 FAIL/0 SKIP다. 조교 미리보기 이후 reload·PC/390px와 학생/보호자
후속 결과까지 완료했고, 양쪽 합성 tenant/user/storage/process/listener 잔여0을 확인했다.
첫 실패의 정리 증거와 수정 전 재현도 보존한다.

backend [35476371096](https://github.com/guswls3028-art/academy-backend/actions/runs/35476371096)은
PostgreSQL 회귀5398 PASS/1 FAIL/5 SKIP였다. Excel 자격 증명의 실제 Fernet 암호문에
네 자리 합성 PIN `0000`이 우연히 포함되자 평문 유출로 오인한 테스트가 실패했다.
같은 단문 부분 문자열 검사를 쓰던 worker 저장 검사까지 두 곳을 수정했다. 저장 결과와
envelope의 정확한 키·암호문 형식, 기본 공개 응답의 자격 증명 제외를 검사하고 실제
500개 복호화·만료와 저장소 반환·캐시 비공개 검증은 유지한다. 제품 암호화 구현이나
난수 생성기를 바꾸지 않았다. 최종 required CI는 PR480의 수정된 exact head에서 확인한다.

후속 [35477228432](https://github.com/guswls3028-art/academy-backend/actions/runs/35477228432)의
Django 검사는5226 PASS/3 FAIL/147 SKIP였다. UTC 날짜 변경 후 두 역사 baseline 테스트가
현재 날짜로 만료된 수락을 읽어 identity 대체·예산 축소 검증에 도달하지 못했다.
해당 두 호출에 다른 역사 fixture 검사와 같은 `2026-09-12`를 명시했다.
운영 게이트의 UTC 현재 날짜 판정과9월19일 허용/20일 거부 회귀 검사는 그대로 유지한다.
실제 수락 만료는 위의 다음 backend 이미지 승격 선행 조건으로 남는다.
또한 현재 배포 workflow의 `^apps/` 분류는 테스트도 포함하므로, 이 문서·QA만의 병합에서
발생한 exact 배포 run은 운영 변경 전에 취소하고 PR480/실행 영수증에 결과를 기록한다.

격리 teacher 목록은 관리자 계정의 교사 화면이다. YouTube 썸네일과 worker가 생성하지 않은 long-video fixture
썸네일은 로컬 이미지 대역이며 목록 응답은 실제 API다. 숙제 PNG 미리보기는 실제
development storage 객체를 검증한다. 그림 대역을 CDN/처리 성공으로, API 채점을
조교 채점 UI 성공으로 바꿔 서술하지 않는다.

## 8. 안정화 계속 실행 — 2026-09-20

사용자는 1차 종료가 아니라 남은 안정화까지 계속 진행하도록 명시했다.
현재 소유 세션은 `stability-completion-0920`이며 backend base `efd2d2251`,
frontend base `d33c4c6458`이다. 이전 작업공간은 닫혔으며 canonical의 외부 변경은 보존한다.
실행 영수증은 `C:\academy\_artifacts\stability-completion-0920`에 남긴다.

| 순서 | 현재 작업·근거 | 완료 조건 |
|---|---|---|
| 1 | 주관식 입력 중 본인 이름의 점유로 수정 불가. 문서마다 client ID가 바뀌며 이전 빈 점유를 종료하지 않는 경로 확인; 특정 운영 시험/차시는 추가 확인 중 | 실제 staff의 주관식 입력→새로고침/다른 화면→안전한 점유 복구→저장→재로그인/학생 결과. 다른 사용자·다른 탭의 미저장 값 보호, 실패/재시도 및 PC/390px |
| 2 | 자정 이후 클리닉과 교직원 직접 학생 등록. 현재 00:00 종료만 지원하고 00:30 이후는 거부하는 정책/제약 확인 | 23시 시작→다음 날 종료 개설·표시→교직원 직접 등록/학생 신청→각 역할 재조회→취소. 경계·정원·중복·날짜 필터·리마인더의 동일 구간 계산. 기존 데이터와 reader-first 호환 배포 |
| 3 | 실제 만료된 ECR 위험 수락과 다음 backend 이미지 배포 | 최소 패키지 수리와 현재 vendor/scan 증거, exact immutable candidate의 보안 gate. 임의 기간 연장 없이 owning 보안 절차 준수 |
| 4 | §5의 가입/승인·직원 숙제 처리·정답/배점 변경 재채점·혼합 클리닉·실패 복구 | 정상 CTA→실제 저장→reload→소비 역할까지 공식 격리 검사로 연결. 재현된 결함은 수리/반영/재검증 후 닫음 |
| 5 | 배포 일관성·오류 은폐/재시도·활성 작업 연속성·성능 관측 | 해당 실행 경계의 실패 재현과 복구, 양 저장소 required CI, 동일 산출물 개발/배포·runtime readback, QA cleanup0. 검증되지 않은 p95/민원율은 수치 주장 금지 |

각 행은 코드나 테스트 작성만으로 완료하지 않는다. 실제 실행 결과와 남은 조건을 이 절에
갱신하고 필요한 다음 변경까지 이어간다. 증거가 없는 전체 무결함을 주장하지 않는다.

첫 backend 후보는 빈 본인 점유의 명시적 재개와 만료 보안 예외의 패키지 교체를 포함한다.
점수 API는 기존 미저장 값·타인 점유를 보호하며26 tests/2 subtests PASS,
보안 정책·이미지 구조 검사는108 PASS다. 페이지 종료는 exact client의 빈 점유만
조건부 해제하며 저장 요청이 진행 중이면 프런트가 종료 해제를 생략한다.
기존 예외5건은 Debian stable 수정본으로 교체하고 Critical/High 허용을0으로 낮춘다.
현재 이는 후보 조건이고 공식 native 이미지 CI·ECR scan·동일 산출물 실사용·운영
readback은 아직 필요하다. 클리닉 자정 이후 지원은 별도 reader-first 변경으로 이어간다.

Backend PR[481](https://github.com/guswls3028-art/academy-backend/pull/481)의 exact
`669709171ae21229c72b2588a0786e6503e0f2ba`는 app/PG/static/native ARM 이미지
CI[35499929254](https://github.com/guswls3028-art/academy-backend/actions/runs/35499929254)를
통과했다. main `6c566cf7fa357fc5ddee71c7175c270cde08cab8`에 병합했고 공식 배포
[35501517908](https://github.com/guswls3028-art/academy-backend/actions/runs/35501517908)가
진행 중이다. exact run의 production review는 eligible readback 후 공식 API로
승인했으며 pending 해소와 보호 job 시작을 확인했다. 아직 운영 완료 증거는 아니다.
주관식 API 독립 검토에서 추가 결함은 없었다.
Frontend 실제 native 종료 검사는 라우트 mock이 fetch keepalive를 관측하지 못하는
한계를 확인해 실제 HTTP 수신으로 보강했다. 해당 관측 한계를 제품 실패로 계산하지 않는다.
실제 HTTP 반복 검사의 간헐 수신 누락은 별도 조사 중이며, 종료 전송의 전면 성공으로
표시하지 않는다. 명시적 본인 빈 점유 재개와 미저장 값 보호도 독립적으로 검증한다.

후속 클리닉 후보의 첫 재현은3 FAIL/2 PASS였다. 개설/직접 등록/학생 신청의 자정
경계를 수정한 뒤 관련60 PASS/10 SQLite SKIP, 리마인더·연속 예약43 PASS/1 SKIP를
확인했다. 새0022 제약은 기존 행을 보존하는 확장이지만 contract annotation을
유지한다. 운영은 호환성·PostgreSQL 잠금/rollback 증거를 확인한 exact main에서
`workflow_dispatch allow_contract_migrations=true` 게이트를 사용한다. 자동 push의
contract 차단을 우회하지 않는다. 두 시간 범위 write 플래그의 실제 활성화와 전체
API/worker reader 수렴, PC/390px 실사용이 끝나기 전에는 자정 민원 완료로 표시하지 않는다.

테스트만 수정한 PR480이 제품 배포로 분류된 재발 원인도 수리한다. 표준tests 디렉터리와
tests.py를 push 및 누적 이미지 diff에서 제외하되 제품 변경이 섞이면 기존 분류를
유지한다. 실행 가능한 분류/인프라 계약132 PASS 뒤 BASE 입력의 별도 경계 assertion
1건을 정정했고, Windows cp949 출력 해석 오류2건은 UTF-8 환경에서 재확인했다.
변경·실패3건은 모두 PASS이며 동일 입력의132개 성공 증거를 재사용한다.

클리닉 reader PR[482](https://github.com/guswls3028-art/academy-backend/pull/482)의
exact `e746b34b42ed592b72c1b27dab67d118d9e34c2d`는
CI[35501775489](https://github.com/guswls3028-art/academy-backend/actions/runs/35501775489)에서
PostgreSQL5436 PASS/5 SKIP/629 subtests, Django5265 PASS/148 SKIP/619 subtests,
static/migration PASS를 확인했다. 선행481 배포가 완료되기 전에는 병합하지 않는다.
그 뒤 모든 API/worker의 reader와0022 수렴을 확인해야 기본값true인 별도 활성화
release를 승격할 수 있다. 명시적 환경false는 유지하며 두 runtime 설정을 확인한다.
활성화 후보의 자정 전용·자정 이후 API 검사는15 PASS/11 subtests다. 기존 자정 전용
테스트는 overnight=false를 명시하고, 자정 이후 정상 경로는 설정 override 없이
실제 활성화 기본값으로 검사한다. 이 로컬 결과는 reader 배포 완료 조건을 해제하지 않는다.

Frontend 고정 build의 점수 회귀는44 PASS/0 FAIL/0 SKIP, native/auth/playback56 PASS,
추가 transport 계약3 PASS와 기존 refactor budget/typecheck/lint/build PASS다.
실제 reload 검사에서 PC/390 모두 종료 요청 HTTP 수신은0회였고 명시적 본인 빈 점유
재개→22 입력→저장→reload는 성공했다. 종료 전송을 보장한다고 서술하지 않는다.
공식 격리 실사용의 staff 주관식 복구, 가입/승인, 정답·만점 변경 재채점, 조교 숙제
UI 채점 및 자정 클리닉은 추가 검사 코드와 독립 검토를 마쳤거나 진행 중이며,
실제 실행·cleanup0·배포 후 결과가 나오기 전에는 완료로 표시하지 않는다.
