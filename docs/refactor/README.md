# refactor

예정 리팩토링, 백로그, migration plan을 두는 작업 대기실.

## 문서

| 파일 | 상태 | 내용 |
|------|------|------|
| [roadmap.md](roadmap.md) | proposed | 대규모 구조 리팩토링 실행 로드맵 |
| [inventory.md](inventory.md) | verified/proposed | 현재 구조 실측과 병목 inventory |
| [phase-0-guardrails.md](phase-0-guardrails.md) | proposed | 코드 이동 전 안전망 구축 계획 |
| [validation-matrix.md](validation-matrix.md) | proposed | 단계별 검증 매트릭스 |
| [backlog-student-grade-comparison.md](backlog-student-grade-comparison.md) | backlog | 학생 성적 비교 시스템 |

## 작성 규칙

- 아직 현재 동작이 아닌 계획은 여기에 둔다.
- 구현이 끝나면 관련 정본 문서(`domain/`, `architecture/`, `operations/`, `infrastructure/`)로 흡수하고 이 문서는 완료/보관 여부를 결정한다.
- 대규모 리팩토링 문서는 목표, 대상 경로, compatibility boundary, 검증 기준, rollback/cleanup 기준을 포함한다.
