# V1 최종 배포 검증 보고서

**명칭:** V1 통일. **SSOT:** docs/ssot/params.yaml. **배포:** scripts/v1/deploy.ps1. **리전:** ap-northeast-2.

## 요약
| 항목 | 값 |
|------|-----|
| 검증 시각 | 2026-06-10T14:37:52.3456792+09:00 |
| 최종 상태 | PASS |
| SSOT↔Actual 정합성 | **PASS** |
| GO/NO-GO | **GO** |



## 합의사항 체크
| 항목 | 결과 |
|------|------|
| API ASG min/desired=1/1 | PASS |
| AI ASG min/desired=0/0 | PASS |
| Messaging ASG min/desired=1/1 | PASS |
| Solapi 고정 IP(NAT/EIP) 취소 | PASS |
| 빌드 (GitHub Actions only) | PASS |

## Front V1 연결
프론트를 V1 인프라(app/api 도메인, CORS, CDN/R2) 기준으로 연결한 검증 결과: **[front-connection.latest.md](./front-connection.latest.md)**

| 항목 | 결과 |
|------|------|
| app 도메인 200 | PASS |
| API 공개 /health | OK |
| CORS/Cache | OK / 1y |

## 남은 WARNING 및 후속 작업
- Drift 1건 이상 시: SSOT 반영 또는 합의된 예외 문서화 후 drift.latest.md 갱신.
- (현재 리스크 없음)

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
