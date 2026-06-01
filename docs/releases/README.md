# releases — 버전별 RELEASE-NOTES

봉인된 버전별 변경 모음. **append-only.** 봉인 후 수정 금지.

## 버전 정책 (Va.b.c)

| 세그먼트 | 의미 | 변경 조건 |
|----------|------|-----------|
| **a** | 프로젝트 버전 | 프로젝트 철학·구조 근본 변경. 사실상 1 유지 |
| **b** | 인프라 버전 | AWS 인프라 구조 변경 시만 |
| **c** | 패치 버전 | 기능 추가/버그 수정/UI 개선 |

## 활성

| 버전 | 상태 | 봉인 시점 | 변경 |
|------|------|-----------|------|
| [v1.2.22.md](v1.2.22.md) | **CURRENT** | 2026-06-02 KST | OMR objective-only decorative essay area + static sheet production route proof |
| [v1.2.21.md](v1.2.21.md) | production-deployed / v1.2.22로 승계 | 2026-06-02 KST | multi-domain hidden failure hardening + production verification closure |
| [v1.2.20.md](v1.2.20.md) | production-deployed / v1.2.21로 승계 | 2026-06-02 KST | OMR v2 sheet contract pipeline refactor |
| [v1.2.19.md](v1.2.19.md) | production-deployed / v1.2.20로 승계 | 2026-06-02 KST | OMR custom sheet shape matrix hardening |
| [v1.2.18.md](v1.2.18.md) | production-deployed / v1.2.19로 승계 | 2026-06-02 KST | OMR objective multi-mark exact grading + manual score hardening |
| [v1.2.17.md](v1.2.17.md) | production-deployed / v1.2.18로 승계 | 2026-06-01 KST | OMR mixed objective/essay sheet shape + tenant 2 real grading verification |
| [v1.2.16.md](v1.2.16.md) | production-deployed / v1.2.17로 승계 | 2026-06-01 KST | OMR fact/readiness architecture + zero-answer grading prevention |
| [v1.2.15.md](v1.2.15.md) | production-deployed / v1.2.16로 승계 | 2026-06-01 KST | OMR late AI answer auto-recovery in the existing 5-minute recovery job |
| [v1.2.14.md](v1.2.14.md) | production-deployed / v1.2.15로 승계 | 2026-06-01 KST | tchul OMR late-result recovery: hydrate AI answers after manual student matching + regrade affected submissions |
| [v1.2.13.md](v1.2.13.md) | production-deployed / v1.2.14로 승계 | 2026-05-30 KST | tchul QnA incident: show student attachment images inline + owner-only QnA alerts + 7 more unified alimtalk triggers + clinic_cancelled on session destroy |
| [v1.2.12.md](v1.2.12.md) | production-deployed / v1.2.13로 승계 | 2026-05-30 KST | OMR same-student duplicate cluster UX + OMR pipeline domain split (Phases A–G) + state recovery cron |
| [v1.2.11.md](v1.2.11.md) | production-deployed / v1.2.12로 승계 | 2026-05-24 KST | HJ3 OMR production grading hardening + student launch surface polish |
| [v1.2.10.md](v1.2.10.md) | production-deployed / v1.2.11로 승계 | 2026-05-23 KST | Clinic participant write SSOT + student clinic change UX contract |
| [v1.2.9.md](v1.2.9.md) | production-deployed / v1.2.10로 승계 | 2026-05-23 KST | Clinic participant transition SSOT + clinic operations mobile QA |
| [v1.2.8.md](v1.2.8.md) | production-deployed / v1.2.9로 승계 | 2026-05-23 KST | Attendance roster write SSOT + local PG-only concurrency test guard |
| [v1.2.7.md](v1.2.7.md) | production-deployed / v1.2.8로 승계 | 2026-05-23 KST | JSON student bulk/create-conflict orchestration convergence |
| [v1.2.6.md](v1.2.6.md) | production-deployed / v1.2.7로 승계, 단말 본문 확인 선택 대기 | 2026-05-23 KST | Excel student import orchestration SSOT + teacher import UX + 운영 QA 하드닝 |
| [v1.2.5.md](v1.2.5.md) | production-deployed / v1.2.6로 승계, 단말 본문 확인 선택 대기 | 2026-05-23 KST | 가입신청 승인 orchestration SSOT + PostgreSQL row-lock 운영 QA 수정 |
| [v1.2.4.md](v1.2.4.md) | production-deployed / v1.2.5로 승계, 단말 본문 확인 선택 대기 | 2026-05-23 KST | 학생 생성 계정 그래프 SSOT + Excel 환영알림 토글 + 모바일 생성 UX |
| [v1.2.3.md](v1.2.3.md) | production-deployed / v1.2.4로 승계, 계정복구 단말 본문 확인 대기 | 2026-05-23 KST | 학생 lifecycle 영구삭제 수렴 + 복원 skip UX + 계정복구 임시 비밀번호 6자리 |
| [v1.2.2.md](v1.2.2.md) | 봉인 | 2026-05-22 | 테넌트/로그인/계정복구 SSOT + 민감정보 로그 차단 + 문서 구조 정렬 |
| [v1.2.1.md](v1.2.1.md) | 봉인 | 2026-05-13 | 매치업 safety 아키텍처 + 커뮤니티 전면 + 랜딩 공개 + CDN Worker |
| [v1.2.0.md](v1.2.0.md) | 봉인 (+§22b ext.) | 2026-04-30 | 매치업 신규 도메인 + RDS Proxy + 헥사고날 컷오버 |
| [v1.1.1.md](v1.1.1.md) | 봉인 | 2026-03-17 | 도메인 SSOT (메시징/OMR/커뮤니티/운영정책) |
| [v1.1.0.md](v1.1.0.md) | 봉인 | - | 인프라 SSOT (배포 아키텍처/runbook 4종) |

## 아카이브 (참고용, 운영 기준 X)

| 버전 | 변경 |
|------|------|
| [archive/v1.0.3.md](archive/v1.0.3.md) | Video 인프라 하드닝 (daemon/batch) |
| [archive/v1.0.3-video-infrastructure.md](archive/v1.0.3-video-infrastructure.md) | v1.0.3 video infrastructure 보충 |

> v1.0.0~v1.0.2, v3, v4, legacy_reports 는 2026-04-10 정리 시 삭제됨.

## 작성 규칙

- 새 봉인 = `v{a}.{b}.{c}.md` 추가 + README 표에 추가
- 봉인 시점부터 immutable
- 도메인/인프라 SSOT 본문 변경은 RELEASE-NOTES 에 명시 (본문은 `domain/`/`infrastructure/` 안에 직접 갱신)
