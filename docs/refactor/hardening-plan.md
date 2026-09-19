# 안정화·사용 편의성 실행 계획

**상태:** active — S0 로컬 정리·문서 정돈 완료, PR/CI 준비. S1 첫 결함 재현·수리 착수. 전체 안정화 완료가 아니다.
**갱신 기준:** 2026-09-20 KST. backend `9ff14e4d4`, frontend `a705cb81a`.
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
| Frontend 운영 | `a705cb81a33300a11148c7f034fe5c2a8b5b2655`, [release35461988634](https://github.com/guswls3028-art/academy-frontend/actions/runs/35461988634), 격리 실사용21 PASS/0 FAIL/0 SKIP 및 운영 read-only PASS | 전체 업무를 망라하는 커버리지 |
| 버전 일치 | 두 release와 manifest, godmin.kr/hakwonplus.com 버전 및 lock 해제 확인 | 이후 배포에 대한 자동 보증 |
| 클리닉 | godmin700행 서버 내부10,960→3,644ms, SQL4,037→461회. 단일 전후 측정 | 브라우저 p95, 피크 동시성, 모든 학원 데이터 |
| 시험·숙제 | 재채점/수동 점수 보존, 학생 업로드→조교 모바일 PNG→재접속 통과 | 모든 파일·장애·권한·재시도 조합 |
| 영상 | PC/mobile 각690초 재생·갱신·진도 복원, 검사 오류0 | 실제 사용자의 모든 네트워크 버퍼링 |
| 도구 | 생성 API 타입, schema check, 경계 검사, 동일 산출물 개발 canary 존재 | 추가한 회귀 검사의 올바른 실행 경로 포함 |

운영 자료는 읽기 전용 집계로만 확인했다. 실사용 mutation은
[격리 개발 런타임](../operations/persistent-development-runtime.md)의 합성 `qa-*`
자료로 수행하고 tenant/user/storage/process 잔여0을 확인한다.
과거 Tenant1 운영 쓰기 QA 기록은 새 테스트 실행 허가나 현재 절차가 아니다.

## 3. 실행 순서

| 단계 | 상태 | 산출물·종료 조건 | 재개 지점 |
|---|---|---|---|
| S0 정리·계획 | 로컬 완료 / CI·병합 전 | 정리 대상/보존 이유/개수·결과, 현재 문서 경로 정정, docs CI, 커밋·PR 인계 | §4·§7, 양쪽 docs 인덱스 |
| S1 업무별 결함·증거 정리 | 영상 후보 현재성 확인 / 나머지 예정 | 아래5업무의 실행 테스트·미검증 조건·재현 결과 대조, 기존 결함 후보 현재성 확인 | §5 및 failure-transparency-stabilization.md |
| S2 수리·필요 리팩토링 | 교사 영상 목록 오류/빈 상태 수리 중 | P0/P1부터 정상 업무 단위로 실패 재현→수리→회귀/소비자 확인 | S1에서 재현된 최우선 항목 |
| S3 규모·복구·편의성 | 예정 | 혼합 합성 데이터, 중복/재시도/부분 실패, PC/390px, 응답시간·조회량, 정상 빈 결과 구분 | 각 변경의 도메인 문서·검사 |
| S4 배포·재발 확인 | 예정 | exact SHA/동일 산출물, required gate, 운영 버전·업무 readback, cleanup0, 한계 기록 | 기존 deployment/release-bundle 절차 |

S1~S4는 기능별 작은 변경 단위로 반복한다. 테스트 변경은 어느 공식 job에서
어떤 업무 조건을 실제 실행했는지 확인한다. 재현된 P0/P1을 미해결로 둔 채
다음 업무로 넘어가지 않는다. 범위가 바뀌면 계획과 PR 설명도 최종 범위로 갱신한다.

## 4. S0 정리 결정

| 대상 | 결정·현재 상태 | 안전/복구 기준 |
|---|---|---|
| main 커밋·release/report 이력 | 보존 | 공유 이력 재작성 금지. 동일 audit snapshot도 시점별 증거일 수 있음 |
| 현재 계획·인덱스 | 이 계획 재사용, 낡은 지시·중복 운영 설명 정리 중 | 과거 판단은 시점·근거 표시 후 현재 owner 연결 |
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
| 학생 등록·계정 | 입력→등록/승인→로그인→교직원/학생/보호자 조회 | account 검사는 존재. 신규 가입·중복·저장 실패의 정확한 공식 포함 범위 재확인 | [student-creation](../domain/student-creation.md), [student-core](../domain/student-core.md), frontend `student-parent-account-realuse.spec.ts` |
| 시험·재채점 | 답안/배점 저장→채점→성적·클리닉·학생 화면→reload | OMR 재채점/수동 점수 통과. 미제출·재응시·부분 실패·재시도 조합 확인 | [exam-grading](../domain/exam-grading.md), frontend `omr-review-realuse.spec.ts` |
| 숙제 | 제출→파일 저장→교직원 조회→명시적 처리→학생 결과 | 모바일 PNG/reload 통과. 다른 파일·실패/삭제·권한·새 제출 발견성 확인 | [homework-grading](../domain/homework-grading.md), frontend `student-parent-homework-realuse.spec.ts` |
| 클리닉 | 조회→통과/되돌리기→학생 상태→예약/취소→reload | 공식 pass/undo 통과. 700행에 OMR/수동/재응시 조합과 시간 기준 확인 | [clinic-booking](../domain/clinic-booking.md), `test_clinic_target_bulk_reads.py`, frontend `student-clinic-required-cancel-realuse.spec.ts` |
| 영상 | 준비/업로드→처리→학생 재생→갱신·진도·복원 | 장시간 재생 통과. 교사 목록 오류/빈 상태 후보와 실제 버퍼링 관측 범위 확인 | [실패 은폐 후보](failure-transparency-stabilization.md), frontend `video-playback-renewal.realuse.spec.ts` |

frontend 공식 목록은 `playwright.development-release.config.ts`와
`scripts/run-development-release-canary.mjs`의 실행 계약에서 확인한다.
표에 이름이 있다고 실행됐다고 판정하지 않는다. 합성 fixture는 운영 개인정보를 복사하지 않는다.
전체 민원율·재발률·응답시간 p95는 아직 기준선 미수집이며 수치를 추정해서 쓰지 않는다.

## 6. 미검증 후보와 보존할 결정

- 첫 공개영상 GET provisioning 후보는 현재 backend에서 GET 조회/POST 준비로 이미 수리됐다.
  `test_public_session_safe_method.py`와 frontend `public-video-preparation.mock.spec.ts`가 존재한다.
  코드·기존 회귀의 확인이며 새 실사용 실행은 아니다. [현재 계약](../domain/public-video-session.md)을 따른다.
- 교사 영상 목록 API 실패가 실제 빈 목록으로 표시되는 후보는 현재 코드에도 남아 있다.
  S0 다음 첫 수리 후보로 정상0건/최초503→재시도/기존 목록 갱신 실패를 재현한다.
- [실패 은폐 후보](failure-transparency-stabilization.md)의 설정 저장 실패·이동, 활성 사용자
  복구는 최초 발견과 현재 재현을 구분한다. CI만으로 runtime 완료로 바꾸지 않는다.
- 과거 영상/OMR의 만료 `ScoreEditDraft` 삭제 차단·403 표시, playback POST 응답 단절 및
  업로드 미도달은 역사 관찰이다. 최신 재현·서버 증거 전에는 현재 장애/해결로 단정하지 않는다.
- 2026-06-07 학생 launch GO/감사 결과는 역사 자료다. 최종/임시 점수, 다자녀,
  중복 제출의 당시 미완료 조건은 현재 정책·검사에서 다시 판정한다.
- Dev Alerts Cron 설정 누락은 별도 미해결 운영 항목이다. 서비스 canary 성공과 구분한다.
  새 알림 수신처/외부 발송을 임의로 설정하지 않는다.
- 유효 HOLD는 [배포 연속성](../operations/deployment-modes.md)과 frontend
  `docs/DEPLOYMENT-OPERATIONS.md`의 현재 scope·해제 조건을 보존한다.

## 7. 인수인계와 재개

현재 배치는 `stability-foundation-0920` 소유 worktree에서 S0를 게시하고 첫 영상 오류/빈 상태 수리를 진행한다.
다른 작업자는 아래 상태와 PR/CI를 확인하고 Git branch/worktree로 소유권을 확인한다.
canonical의 기존 dirty 변경을 가져오거나 덮어쓰지 않는다.

| 항목 | 현재 상태 |
|---|---|
| 목표·허용 범위 | 장기 안정성·편의성, 필요한 리팩토링. 기존 자료·tenant/권한 보호 |
| base | backend `9ff14e4d4`, frontend `a705cb81a` |
| 변경/판단 | 기존 계획·문서 정돈, 로컬 브랜치28개 정리. 공개영상 준비는 기존 수리 확인, 목록 오류 은폐는 현재 재현 대상 |
| 검증 | frontend 문서 guard2+13 PASS(40a1062), backend lifecycle/change-risk/release-bundle 계약3개 PASS, 변경 markdown 링크40개 PASS. required CI는 게시 후 확인 |
| 배포 | S0는 문서·로컬 정리. 첫 제품 수리 착수, 아직 CI/운영 반영 완료 아님 |
| 다음 실행 | 문서 PR/CI를 게시하고 교사 영상 최초503·갱신실패·정상0건을 수정 전/후 비교. 다른4업무는 미완료 |
| 주의 | 외부 dirty 변경 보호, Windows 삭제 거부, 역사 후보와 현재 장애 구분 |

각 배치 종료 시 exact commit/PR·실행 결과·운영 반영 여부·남은 재현·다음 한 작업을
갱신한다. 같은 입력의 통과 검사는 재사용하고 기능 정책은 도메인 문서에 반영한다.
required CI와 executable 변경의 개발/운영 gate는 유지한다.
