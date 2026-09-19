# refactor

예정 리팩토링, 백로그, migration plan을 두는 작업 대기실.

## 문서

| 파일 | 상태 | 내용 |
|------|------|------|
| [structure-reform/REFACTOR_ROADMAP.md](structure-reform/REFACTOR_ROADMAP.md) | design backlog / historical slices | 구조 설계 후보·과거 구현 기록; 현재 실행 순서는 hardening-plan |
| [structure-reform/STRUCTURE_AUDIT.md](structure-reform/STRUCTURE_AUDIT.md) | verified | 학생 중심 duplicate root 및 경계 감사 |
| [structure-reform/DOMAIN_BOUNDARIES.md](structure-reform/DOMAIN_BOUNDARIES.md) | verified/proposed | 도메인별 현재 책임과 공개 인터페이스 후보 |
| [structure-reform/DUPLICATE_ROOTS.md](structure-reform/DUPLICATE_ROOTS.md) | verified/proposed | 중복 진입점과 canonical 후보 |
| [structure-reform/PRE_PROMOTION_STRUCTURE_PLAN.md](structure-reform/PRE_PROMOTION_STRUCTURE_PLAN.md) | proposed | 운영 홍보 직전 배포·작업트리·구조조정 착수 계획 |
| [roadmap.md](roadmap.md) | historical proposal | 초기 구조 이동 제안; 현재 가드레일/실행 상태로 사용하지 않음 |
| [inventory.md](inventory.md) | historical snapshot | 2026-06-23 구조 실측·추론; 현재 수치로 재사용하지 않음 |
| [phase-0-guardrails.md](phase-0-guardrails.md) | historical proposal | 초기 안전망 설계; 현재 구현/검사는 실행 소스 확인 |
| [validation-matrix.md](validation-matrix.md) | historical proposal | 초기 검증 설계; 현재 gate는 owning workflow/운영 계약 |
| [hardening-plan.md](hardening-plan.md) | active execution / handoff | 현재 안정화·사용 편의성 단계, 실제 증거·미검증 조건, 다음 작업 |
| [failure-transparency-stabilization.md](failure-transparency-stabilization.md) | partially implemented / runtime-unverified | 자동승인·공개영상 준비 수리 근거와 남은 오류/복구 후보 |
| [student-domain-phase2-stability-audit.md](student-domain-phase2-stability-audit.md) | historical audit | 2026-06-07 증거·미완료 후보; 현재성 재검증 필요 |
| [student-domain-launch-readiness.md](student-domain-launch-readiness.md) | historical decision | 2026-06-07 GO 판단; 현재 릴리스 허가로 사용하지 않음 |
| [matchup-segmentation-risk-backlog.md](matchup-segmentation-risk-backlog.md) | proposed | 매치업 문항분리 숨은 버그·잠재 리스크와 실행 단위 |
| [exam-wrong-note-hwpx-plan.md](exam-wrong-note-hwpx-plan.md) | proposed | 시험 원본 검수·문항 정본 저장·회차 범위 학생별 HWPX 오답노트 단계와 수용 기준 |
| [backlog-student-grade-comparison.md](backlog-student-grade-comparison.md) | backlog | 학생 성적 비교 시스템 |

## 작성 규칙

- 아직 현재 동작이 아닌 계획은 여기에 둔다.
- 구현이 끝나면 관련 정본 문서(`domain/`, `architecture/`, `operations/`, `infrastructure/`)로 흡수하고 이 문서는 완료/보관 여부를 결정한다.
- 대규모 리팩토링 문서는 목표, 대상 경로, compatibility boundary, 검증 기준, rollback/cleanup 기준을 포함한다.
- 현재 실행 순서·인수인계는 `hardening-plan.md`, 구조 설계 후보는 `structure-reform/`이 소유한다. 과거 완료 기록을 현재 실행 증거로 재사용하지 않는다.
