# architecture — 설계 결정

전체 설계 그림 + 큰 결정 기록 (ADR).

## 설계 문서

| 파일 | 용도 |
|------|------|
| [설계.md](설계.md) | 인프라/워커 전체 설계 (구성도/SQS/R2/비용) |
| [reference.md](reference.md) | 참고 자료 |
| [internal-api-allow-ips.md](internal-api-allow-ips.md) | 내부 API 허용 IP 목록 |

## ADR (Architecture Decision Records)

| 파일 | 용도 |
|------|------|
| [adr/ADR-001.md](adr/ADR-001.md) | (제목 작성 필요) |
| [adr/ADR-002.md](adr/ADR-002.md) | (제목 작성 필요) |
| [adr/ADR-003.md](adr/ADR-003.md) | (제목 작성 필요) |
| [adr/ADR-004.md](adr/ADR-004.md) | (제목 작성 필요) |
| [adr/admin-api-contract.md](adr/admin-api-contract.md) | admin API 계약서 |

## 작성 규칙
- 큰 설계 결정 = 새 ADR 추가 (ADR-{NNN}.md)
- 결정 후에는 immutable. 번복은 새 ADR 로 superseded 표시
