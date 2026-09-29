# 2026-09-20 안정화 선행 정리

상태: 로컬 Git 정리·문서 현재성 조사 완료. 캐시 영구 삭제는 정책상 차단되어 보존.
제품 기능 수리와 배포 상태는 [현재 계획](../../refactor/hardening-plan.md)이 소유한다.

## 범위와 결과

- base: backend9ff14e4d4 / frontenda705cb81a. 공유 main 이력은 재작성하지 않았다.
- main에 완전 병합됐고 등록 worktree에서 사용하지 않는 로컬 codex 브랜치28개(backend5/frontend23)를 정리했다.
  아래 ref/SHA를 기록하고 각 삭제 직전 ancestry와 비사용을 다시 확인했다.
  expected-SHA `git update-ref -d` 이후 부재를 확인했다. 커밋은 origin/main에서 계속 도달 가능하다.
  원격 브랜치·등록 worktree·미반영 변경은 이 정리에 포함하지 않았다.
- 작업 시작 당시 등록 worktree257개 중 dirty24였다. 이번 소유2개 외의 작업공간은 보존했다.
  canonical backend의 production-canary.latest.md 변경과 frontend bookingRange.ts 미추적 파일도 보존했다.
- 현재 하드닝 계획의 낡은 수치·운영 쓰기 QA 안내를 현행 소유 절차와 확인된2026-09-20 기준으로 교체했다.
  6월 launch GO 및 구조 개편 제안은 역사/설계 후보로 구분하고 미완료 조건은 유지했다.
  frontend 배포 인덱스·실사용 suite·worker 안내를 실행 소스에 맞췄고 유효HOLD는 보존했다.
- backend의 동일 blob 문서26그룹은 audit/release 시점별 기록과 latest 사본이었다.
  내용 일치만으로 불필요하다고 판단하지 않아 append-only 증거를 보존했다.
- 생성 캐시2개(.pytest_cache/.ruff_cache),8파일,962bytes 삭제 요청은 실행 전 자동 승인 검토에서
  `blocked by policy`로 거부됐다. 상세 사유는 제공되지 않았다. 삭제0, 두 경로 존재를 재확인했다.
- 이전 작업의 Windows 잔여 frontend 보관물40,861파일,755,322,528 logical bytes는 보존한다.
  node_modules에는 junction이 있어 외부 대상을 따라 삭제하지 않는다. 현재 물리 디스크 회수량은 보고하지 않는다.
- .secrets, materials, 사용자 산출물, dnfm/dnfm-group은 정리하지 않았다.

## 삭제한 로컬 참조와 복구점

커밋은 보존돼 있다. 필요할 때 해당 저장소의 main ancestry를 확인하고 아래 SHA에서
로컬 브랜치를 다시 만들 수 있다. 작업공간을 새로 만드는 경우 현재 세션 소유 절차를 따른다.

| 저장소 | 삭제한 ref | 보존 SHA |
|---|---|---|
| backend | refs/heads/codex/limglish-tenant-alimtalk-20260906-backend-20260906-190504 | a8e606e7fef75df543f3f9c495633f959fc29a02 |
| backend | refs/heads/codex/parent-account-explicit-password-backend-20260910-233452 | 6c1430f74659e0ce27cc3b4bd5d4e508cb9a1d38 |
| backend | refs/heads/codex/parent-student-parity-0906-backend-20260906-230806 | 851119e87fb8064e2a0b6565817bf68812ff95b1 |
| backend | refs/heads/codex/student-import-followup-20260823-backend-20260823-145302 | 2206f867ca9426bff0069c7a1e1452cc389bfdf3 |
| backend | refs/heads/codex/ymath-fixture-membership-fix-20260822 | 90ad057a96f8192a367f11a06351470302fa2c1e |
| frontend | refs/heads/codex/assessment-residual-risk-frontend-20260821-224743 | 28e5b7aad09eab0bec6b785e0445712be3f9bb62 |
| frontend | refs/heads/codex/canary-outage-incident-note-0916 | f1a8a653c4fbbbbdbe86fabf853bafa55abc51fb |
| frontend | refs/heads/codex/canary-retrigger-0917 | 8b9ccdb03b853c92e0533477f1023e1c3b665622 |
| frontend | refs/heads/codex/clinic-range-availability-0904-frontend-20260904-035504 | b68f22a8848048f4afd23ae5f3bc60296af36b0f |
| frontend | refs/heads/codex/clinic-workbench-0904-frontend-20260904-004128 | 7dc6a659e4f2b8f211b4ffb1b1e23ab0555f94ed |
| frontend | refs/heads/codex/exam-insights-hardening-01a026d6-frontend-20260822-162002 | 701d54d48d15b252e48565bd857d7bd865b4cda6 |
| frontend | refs/heads/codex/frontend-dev-canary-diagnosis-0907-frontend-20260907-002326 | 5a558318230391619c933edd4fa1d27fd2eefd16 |
| frontend | refs/heads/codex/frontend-development-canary-timeout-20260911 | 6ab8cdb7e2a537f4d0e541880ae9886ffd99d05c |
| frontend | refs/heads/codex/ios-safari14-login-0906-frontend-20260906-171939 | a6d18d9d3da8461d204fb46009bb5f86d503abfc |
| frontend | refs/heads/codex/materials-attachment-regression-20260829-frontend-20260829-215207 | 17b659bc23f087234afda4cbb7aff85ca390f928 |
| frontend | refs/heads/codex/omr-registration-notice-text-0916 | 3ebb41d57fb75c14762e3ac3199fc64061102689 |
| frontend | refs/heads/codex/omr-score-draft-lease-cleanup-0915 | 964f2acfd67a7c1680dfdc6f87ca55a1239ed5c4 |
| frontend | refs/heads/codex/parent-account-explicit-password-frontend-20260910-233452 | 45961774b1d8cf01c752544dec4b58403f12d8d4 |
| frontend | refs/heads/codex/parent-receipt-redacted-target-20260911 | bdb5b6a230df183f18d4742966f308e11a3ac023 |
| frontend | refs/heads/codex/payroll497-p1-fix-frontend-20260910-225353 | bdaab53ea6dca91318c24905bb30406dc94f96e7 |
| frontend | refs/heads/codex/realuse-transport-replay-0915-frontend-20260915-183023 | 60396f91887d6cdfb4d7bbe63aae235104ef5cf9 |
| frontend | refs/heads/codex/sms-alimtalk-only-audit-frontend-20260820-034309 | effbad34780cb2959729db6ea5377e64333b9cff |
| frontend | refs/heads/codex/staff-feature-audit-01a075a6-frontend-20260906-164158 | 0f7e5f67846f679d9a73443bf7b0e1965a4ccde4 |
| frontend | refs/heads/codex/student-parent-canary-debug-0909-frontend-20260909-161933 | 491e08d6a926886110b97aee4c6dd5c9890dacbf |
| frontend | refs/heads/codex/student-support-preview-frontend-20260822-011031 | 0dc1881d524ca637a60168b85406f2d3d7ced409 |
| frontend | refs/heads/codex/tools-timer-contrast-size-frontend-20260830-092306 | 67e1ee2b091ccc6d0093dbe19c1c96b3c735494e |
| frontend | refs/heads/codex/video-renewal-seal-0908-frontend-20260908-232228 | 4c1f9303f1052ba4654098dea97c08b222ecd216 |
| frontend | refs/heads/codex/ymath-quality-0811-frontend-20260812-cleanup | 31d8825c7c68ac7d8a3a2d246faf0776d85e614b |
## 증거와 한계

로컬 세부 산출물: C:\academy\_artifacts\stability-foundation-0920 의
worktree-inspect.log, deleted-local-branches.json, cache-cleanup-result.json,
backend-identical-doc-blobs.json. 원자료에 사용자 내용을 추가하지 않았다.
이 보고서는 안정화 프로그램 전체 완료나 모든 외부 작업의 안전한 폐기를 선언하지 않는다.
