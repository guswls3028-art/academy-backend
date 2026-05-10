# infrastructure — 인프라/배포 SSOT

배포 아키텍처 + 운영 런북. RDS Proxy 도입 (V1.1.0) 시 봉인된 SSOT 본문은 RELEASE-NOTES 에 변경 명시.

## 아키텍처

| 파일 | 용도 |
|------|------|
| [deployment-architecture.md](deployment-architecture.md) | API/Worker/RDS/SQS/R2/Cloudflare 전체 구성 (정본) |
| [infrastructure-optimization.md](infrastructure-optimization.md) | RDS Proxy / 워커 ASG / 비용 최적화 |

## Runbook (장애·운영)

| 파일 | 용도 |
|------|------|
| [runbook-deploy-checklist.md](runbook-deploy-checklist.md) | 배포 전 체크리스트 |
| [runbook-emergency-mode.md](runbook-emergency-mode.md) | 긴급 모드 |
| [runbook-incidents.md](runbook-incidents.md) | 사고 일반 대응 |
| [runbook-ops-prohibited.md](runbook-ops-prohibited.md) | 운영 금지 사항 |

## 자동 생성 보고서

| 경로 | 용도 |
|------|------|
| [../reports/ci-build.latest.md](../reports/ci-build.latest.md) | CI 빌드 digest (GitHub Actions 자동) |
| [../reports/runtime-images.latest.md](../reports/runtime-images.latest.md) | 운영 이미지 digest |
