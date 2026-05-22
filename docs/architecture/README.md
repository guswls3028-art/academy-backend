# architecture

레이어 경계, 모듈 배치, 큰 설계 결정의 정본.

## 현재 설계/참조

| 파일 | 용도 |
|------|------|
| [hexagonal-cutover-policy.md](hexagonal-cutover-policy.md) | `academy/` 헥사고날 ↔ `apps/` Django CRUD 경계 |
| [설계.md](설계.md) | legacy 인프라/워커 스케치. 현재 배포/영상 진실은 `../infrastructure/deployment-architecture.md`와 `../operations/runbooks/video-batch.md`를 우선 |
| [reference.md](reference.md) | backend 구조 참조 |
| [internal-api-allow-ips.md](internal-api-allow-ips.md) | 내부 API 허용 IP 목록 |

## ADR

| 파일 | 용도 |
|------|------|
| [adr/ADR-001.md](adr/ADR-001.md) | ADR 001 |
| [adr/ADR-002.md](adr/ADR-002.md) | ADR 002 |
| [adr/ADR-003.md](adr/ADR-003.md) | ADR 003 |
| [adr/ADR-004.md](adr/ADR-004.md) | ADR 004 |
| [adr/admin-api-contract.md](adr/admin-api-contract.md) | admin API 계약 |

## 작성 규칙

- 레이어 책임, 의존 방향, 코드 배치 정책은 여기 둔다.
- 도메인 비즈니스 규칙은 `../domain/`에 둔다.
- 큰 결정은 ADR로 남기고, 번복은 기존 ADR 수정이 아니라 새 ADR에서 superseded 처리한다.
