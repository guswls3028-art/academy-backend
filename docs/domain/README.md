# domain — 도메인 SSOT

도메인별 정책·규칙·상태머신 SSOT. 코드 ↔ 문서 일치 의무.

## 핵심 정책

| 파일 | 도메인 | 핵심 |
|------|--------|------|
| [hexagonal-cutover-policy.md](hexagonal-cutover-policy.md) | 전체 | `academy/` 헥사고날 ↔ `apps/` Django CRUD 경계 (필독) |
| [state-transitions.md](state-transitions.md) | 시험/클리닉/과제 | 5도메인 상태머신 (results=SEALED, exam-only) |
| [operations-baseline.md](operations-baseline.md) | 운영 | 멀티테넌트/인증/권한 baseline |

## 도메인별

| 파일 | 도메인 |
|------|--------|
| [messaging.md](messaging.md) | 메시징 SSOT |
| [messaging-alimtalk.md](messaging-alimtalk.md) | 알림톡 템플릿 SSOT (4종 ITEM_LIST) |
| [omr.md](omr.md) | OMR v15 인식 시스템 |
| [community.md](community.md) | 커뮤니티 보안/구조 (재설계) |
| [parent-account.md](parent-account.md) | 학부모 대리 계정 (전용 기능 X) |
| [teacher-mobile.md](teacher-mobile.md) | 선생앱 모바일 디자인 |
| [matchup.md](matchup.md) | 매치업 사용자 흐름 |

## 운영/체크리스트

| 파일 | 용도 |
|------|------|
| [billing-go-live-checklist.md](billing-go-live-checklist.md) | 결제 오픈 전 체크리스트 |
| [connection-budget.md](connection-budget.md) | RDS connection 예산 (RDS Proxy 도입 후) |
| [backlog-student-grade-comparison.md](backlog-student-grade-comparison.md) | 학생 등급 비교 백로그 |
| [incident-2026-03-23-db-auth-failure.md](incident-2026-03-23-db-auth-failure.md) | 사고 보고서 |
