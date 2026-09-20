# 안정화·사용 편의성 실행 계획

**상태:** active — 1차 운영 검증 뒤 사용자가 안정화 완료까지 계속 진행하도록 지시했다. 새 주관식 자기 점유 민원, 자정 이후 클리닉과 교직원 직접 등록, 기존 업무별 미검증 및 배포 선행 조건을 §8에서 처리한다. 전체 안정화 완료가 아니다.
**갱신 기준:** 2026-09-21 KST. 현재 실행·운영 SHA/run은 §8, 첫 배치 출발점·결과는 §2·§7.
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

이 절의 증거 표는 §7의 첫 완료 배치 기준이다. 이어서 확장한 가입·직원 채점·
정답/만점 변경·수동 통과 취소·자정 이후 검증의 실행 상태는 §8을 우선한다.
새 테스트의 존재만으로 아래 공백을 완료 처리하지 않는다.

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

첫 배치는 `stability-foundation-0920` 소유 worktree에서 S0와 첫 안정화 수리를 완료했다.
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

### 현재 실행 상태 — 2026-09-21 KST, backend 배포 성공·frontend 실사용 검증 진행

- Backend 공식 [35540757110](https://github.com/guswls3028-art/academy-backend/actions/runs/35540757110)은
  최종 성공했다. 소스는 `057403c653f4c5f26fc43dc524b974895a4ba432`, 성공 manifest를
  반영한 main은 `f30e36f2b1065a52c1cb8bee42cf3fc3a49906c2`다. 6개 새 이미지 각각
  Critical0/acceptedCritical0/High0, 격리 개발 Excel/PPT/R2, preprod DB 격리·CDN,
  임시 서버 종료 확인, 운영 migration·API/worker 교체·실행 digest·영상 체인,
  manifest 승격과 공유 잠금 해제를 모두 통과했다.
- Frontend PR562의 exact `18daa4c848f0749a24b5463d4852f4fa087deeda`는 Quality와
  전체 E2E 성공 후 일반 merge로 `49512ab449626c5db3c0bf2e10edf56aae477b0f`에
  반영됐다. 새 공식 [35543367845](https://github.com/guswls3028-art/academy-frontend/actions/runs/35543367845)가
  진행 중이다. 동일 산출물21개·cleanup0, 고정 v3 최종 UI QA, 운영 승격과 두 도메인
  파일 일치·영향 화면 확인이 남았다. 운영 frontend는 아직 d33c다.
- 다음 실행은 새 공식21개 결과 확인이다. 실패하면 exact 산출물·응답·정리 결과로
  원인을 좁히고 수정한다. 성공하면 새 source/run/artifact/backend manifest에 묶인
  일회용 v3 pins로 최종 QA를 실행한다. 기존 실패한3efc 증거를 승격에 재사용하지 않는다.
  메시지·공용 컨트롤 작업의 별도 병합·runtime HOLD는 유지한다.

아래는 현재 성공본에 도달하기까지의 실패·수리 기록이며 현재 배포 상태를 대체하지 않는다.

- OMR 수정의 공식 배포35532553325/c2ee는 이미지 조립·스캔 단계에서 실패했다.
  API와 AI의 완료된 스캔에서 `CVE-2026-93990` / `expat` /
  `2.8.3-1~deb13u1` High1이 각각 확인됐다. 격리 개발·preprod·운영 교체는
  모두 건너뛰었고 공유 배포 잠금은 반환됐다. 당시 운영 backend의 마지막 성공본은
  c880, frontend는 d33c이며 이 실패를 기능 수정의 운영 반영으로 세지 않는다.
  보안 수정은 fresh c2ee 기반의 소유 세션 `stability-ai-scan-0921`에서 진행한다.
  upstream Expat PR1282의 실제 수정과 정상/비정상 UTF-16 회귀를 고정하고,
  시스템 라이브러리뿐 아니라 pinned Python3.11.15의 bundled Expat 경계도
  함께 검증한다. High 상한0·예외 없음은 유지하며 새 여섯 digest의 완료 스캔과
  전체 배포 성공 전에는 frontend 승격·추가 최종 QA·다른 작업의 runtime HOLD를
  해제하지 않는다. 과거 스캔의0건을 현재 취약점 부재로 재사용하지 않는다.
  보완 PR [489](https://github.com/guswls3028-art/academy-backend/pull/489)의 head는
  `c95e9183f598cbe31a201dba31832299e16ba548`이다. 집중 계약116개·shell/Python
  구문·diff 검사는 통과했다. 공식 CI35534369800의 이미지 검사는 45분 제한으로
  취소됐으며 수동 취소가 아니다. normal·XML_MIN_SIZE의 취약 입력 실패 재현과
  수정 후 upstream·UTF-16 검사는 통과했고 wide configure에서 종료됐다.
  Python 확장·최종 이미지까지 통과한 것으로 세지 않는다. 같은 CI의 PostgreSQL5464 PASS/
  5 SKIP/639 subtests, Django5293 PASS/148 SKIP/629 subtests와 static·migration은
  성공했다. 45분 한도와 검사는 유지하고 Quality 및 공식 배포의 공통 base
  빌드만 기존 Video와 같은 ARM runner로 옮긴 뒤 새 CI로 완료 여부를 검증한다.
  전환 head `6ccc90a5a3a2547e981bd55808d891699c1553fa`는 관련117개·workflow
  governance·YAML·diff 검사를 통과했다. 새 공식 CI35539079833을 시작했으며
  이전 실행의 부분 통과를 전체 이미지 성공으로 대체하지 않는다.
  ARM 실행35539079833은 normal/min 검사를 약170초에 통과한 뒤 upstream의
  ushort wide 테스트 미지원으로 실패했다. 라이브러리 ABI를 변경하지 않고
  실제 wide parser의 정상/비정상 입력·16비트 callback 출력을 검증하도록 보완했다.
  후속 head `a7da0fa97bf579c0ee4273415c93cc6c5cfbd167`의 관련119개·shell/AST/diff는
  통과했다. 공식35539486916의 native 이미지 검사도 성공했다: normal/min 실패
  재현·수정 후 전체 upstream, wide 실제 ABI·UTF-16, Python XML779개(13 skip),
  실제 로드 경로·최종 이미지 package/parser 검증까지 통과했다. 새 여섯 digest의
  ECR 스캔·전체 배포 성공은 여전히 별도 필수 단계다.
  같은 실행의 전체 Django 검사는 QEMU action을 요구하던 이전 계약1개 때문에
  실패했다(그 외5295 PASS). 해당 테스트만 native ARM·실행 검증으로 맞춘
  head `7a35e49e8f836c6dea0537011ae4aa8c10351a1b`는 관련131개가 통과했다.
  이미지 입력은 a7da와 같아 실제 이미지 성공 증거를 재사용한다. 원격 PR head와
  Actions 조회에서 새 자동 실행0건을 확인해 기존 공식 workflow를 같은 branch로
  dispatch했다(35540127663). 이 실행의 필수 CI 성공 없이 병합하지 않는다.
  최종35540127663은 성공했다(PostgreSQL5467 PASS/5 SKIP/639 subtests,
  Django5296 PASS/148 SKIP/629 subtests). PR489를 보호된 일반 merge로 병합한
  main은 `057403c653f4c5f26fc43dc524b974895a4ba432`이며 공식 배포35540757110이
  시작됐다. 6개 이미지 빌드·전체 보안 검사와 상시 격리 개발환경 검증은 성공했고,
  별도 preprod도 DB 격리·CDN 재생 체인을 통과했다. 임시 서버 종료 확인 뒤
  운영 마이그레이션을 마쳤고 API/worker 교체가 진행 중이다. 최종 운영 검증과
  성공 manifest 확인 전에는 frontend를 승격하지 않는다.
  Python 전체 대신 같은3.11.15의 `pyexpat`·`_elementtree`를 함께 재빌드하고
  서비스의 마지막 패키지 설치 뒤 실제 연결·동작을 다시 확인한다.

- 새 공식35528265922는19 PASS/2 FAIL/0 SKIP/0 flaky로 종료했고 운영 승격은
  차단됐다. 정확한3efc 산출물 fingerprint는
  `c54d162e7d624742fe16836a4cfce11a2c07988f017d16533d9887e2bc077790`다.
  OMR 첫 답안 수정 응답의200 assertion과 숙제 채점의 `수정` 버튼 대기만 실패했다.
  OMR은 실제500 응답이며 정리 오류가 가린 실패가 아니다. 기존 개발 로그의
  정확한 실패 구간을 두 번 읽어 `OMR_ANSWERS_INCOMPLETE`를 확인했다.
  종이 OMR의 숫자 정답 주관식도 온라인 자동 채점 대상으로 분류되어30개
  객관식 답안에32개를 요구했다. 기존 `docs/domain/omr.md`의 종이 주관식
  수동 채점 정책에 맞춰 source별 채점 경계를 수정한다. 원문 로그·본문은
  반출하지 않았고 두 조회 전후 c880 컨테이너 동일·reader 종료를 확인했다.
  제품 수정은 fresh main cd3 기반의 별도 소유 세션 `stability-omr-policy-0921`에
  분리하며, 종이 수동 점수 보존·온라인 숫자 답안·누락 검사를 회귀 검증한다.
  이 수정은 새 backend 공식 배포와 새 frontend 전체 실사용을 모두 요구한다.
  수정 PR [488](https://github.com/guswls3028-art/academy-backend/pull/488)의
  head는 `b3b1f5de59b77d859b7ecb2e4891aa1a4953aa5f`다. 첫 저장500의
  실패 재현 뒤 저장200·수동8/9·정답/만점 변경 후 합계46과 수동17 보존을
  확인했다. 관련 회귀53개/2 subtests와 static·migration·boundary 검사는
  통과했다. 전체 CI35531781958도 성공했다(PostgreSQL5444 PASS/5 SKIP/
  639 subtests, Django5273 PASS/148 SKIP/629 subtests). 보호된 squash 병합으로
  main `c2ee17256522f394fb1032fdf7a38c938e95cc86`이 되었고 검증한 b3와
  tree가 같다. 공식 immutable 배포35532553325는 위 이미지 스캔에서 중단됐다. 현재 OMR 세션은
  clean b3를 보존하며, squash main은 원격 ref로 조회한다.
  숙제는 빈 성적표가 자동 입력 모드로 바뀌는 동안 검사 코드가 사라진 버튼을
  기다리는 경합을 로컬에서 재현했다. 입력 준비 대기 후390px91점 저장/reload,
  기존 읽기 모드에서1366px92점 수정/reload가 통과했으며 실제 실패 상태도 대조한다.
  이 경합의 최소 검증 보완은 PR [562](https://github.com/guswls3028-art/academy-frontend/pull/562),
  head `18daa4c848f0749a24b5463d4852f4fa087deeda`다. 제품 코드는 그대로이며
  앞선657의 Quality35531433770은 성공했다. 실제 직원 로그인 뒤 필수 출근
  선택창에서 `출근하지 않고 로그인`을 선택하는 누락 단계도 추가했다.
  기존 실패 후보의 좁은 진단은 이미지 접근에
  필요한 소유 tenant 환경값을 빠뜨려 목표 단계에 도달하지 못했다. 이 별도
  실행기 오류와 tenant387 정리0을 기록하고 원본 증거를 보존했다. 공식 실행기의
  정확한 환경값을 복원한 두 번째 진단에서는 이미지 경계 오류가 사라졌지만,
  과제는 미분류 dialog가 옵션 클릭을 가로막고 OMR은 응답 관측 전 timeout됐다.
  이 결과를 원래245/1005 실패의 재현으로 세지 않는다. tenant388도 정리0이며
  두 실행기는 승인값 false·소비 완료로 기록하고 추가 재실행하지 않는다.
  미분류 dialog의 이름은 관측하지 않았고, 출근 선택 보완은 소스에서 확인한
  정상 로그인 단계의 누락을 해결한 것이다. 최신 Quality35532368840은 성공했다.
  최신18daa의 전체 E2E35532368839도 성공했다: 브라우저732개·iPhone36개·운영
  조회6개·bundle/theme 각1개, 기존 Chromium 전용1개는 WebKit에서 제외됐다.
  이는 아직 실행하지 않은 후속 main의 격리 업무21개를 대체하지 않는다.
  657의 전체 E2E35531433771은 이 추가 코드 push로 workflow가 자동 대체·취소했다.
  수동 취소·면제 없이 새 검사를 진행하며, 선택된 브라우저 시나리오의 변경 없는
  입력에만 이전 성공 증거를 재사용한다.
  클리닉 심야 개설·직접 등록·학생/학부모 신청, Q&A·계정은 이번 실행에서 통과했다.
  클리닉 직접 등록은 응답362ms/목록689ms, 통과는628ms/667ms의 단일 관측이다.
  영상은 PC/모바일 각각690초·갱신·진도 저장·오류0이나 전체 실패 때문에 후속
  playback Inspect는 실행되지 않았다. 두 QA tenant385/386·user·R2·프로세스·
  리스너 정리0이다. 좁은 진단·추가 최종 QA·새 공식 전체 성공 전에는 승격하지 않는다.
- de7 전체 E2E35526991204는 성공했다. 일반 브라우저732개, iPhone36개,
  운영 조회6개와 bundle/theme 각1개가 통과했고 기존 Chromium 전용1개는
  WebKit에서 제외됐다. 이 성공은 위 실제 업무 실패를 면제하지 않는다.

- 후속 PR [561](https://github.com/guswls3028-art/academy-frontend/pull/561)은 필수
  Quality35526991200 성공 뒤 보호된 일반 병합으로 frontend main
  `3efc74d79f953b3f6c1fa23105585e9be28f7fe5`가 됐다. 새 공식 실행
  [35528265922](https://github.com/guswls3028-art/academy-frontend/actions/runs/35528265922)가
  위19/2 결과로 종료됐으며 운영은 아직 아래 d33이다. merge tree는 검증한 de7 후보와 같다.
  독립 검토로 이전 성공4c54와 현재 de7의 화면·E2E·의존성·설정 Git 객체가
  동일함을 확인해, 별도 전체 E2E35526991204와 main 격리21개를 병렬 진행했다.
  검사 취소·면제는 없으며 **운영 승인은 후속 main의 필수 검사·새 공식21개·추가 QA·
  정리0 모두 성공한 뒤**다. 실패한 후보의 좁은 진단 성공으로 이 조건을 대체하지 않는다.
- 공식21개 안에는390px의23시→익일1시 UI 개설·저장201, 시작 운영일 재진입,
  익일0시30분 수동등록과390/1366px reload가 있다. 학생의 운영일에서 심야
  예약·서버 저장값·학부모 reload/재로그인/취소도 포함한다. 이 심야 수동등록은
  admin 역할이며 별도 staff 검사는 낮 세션이다. 실제 자정이 흐르는 순간이나
  월경계 자동 갱신을 이 실사용 결과가 보장한다고 주장하지 않는다.
- 최종 화면 QA의 v3 실행기는14개 파일 해시·사전 도구 검증·Port 자식에만
  환경 적용·기존 공식21개/격리/정리 조건·finally 보존의 독립 검토를 통과했다.
  아직 실행하지 않았으며 새 공식21개 성공 뒤 root의 정확한1회 실행 창이 필요하다.
  실행기는 승인 파일을 자동 소비하지 않으므로 담당자가 실행 후 실패 포함
  승인값을 false로 기록하고 자동 재실행하지 않는다.
- 이번 검증에서 만든 빌드 캐시2개·중복 소스1개(59,481파일,
  1,725,396,698 logical bytes)는 소유 절대 경로·reparse point0을 확인한 뒤
  삭제를 시도했지만 자동 승인 검토가 실행 전에 `blocked by policy`로 거부했다.
  삭제0·3개 모두 보존이며 다른 방법으로 재시도하지 않았다. 원본 소스·컴파일러·
  실행 파일·manifest·검증 기록도 유지한다. 이전 §4의 캐시 거부와 별개 기록이다.

- Backend 활성화 PR [485](https://github.com/guswls3028-art/academy-backend/pull/485)는
  exact head `680ed5cae8a52ce074fcfad0c055d1eb4d9362f5`의 필수 CI
  [35508963261](https://github.com/guswls3028-art/academy-backend/actions/runs/35508963261)
  통과 뒤 main `c88038d47a05e50ec7e8fd095698f88c910e080f`에 병합했다.
  공식 배포 [35509622551](https://github.com/guswls3028-art/academy-backend/actions/runs/35509622551)는
  완료됐다. 성공 manifest는 같은 source와 `verifiedAt=2026-09-20T22:04:28+09:00`,
  `complete=true`를 기록했다. 독립 read-only SSM으로 실제 API digest 일치,
  migration0022·확장 제약·두 플래그 true·DRF 3.17.2를 확인해 backend 선행 HOLD를
  해제했다. 여섯 이미지 모두 critical=0/acceptedCritical=0/high=0이다.
- 통합 frontend PR [556](https://github.com/guswls3028-art/academy-frontend/pull/556)의
  후보는 `dd4f7d14882c55e3e23ebf633d2c27d6a88c9285`다. 선행 b950의 Quality
  [35509628519](https://github.com/guswls3028-art/academy-frontend/actions/runs/35509628519)는
  성공했고 전체 E2E [35509628673](https://github.com/guswls3028-art/academy-frontend/actions/runs/35509628673)도
  화면728개·iPhone36개·운영 read-only6개와 bundle/theme 검사를 통과했다.
  마지막 학생 작성창 경합 수정 후 dd4 Quality35512120717은 성공했고 전체
  E2E35512120718은729개 성공·1개 실패다. 실패는 비동기 실행 요청의 수신 전에
  mock 본문을 읽는 테스트 경합으로 재현했고, 요청 수신 대기로 수정해 통과했다.
  필수 병합 검사 두 개와 backend 선행 조건 통과 후
  보호된 일반 병합으로 main `27da7b7443c51bcde4d47740b74b9091030ab8cb`가 됐다.
  공식 main Quality [35512743917](https://github.com/guswls3028-art/academy-frontend/actions/runs/35512743917)의
  동일 산출물 실사용은16개 성공·5개 실패로 운영 승격을 차단했다. 양쪽 QA
  tenant/user 및 R2·프로세스·리스너 정리0을 확인했다. PC/390px 각각690초 영상
  재생·갱신·진도 저장은 오류0이며, 모바일 클리닉 수동 통과는 이번 측정에서
  응답640ms·목록 반영675ms다(1회 관측이며 p95가 아님).
  남은 실패는 클리닉 생성 버튼 문구 불일치, 수학 답안 fixture 검증,
  과제 채점 컨트롤과 Q&A/계정 GET 통신 경계다. 클리닉 문구 수정 후 공식27da
  산출물의 PC/390px mock 생성 검사는 통과했고 나머지는 좁은 격리 진단 중이다.
  검사 보완 후속 PR [561](https://github.com/guswls3028-art/academy-frontend/pull/561),
  `4c54bf220787087d947791429b539bfaa9d534b3`를 draft로 올려 필수 CI와 전체
  E2E를 실행했다. 필수 Quality35515069050과 전체 E2E35515069234는 모두 성공했다
  (route mock732·iPhone WebKit36·운영 조회6·bundle/theme smoke 각1;
  기존 Chromium 전용1개는 WebKit 미실행). 제품 코드와 빌드 입력은 바꾸지 않았으며, 과제390/1366px
  입력 모드별 저장·재조회와 수학 정답 검증의 로컬 재현도 통과했다.
  정확한 후속 후보의 전체 PR E2E·21개 실사용·추가 QA·정리0 모두
  성공하기 전 운영 승인은 보류한다. main push가 별도 전체 E2E를 자동 실행한다고
  가정하지 않는다. 운영은 아직 `d33c4c645868b8924719507524e2f03ea8d02d96`다.
- 실패한 공식27da 산출물의 별도 진단은11개 중0개 성공·5개 실패·직렬 후속6개
  미실행이다. 다섯 파일 모두 첫 로그인 안내 확인 POST의 통신 경계에서 먼저
  실패했으며, 기존 공식 실행의 후반 GET 실패와 같은 원인이라고 단정하지 않는다.
  실제 handler/DRF3.17.2와 Axios→Chromium→동일 전달 guard의 로컬 빈 POST는
  정상200이다. 개발 서버 해당 시간대5xx·worker 재시작은 관측되지 않았다.
  양쪽 QA tenant372/373·user·R2·원격 프로세스·리스너 정리0과 테스트 Job의
  자식29개 종료는 입증했다. 로컬 SSM Job의 개별 종료 증거가 누락되어 전체
  프로세스 종료·진단 완료는 false로 보존한다. 사후 프로세스0을 그 증거로
  대체하지 않는다. 다음 진단용 고정 필드 관측만 별도 파일에서 검토 중이며,
  재실행·운영 승격 증거로 사용할 수 없다. 원본 증거와 파일 해시는 보존한다.
  관측을 보완한 두 번째 진단도0/5/6이며, 다섯 POST가 화면 이동·종료 전에
  2017–2025ms의 `socket-hang-up`으로 실패했음을 확인했다. 임시 tenant374/375와
  원격 자원 정리0, 로컬 포트0을 확인했으나 SSM port의 종료 프로토콜 유효성이
  false여서 이 실행도 진단 완료 false다. 별도 로컬 검사에서 정상 zero seal 뒤
  이미 닫힌 stdin에 STOP을 쓰는 경합을 재현하고 수정 후보를 검증했다. 원본
  실행 결과를 사후 성공으로 바꾸지 않는다. 개발 서버에 직접 보낸 미인증
  동일 경로의 빈 POST/JSON 요청은401·37–45ms로 정상 거부됐다. 후속 미인증
  SSM 전달 검사도 같은 guard의 API/browser6개 모두401·55–73ms, 재시도0으로
  통과했다. 정리 검사가 종료 상태를 과도하게 제한해 남긴 원본 false는 보존하고,
  정확한 세션의 비활성·EndDate·Terminating 읽기로 기존 종료 계약 충족을 별도 기록했다.
  실제 빌드 안내창의 요청도 빈 본문/Content-Length·Content-Type 없음으로 확인했다.
  이 음성 대조 검사들은 실제 인증 흐름의 실패 원인을 확정하지 않는다.
  원본 첫 클리닉 테스트1개·재시도0을 반복 관측했으며 같은 안내 확인 실패다.
  Node 내부 헤더 표현은 CL0/완결·1017바이트로 보였다. 양끝 관측은1017바이트를
  개발 API의 TCP가 모두 수신·ACK한 뒤 약2초 후 응답 없이 서버가 FIN을 먼저
  보내는 것을 입증했다. Node 연결 재사용 가설은 제외한다. 내부 표현만으로
  실제 전송 헤더가 완결됐다고 판단했던 결론은 아래 실제 바이트 검사로 정정한다.
  `gunicorn_h1c`는 설치되지 않았고 worker는 gevent·keepalive 기본2초다.
  tenant376/377와 관련 자원 정리0·포트0은 확인했으나 로컬 tunnel STOP이 종료
  이벤트 처리 전에 EPIPE를 낸 두 실행의 aggregate 완료 false는 보존한다.
  로컬 Job 종료·zero 확인을 원격 SSM 종료보다 먼저 수행하는 순서 수정 후,
  tenant378/379 진단은 모두 정리0·6개 child의 유효 종료·세션5/5 종료·포트0으로
  완료됐다. 테스트 자체는 같은 요청에서 실패하며 진단 완료와 제품 성공을 구분한다.
  준비시간 차로 요청을 놓친 첫 syscall 관측과 파서 공백 처리 오류는 실패로 보존했다.
  별도 관측기는 종료 정렬 공백을 처리하고, 테스트 직전 nonce gate로 시작 시점을
  맞췄다. 두 번째150초 관측은4개 worker 동일성·분리·tracer 종료·EOF·marker 정리,
  unknown/drop/pending0으로 유효하다. 유일한1017바이트 연결에서 worker가 전체
  데이터를 읽고 즉시 다시 읽어 EAGAIN을 받은 뒤 약2초에 응답 없이 닫았다.
  kernel→worker 수신 손실은 제외한다. 후속 수동 관측은 실제 요청을 메모리에서만
  설치 parser에 공급했다. 실제1017바이트에 헤더 종료 구분자가 없고 CRLF는17개,
  bare LF/NUL은0이며 parser가 헤더 단계에서 두 번째 읽기를 요구함을 확인했다.
  parser 코드 pin·요청1건·정리·kernel/sequence/drop0·동일 container/image/master를
  검증했다. 따라서 내부 `_header` 표현과 길이 일치만으로 wire 완결성을 판단할 수
  없다. 후속 실제 Node22.23.1 socket handoff는1회·latin1·1017바이트·CRLF18개,
  마지막 CR/LF/CR/LF·`_header`와 바이트 일치로 정상임을 확인했다. 같은 실행의
  서버 입력은1017바이트·CRLF17개로 달랐다. 추가 고정 enum 관측에서 서버 입력은
  CR19개/LF17개·마지막 CR/LF/CR/CR로, 최종 LF가 CR로 바뀌었음을 확인했다.
  모든 관측은 원문 없이 길이/고정 분류만 남겼고, tenant382/383와6개 자식·정확한
  세션5/5·포트·marker 정리0, 동일 개발 container/image/master를 검증했다.
  원인은 연결 도구의 구체적인 변환 경로로 좁혀졌다. 설치된 AWS Session Manager
  plugin1.2.814.0의 [SendInputDataMessage](https://github.com/aws/session-manager-plugin/blob/1.2.814.0/src/datachannel/streaming.go#L284-L287)는
  단독 LF를 세션 종류와 무관하게 CR로 바꾼다. 실제 agent3.3.3598.0은 Mux 경로를
  사용하며, smux 헤더8바이트와 요청1017바이트가1024+1로 나뉘는 경계가 정확히
  맞는다. 최신 공식1.2.835.0에도 같은 변환이 남아 있어 단순 업그레이드는 해결책이
  아니다. 기존 터미널 변환은 유지하고 Port 바이트를 보존하도록 shell 세션으로만
  조건을 제한한 고정 소스 custom1.2.814.10001을 만들었다. 실제 전송 함수의 원본
  RED→한 줄 수정 GREEN과 shell/명령 세션 호환·일반 바이너리·분할 경계를 검증했다.
  고정 Go1.26.8/vendor 소스의 Windows/Linux 빌드와 새 디렉터리의 Windows 재빌드
  hash 일치를 확인했다. frontend PR561의 `de7fe76c348d1e79fad1d47312c2b87918ecb369`
  Quality35526991200에서 Linux 재빌드·동일 hash·custom version 실행도 통과했다.
  PR/main 필수 품질 검사에서 검증한 도구 artifact를 개발 job이 다시 검증하며,
  고정 Port 세션 자식에만 PATH를 적용한다. 운영 계약은 frontend
  `scripts/ssm-binary-safe/README.md`와 `docs/DEPLOYMENT-OPERATIONS.md`가 소유한다.
  같은 원본 클리닉 테스트를 수정 도구로 재실행하자 안내 POST200·61.426ms,
  클리닉 생성201을 포함한 전체1개 테스트가3944ms에 성공했다. 서버 입력도
  1017바이트·CR18/LF18·완결 헤더·parser1회 읽기·정상 응답으로 복원됐다.
  tenant384·6개 자식·정확한 세션5/5·포트·marker·관측 socket 정리0과 동일 개발
  container/image/master를 검증했다. 이 비교는 검증 연결 오류의 수정 근거이며
  실패한27da 산출물의 운영 승격 근거는 아니다. 새 후보의 전체 PR E2E·공식21개
  실사용·추가 QA·정리0과 운영 readback이 남아 있다. 기존 Q&A/계정 GET 및 과제
  실패가 모두 이 원인인지는 새 공식 실행에서 별도로 확인해야 한다. 전역 도구나
  앱 요청 본문·패딩·재시도·timeout은 변경하지 않았다.
  최초 root 관측은 appuser의 user-site 패키지 경로 차이로 ready 전에 실패했다.
  정확한 설치 경로만 명시한 wrapper를 자체 검사하고 재실행했으며, 준비 실패와
  gate timeout 원본은 보존했다. tenant380/381도 관련 자원 정리0이다.
  Django/WSGI 호출·요청 재전송·원문/인증값/본문/요청 해시 저장은 하지 않았다.
  요청/Agent 변경도 없으며 별도 진단은 운영 승격 증거가 아니다.
- 빌드의 `/version.json`과 일반/빈 점수 점유 해제 요청의 `X-Client-Version`이
  달라지던 경로를 하나의 빌드 식별자로 통일했다. 실제 빌드 HTTP 비교에서 수정 전
  `dev` 불일치, 수정 후 일치를 확인했다. 인증·tenant·keepalive·요청 본문은 유지한다.
  새 회귀는 기존 필수 Quality 경로에서 실행되어 성공했다. CI에 없는 검사로
  오인해 중복 게이트를 추가하지 않았다.
- 학생 질문·상담은 reload 후 현재 탭 재클릭→즉시 작성 CTA에서 늦은 탐색이
  폼을 닫는 경합2개를 독립 재현했다. 같은 탭의 재탐색을 생략하는1줄 수정 후
  2개 모두 통과했고, PC/390px·복원·실제 탭 전환·실패 복구·자녀 격리까지
  focused12개를 확인했다. 첫 전체 실행의 초기 page.goto timeout1개는 같은
  source 재검증에서 통과했으며 실패 기록도 보존했다. Typecheck/lint/guard 통과.
  이전 c183 CI의 유일 원인으로 단정하지 않는다.
- 메시징 PR 558의 편집기/클립보드 수정과 PR 555의 출결·교사 화면 수정을 통합했다.
  이 작업이 최종 frontend 배포를 소유하며 두 원작업은 독립 배포·공유 개발 환경
  변경을 보류한다. 공식 21개 실사용·정리0 뒤 같은 산출물의 추가 출결/교사 UI
  검사를 완료하고 운영 승격한다. 추가 검사의 실제 역할은 admin이며 조교 권한
  증거는 공식 staff 시나리오로 구분한다.

아래는 재현·수정·검증의 경과다. 현재 상태와 충돌하는 과거의 후보/대기 문장은
당시 기록이며, 위 실행 상태와 공식 영수증을 우선한다.

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
최종 성공했다. exact run의 production review는 eligible readback 후 공식 API로
승인했고 개발·preprod·임시 서버 정리·rolling·실제 digest 검증을 모두 통과했다.
성공 manifest는 같은 source SHA와 여섯 candidate digest를 보존하며
`verifiedAt=2026-09-20T19:16:15+09:00`, `complete=true`다. 여섯 ECR scan 모두
critical=0/acceptedCritical=0/high=0을 확인했다.
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
static/migration PASS를 확인했다. 선행481 배포 완료 후 두 배포 보고서만 바뀐 main을
공식 update-branch로 반영했다. 새 exact head는 `1f28cceca3e54e99eb4f6c964e0d4e59753c4e8d`이고,
필수 CI[35504765481](https://github.com/guswls3028-art/academy-backend/actions/runs/35504765481)가
통과 후 main `07597fc6837e3274516c597be4355344327b3307`에 병합했다.
자동 push35505463274의 contract 차단 뒤 공식 dispatch
[35505492422](https://github.com/guswls3028-art/academy-backend/actions/runs/35505492422)에
`allow_contract_migrations=true`를 명시했다. exact production review의 자격 확인·승인·
pending 해소 뒤 전체 workflow가 성공했다. 성공 manifest의 source는07597fc6,
`verifiedAt=2026-09-20T20:48:00+09:00`, `complete=true`다. 모든 API/worker의
정확한 digest, 격리 개발·preprod·임시 서버 정리, 운영 migration·rolling·검증과
image 정리를 통과했다. 추가 read-only SSM에서 운영 API의 manifest digest 일치,
DRF3.16.1, migration0022, 서로 다른 시작/종료 시각을 허용하는 실제 DB 제약,
두 write 플래그false를 확인했다. 별도 활성화
PR[485](https://github.com/guswls3028-art/academy-backend/pull/485)의 reader 선행 HOLD는
해제됐고 최신 main 병합 뒤 필수 CI를 다시 통과해야 한다.
명시적 환경false는 활성화 후에도 우선한다.
활성화 후보의 자정 전용·자정 이후 API 검사는15 PASS/11 subtests다. 기존 자정 전용
테스트는 overnight=false를 명시하고, 자정 이후 정상 경로는 설정 override 없이
실제 활성화 기본값으로 검사한다. 이 로컬 결과는 reader 배포 완료 조건을 해제하지 않는다.

Frontend 고정 build의 점수 회귀는44 PASS/0 FAIL/0 SKIP, native/auth/playback56 PASS,
추가 transport 계약3 PASS와 기존 refactor budget/typecheck/lint/build PASS다.
실제 reload 검사에서 PC/390 모두 종료 요청 HTTP 수신은0회였고 명시적 본인 빈 점유
재개→22 입력→저장→reload는 성공했다. 종료 전송을 보장한다고 서술하지 않는다.
Frontend PR[556](https://github.com/guswls3028-art/academy-frontend/pull/556)의
`c4c8135d8`은 staff 주관식 복구, 가입/승인, 정답·만점 변경 재채점, 조교 숙제
UI 채점 및 자정 클리닉의 실제 UI 검사와 독립 검토를 포함한다. 클리닉 고유72개
브라우저 회귀와 runner 계약126개가 통과했고, 클릭부터 응답·목록 반영까지 숫자만
공식 보고서에 보존한다. 개발 의존성 최소 수정4건은 전체audit0, lint/type/API/build
및 새 bundle 부팅을 통과했다. Backend 활성화 release와 두 플래그true readback 뒤에만
FE를 병합한다. 동일 산출물21개 실사용·cleanup0·운영 확인 전에는 완료로 표시하지 않는다.

추가로 발견한 backend DRF 경고4개는 동일한 두 advisory가 manifest별로 중복된
것이다. 최소 수정3.17.2를 High 독립 검토했고, 기존3.16.1의 과대 JSON/Form
HTTP200을 재현했다. 새 버전의 정상 한국어 JSON·과대 요청HTTP400·3 MiB multipart
파일 스풀, PPT 제한과 검수 보고서 검사27 PASS/2 subtests를 확인했다.
기존 venv를 바꾸지 않고 별도 패키지 경로에서 실행했고, 의존성 check와 OpenAPI
일치 검사가 통과했다. 실제 테넌트/인증/업로드 완료는 필수 전체 CI·실사용의 별도
증거를 요구한다. 파서 단독 테스트를 해당 경계의 성공으로 확대 해석하지 않는다.

§5 대조에서 수동 통과 취소 API는 있었지만 화면 호출자가 없음을 확인했다.
새 버튼의 회귀가 아니라 복구 편의성·실사용 공백이다. 해결 완료 포함 목록에
직접 수동 통과만 취소하는 경로를 연결하고, 원문 처리 시각을 보내 서비스 행 잠금
안에서 같은 판단인지 확인한다. 시각 변경·자동 통과·성적 교정·이미 취소된 결과는
409로 보호하며 기존 token 없는 내부/API 계약은 유지한다. 관련 서버 검사
11 PASS/10 subtests와 schema 갱신을 확인했다. 실제 조교 UI와 학생 결과/reload,
PC/390px 실패·재시도 및 최종 전체 CI는 후속 후보에서 검증한다.

### 통합 후보와 남은 실행 증거

수동 통과 취소의 frontend 구현은 `3036e673f`에 기록했다. 직접 수동 처리만
취소할 수 있고, 실패 시 선택을 보존해 재시도하며, 다른 판단으로 바뀐409는
목록을 새로 읽고 재확인을 요구한다. PC/390px mock16개와 변경 경계2개가
통과했다. 실제 조교 UI→학생 원점수20·교정대기 상태→reload 검사는 기존
공식21개 안에 추가했으며 아직 해당 후보의 실사용 실행 증거는 없다.
Backend `fd5844322`의 필수 CI[35506971044](https://github.com/guswls3028-art/academy-backend/actions/runs/35506971044)는 성공했다.

동시에 진행되던 메시지·공용 컨트롤 작업과 최종 frontend 배포 소유자를
`stability-completion-0920`으로 합의했다. 다른 작업은 별도 main 병합·배포·
상시 개발 환경 QA를 중단하고 exact SHA와 검증 결과를 인계한다.
메시지 PR554의 main `5b31523b5`와 PR558의 초기 검증 변경, 공용 컨트롤
PR555의 `cf512ddde`를 충돌 없이 통합했다. PR555는 Quality 성공·전체 E2E
진행 중이며 PR558의 붙여넣기 race 수정은 추가 인계 대기다. 원본 PR 통과를
통합 산출물의 통과로 대신하지 않는다. 공용 컨트롤의 테마 검증 workflow도 보존한다.

PR554의 development[35506200780 attempt2](https://github.com/guswls3028-art/academy-frontend/actions/runs/35506200780)는
21개 중18개 성공·3개 실패로 운영 승격되지 않았고 두 QA tenant/user 및
객체·프로세스·포트 정리는0을 확인했다. OMR 답안 버튼, viewport 변경 뒤 편집기,
영상 bootstrap이 실패했다. OMR은 같은 버튼을 사용하는30문항 스캔·수정·저장·
원복 mock과 기존 진입6개가 통과해 원인이 아직 확정되지 않았다. viewport 변경은
AppLayout 재마운트에 맞게 실제 편집기를 다시 여는 검증으로 보강했다.
영상 POST는 두 화면에서 약8초에 timeout됐으며 재생 성공으로 보고하지 않는다.
다른 작업의 격리 QA는10:34 UTC에 끝났고 실패 실행은10:56–11:08 UTC였으므로
동시 작업 경합을 원인으로 단정할 근거가 없다.

추가 전체 회귀에서 발견한 급여 검색어 소실은 검색 직후 필터를 누를 때
Router 전환 전의 오래된 query를 복사하던 race였다. 조작 시점 URL을 합치는
frontend `c89c6544c`로 동일 조작 실패→성공과 상세5탭·reload·390px 복귀를
검증했다. 학생 HTML 제목 회귀의 클리닉 일정은 고정된8월10일 fixture라
현재 시각에 종료되어 노출되지 않았다. fixture 시각을 고정한 원래 여정이
통과했으며 실제 제품의 종료 일정 제외 정책은 바꾸지 않았다.

운영 frontend는 여전히 `d33c4c645868b8924719507524e2f03ea8d02d96`이며
위 모든 변경의 최종 exact 후보 필수 CI, backend 활성화 수렴, 동일 산출물
21개 실사용·cleanup0, 운영 두 도메인 version/assets와 영향 화면 확인이 남았다.
