# Ymath 시험 채점·오답노트 전달 보고서

## 판정

**운영 전달 완료.**

Ymath의 시험 생성, 선택형 OMR, 답변형 직접 채점, 혼합형 채점, 기존
엑셀 채점표 호환, 문항 자동 분리와 학생별 오답노트 흐름을 구현하고
운영 배포 및 실제 사용자 흐름 검증까지 완료했다.

현재 동작 정본은 [시험 생성·혼합 채점·오답노트](../domain/exam-grading.md)와
[OMR 자동채점 시스템](../domain/omr.md)이다. 프론트 운영자 사용법은
[관리자 가이드](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/USER-GUIDE-ADMIN.md)가
소유한다. 이 보고서는 전달 당시의 변경·검증·운영 증거를 기록하며 현재
정책을 대체하지 않는다.

## 전달 범위

- 시험 생성 시 원본 파일, 시험명, 만점, 커트라인과 채점 방식을 함께 등록
- 선택형은 기존 OMR 자동 채점으로 연결
- 답변형은 `O / X / 0` 정오 입력 또는 문항별 부분점수 입력
- 혼합형은 선택형 OMR 결과를 보존하고 답변형만 직접 입력
- `0`은 정답·문항 만점을 유지하면서 `include_in_wrong_note=true`로 저장
- 결시는 `NOT_SUBMITTED`로 저장하고 점수·평균·석차·합불·문항 통계에서 제외
- 확인 단계는 무기록, 최종 확정은 한 transaction으로 전체 반영
- `expected_version`으로 다른 직원·탭의 동시 결과 변경을 fail-closed
- 기존 Ymath 엑셀의 `.`, 숫자 `0`, 결시 열과 다중 시트 roster를 안전하게 해석
- 오답 또는 복습 지정 문항을 학생별·강의 누적 오답노트 PDF에 포함
- PDF/이미지 원본은 문항 분리 작업으로 연결
- HWP/HWPX는 원본을 보관하되 PDF 변환을 요구하고 자동 분리 성공으로 가장하지 않음

## 원본 자료 검증

제공된 시험 자료에서 PDF 23개, 194페이지를 처리해 403개 문항 영역을
분리했고 자동 품질 경고는 0건이었다. 함께 제공된 HWP/HWPX는 운영 Linux
환경의 수식·쪽 배치 보존 한계 때문에 `conversion_required` 경로로
검증했다.

기존 Ymath 채점 엑셀은 다음 의미를 회귀 테스트로 고정했다.

| 입력 | 의미 |
|------|------|
| 문항 셀 빈칸 또는 `O` | 정답 |
| 문항 셀 `X` 또는 `.` | 오답 |
| 문항 셀 숫자 `0` | 정답이지만 오답노트에 포함 |
| 응시 여부 열 `.` | 결시 |

서로 다른 학생 집합의 여러 시트는 합치고, 같은 학생이 겹치는 후보
시트가 둘 이상이면 임의 선택하지 않고 반영을 중단한다.

## 구현 커밋과 PR

### Backend

| PR | merge commit | 내용 |
|----|--------------|------|
| [#4](https://github.com/guswls3028-art/academy-backend/pull/4) | `7ec71209c3d98adb423bd07858b8352a88f98a2f` | 시험 채점 계약, 직접 채점, 원본 분리, 오답노트·엑셀 의미 |
| [#6](https://github.com/guswls3028-art/academy-backend/pull/6) | `c9eb011fdc3ea57516d611b3f54172cec63e9d70` | 결과/시험 도메인 경계와 CI strict-touched 수정 |
| 중앙 문서 통합 | `51e4f6511` | `docs/domain/exam-grading.md` 및 OMR 문서 연결 |

### Frontend

| PR | merge commit | 내용 |
|----|--------------|------|
| [#4](https://github.com/guswls3028-art/academy-frontend/pull/4) | `6f9f7c7bf7db1a5df88f8b1cc6dfd6c8262d9832` | 시험지로 만들기, 모드별 CTA, 직접 채점 표, 오답노트 복습 표시 |
| [#6](https://github.com/guswls3028-art/academy-frontend/pull/6) | `a04a01c1c5ee1ff017ec7d692442482bd8deb369` | 새 시험 생성 라벨에 실사용 E2E 계약 정렬 |
| [#7](https://github.com/guswls3028-art/academy-frontend/pull/7) | `8cf7f9d2a156e70636f8ae8cca78c0ef9cbb6978` | 관리자 사용 가이드. `[skip ci]` 문서 병합으로 운영 재배포 없음 |

위 기능 merge commit은 각 저장소 `origin/main`의 조상임을 확인했다.

## 검증 결과

### Backend

- Ymath/수동 채점/원본 분리 집중 테스트 52개 통과
- 제출·결과 회귀 테스트 44개 통과
- 도메인 경계 수정 후 집중 테스트 9개 통과
- `manage.py check` 통과
- migration dry-run 통과
- 변경 파일 Ruff 통과
- `refactor_boundary_snapshot.py --strict-touched` 통과
- PDF, HWP 변환 안내, 잠긴 시험 보호, `0` 복습 의미, 부분점수,
  혼합형 OMR 보존, stale version 거부, 다중 시트와 tenant 차단 검증

### Frontend

- TypeScript typecheck 통과
- 변경 파일 strict ESLint 통과
- production Vite build 통과
- bundle budget 494개 JavaScript asset 통과
- 생성 모드 전환, `O/X/0` 키보드 입력, 미리보기 무기록,
  최종 확정과 오류 상태 로컬 실사용 검증 통과
- 운영 로그인 smoke, 모든 tenant 공개 화면, 10개 왕복 흐름,
  차시 성적·시험·과제 실사용 게이트 통과

광범위 PR E2E 1,026개는 단일 worker 120분 제한으로 완료되지 않았다.
산출물에는 누락된 별도 tenant 계정, 429 제한, 기존 메시징·문서·선택자
실패가 섞여 있었다. 이 결과를 성공으로 기록하지 않았으며, 배포 차단
게이트와 Ymath 관련 실사용 검증을 별도로 전부 통과시켰다.

## 운영 배포 증거

### Backend

- Workflow:
  [30461184505](https://github.com/guswls3028-art/academy-backend/actions/runs/30461184505)
- 배포 revision: `5c58e9a9334b9f89b7a24de13550b2c48073d208`
- release tag:
  `sha-5c58e9a9334b9f89b7a24de13550b2c48073d208-run-30461184505-1`
- API digest:
  `sha256:1a984901c407b73e7326cd900659476452a92163bf427ff9c75c81814101cdff`
- AI worker digest:
  `sha256:7eeb5d7b5a68f0aea000073d031421ed7ab478790589a68aeb4c57a07ab7c5bb`
- Tools worker digest:
  `sha256:69ac149ccae90ebb078b6be9e035431a78191e6c31b36c846cf0c3cb4091fca2`

같은 digest가 상시 격리 개발 런타임, 임시 preprod와 운영에서
검증됐다. 개발 migration·운영 자원 접근 거부·Excel/PPT/R2 smoke,
preprod migration·DB 경계·health·image identity, 운영 migration,
ASG/ALB health-gated refresh와 최종 runtime digest 확인이 모두
통과했다. 임시 preprod EC2 종료도 확인됐다.

### Frontend

- Workflow:
  [30466665819](https://github.com/guswls3028-art/academy-frontend/actions/runs/30466665819)
- 운영 revision: `a04a01c1c5ee1ff017ec7d692442482bd8deb369`
- 품질 검사, Cloudflare Pages 배포, 로그인·tenant·왕복·차시 시험
  실사용 검증 통과

후속 문서 병합은 `[skip ci]`로 처리해 이미 검증된 운영 revision을
중복 배포하지 않았다.

## 최종 운영 확인

2026-07-30 KST 마감 확인:

- `https://hakwonplus.com/version.json`
  → `a04a01c1c5ee1ff017ec7d692442482bd8deb369`
- `https://api.hakwonplus.com/healthz` → HTTP 200
- `https://api.hakwonplus.com/health`
  → `status=healthy`, `database=connected`
- 학생·성적·오답노트의 canonical 결과 경로는 별도 Ymath 전용 저장소를
  만들지 않고 기존 통계·진행도 경로를 그대로 사용

## 남은 제약과 운영 안내

- HWP/HWPX는 PDF로 저장해 다시 올려야 문항 자동 분리가 시작된다.
- 새 선생님에게는 자동 분리를 위해 원본 시험지를 PDF 또는 선명한
  페이지 이미지로 제공하도록 안내한다.
- 자동 분리는 문항 이미지를 제안·연결하는 시작점이다. 문항 번호,
  유형, 배점과 이미지가 맞는지 답안 설정에서 확인한 뒤 채점한다.
- 새로운 시험지 형식이 추가되면 실패 자료를 보존하고 corpus 회귀
  검증에 추가한다. 기존 문항·성적이 있는 운영 시험을 자동으로 다시
  자르지 않는다.
- 광범위 E2E 전체 완료 시간과 기존 환경 의존 실패는 Ymath 전달과
  분리된 테스트 인프라 개선 항목으로 남는다.

## 작업공간 보존

공유 `C:\academy\backend`, `C:\academy\frontend`의 기존 대규모
미커밋 변경은 정리·덮어쓰기하지 않았다. 구현·검증·문서화는 아래 격리
worktree와 명시적 브랜치/커밋에서 수행했다.

- `C:\academy\_artifacts\ymath-release-backend-20260729`
- `C:\academy\_artifacts\ymath-release-frontend-20260729`

중앙 검토를 위해 구현 및 문서 브랜치와 격리 worktree는 보존한다.
