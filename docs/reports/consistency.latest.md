# SSOT ↔ 실제 인프라 ↔ 합의사항 정합성

**Generated:** 2026-06-24T07:58:44.6510107+09:00
**SSOT:** docs/ssot/params.yaml (prod)

## 합의사항 체크리스트
| 항목 | 기대 | 실제 | 결과 |
|------|------|------|------|
| API ASG min/desired | 2/2 | 2/2 | PASS |
| AI ASG min/desired | 0/0 | 0/0 | PASS |
| Messaging ASG min/desired | 1/1 | 1/1 | PASS |
| Solapi 고정 IP(NAT/EIP) | 취소(불필요) | NAT Gateway 0개, EIP 3 개 (미연결 0). 연결된 EIP는 활성 리소스 소유로 Solapi 정리 후보 아님. | PASS |
| 빌드 서버 | 사용하지 않음(0대) | 정상 (빌드 서버 없음, GitHub Actions only) | PASS |

## SSOT vs Actual (일부)
| 항목 | SSOT(기대) | Actual | 일치 |
|------|-----------|--------|------|
| Messaging SQS VisibilityTimeout(초) | 900 | 900 | Yes |
| AI SQS VisibilityTimeout(초) | 1800 | 1800 | Yes |

**Drift 상세:** [drift.latest.md](./drift.latest.md). 이 PHASE는 read-only이며 차이는 Fix needed로만 기록.
