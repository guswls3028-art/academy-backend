# V1 최종 배포 검증 보고서

**명칭:** V1 통일. **SSOT:** docs/00-SSOT/v1/params.yaml. **배포:** scripts/v1/deploy.ps1. **리전:** ap-northeast-2.

## 요약
| 항목 | 값 |
|------|-----|
| 검증 시각 | 2026-03-07T11:20:18.4011433+09:00 |
| 최종 상태 | FAIL |
| SSOT↔Actual 정합성 | **WARNING** |
| GO/NO-GO | **NO-GO** |

FAIL 항목 해결 후 재검증 필요.

## 합의사항 체크
| 항목 | 결과 |
|------|------|
| API ASG min/desired=1 | PASS |
| AI ASG min/desired=1 | PASS |
| Messaging ASG min/desired=1 | PASS |
| Solapi 고정 IP(NAT/EIP) 취소 | WARNING(EIP 잔여) |
| 빌드 (GitHub Actions only) | PASS |

## Front V1 연결
프론트를 V1 인프라(app/api 도메인, CORS, CDN/R2) 기준으로 연결한 검증 결과: **[front-connection.latest.md](./front-connection.latest.md)**

| 항목 | 결과 |
|------|------|
| app 도메인 200 |  |
| API 공개 /health | unreachable |
| CORS/Cache | not checked / - |

## 남은 WARNING 및 후속 작업
- Drift 1건 이상 시: SSOT 반영 또는 합의된 예외 문서화 후 drift.latest.md 갱신.
- EIP/NAT 잔여: Solapi 고정 IP 요구 취소에 따라 제거 검토(비용·불필요 리소스).
- [WARNING] Drift: SSOT와 불일치 1건: API LT/academy-v1-api-lt
- [FAIL] API: /health unreachable: Response status code does not indicate success: 502 (Bad Gateway).
- [FAIL] API: ALB target healthy 0 / 2
- [WARNING] API: API 공개 URL /health unreachable: https://api.hakwonplus.com/healthz — Response status code does not indicate success: 502 (Bad Gateway).

## 상세 보고서
- [deploy-verification-latest.md](./deploy-verification-latest.md) — 인프라·Smoke·프론트/R2/CDN·SQS·Video·관측·GO/NO-GO 상세
- [consistency.latest.md](./consistency.latest.md) — SSOT↔실제↔합의사항 정합성
- [front-connection.latest.md](./front-connection.latest.md) — Front V1 연결 검증·근거
- [scale-policy.latest.md](./scale-policy.latest.md) — API ASG 스케일 정책 (런칭 전 min/desired=1)
- [resource-cleanup.latest.md](./resource-cleanup.latest.md) — 리소스 정리 기록 (EIP/EBS/SG/ASG)
- [cleanup-run.latest.md](./cleanup-run.latest.md) — 정리 스크립트 실행 결과
- [front-pipeline-mapping.latest.md](./front-pipeline-mapping.latest.md) — 프론트 Git 파이프라인 ↔ SSOT 매핑
- [audit.latest.md](./audit.latest.md) — 리소스·지표 스냅샷
- [drift.latest.md](./drift.latest.md) — SSOT 대비 drift


