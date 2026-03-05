# .cursor/knowledge — Academy 인프라 지식

이 폴더는 Cursor 규칙·에이전트·프롬프트가 공통으로 참조하는 **인프라 지식**을 담습니다.

## 폴더 구조 (SSOT·스크립트)

- **정식:** docs/00-SSOT/v1, scripts/v1 만 수정·실행·참조.
- **아카이브/레거시:** docs/00-SSOT/archive, scripts/archive — 참고용만. 수정/실행 금지. 상세는 규칙 `00_ssot_v1_archive_structure.mdc` 참조.

## 필수 참조 파일

| 파일 | 용도 | 참조 키워드 |
|------|------|--------------|
| **infra_topology.yaml** | 서비스·큐·스토리지·DB·CDN·연결 관계의 **단일 소스** | `infra topology`, `canonical topology`, `infra_topology.yaml` |

- **아키텍처/인프라 설계·변경 시:** 반드시 `.cursor/knowledge/infra_topology.yaml` 을 먼저 읽고, 이 파일에 정의된 서비스·연결만 사용한다.
- 이 파일에 없는 서비스(예: ECS, EKS, S3)는 도입하지 않는다.

## 기타 지식

- **aws_patterns.md** — 허용/금지 AWS 서비스 요약
- **scaling_playbook.md** — 스케일링 신호·한계·액션
- **cost_playbook.md** — 인스턴스 타입·비용 전략
- **incident_playbook.md** — 장애 유형별 대응

위 playbook들은 topology와 일치하도록 유지한다.
