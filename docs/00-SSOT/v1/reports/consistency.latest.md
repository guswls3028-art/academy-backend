# SSOT ↔ 실제 인프라 ↔ 합의사항 정합성

**Generated:** 2026-03-06T15:06:22.3818344+09:00
**SSOT:** docs/00-SSOT/v1/params.yaml (prod)

## 합의사항 체크리스트
| 항목 | 기대 | 실제 | 결과 |
|------|------|------|------|
| API ASG min/desired | 1/1 | 2/2 | Fix needed |
| AI ASG min/desired | 1/1 | 1/1 | PASS |
| Messaging ASG min/desired | 1/1 | 1/1 | PASS |
| Solapi 고정 IP(NAT/EIP) | 취소(불필요) | EIP 4 개 (미연결 0). Solapi 고정 IP 취소로 NAT/EIP 불필요·비용 검토 권장. | WARNING |
| 빌드 서버 | 최종 0대 목표 | 목표 달성 (0대) | PASS |

## SSOT vs Actual (일부)
| 항목 | SSOT(기대) | Actual | 일치 |
|------|-----------|--------|------|
| Messaging SQS VisibilityTimeout(초) | 900 | 900 | Yes |
| AI SQS VisibilityTimeout(초) | 1800 | 3600 | Fix needed |

**Drift 상세:** [drift.latest.md](./drift.latest.md). 이 PHASE는 read-only이며 차이는 Fix needed로만 기록.

