# Academy Backend

학원 관리 시스템 백엔드 (Django + DRF, multi-tenant, AWS).

## 진입점

모든 문서는 **[docs/README.md](docs/README.md)** 에서 시작.

| 용도 | 경로 |
|------|------|
| 진입 | [docs/README.md](docs/README.md) |
| 현재 버전 RELEASE-NOTES | [docs/releases/README.md](docs/releases/README.md)의 CURRENT 행 |
| 실행 파라미터 SSOT | [docs/ssot/params.yaml](docs/ssot/params.yaml) |
| 배포 아키텍처 | [docs/infrastructure/deployment-architecture.md](docs/infrastructure/deployment-architecture.md) |
| 배포 절차 | [docs/operations/배포.md](docs/operations/배포.md) |
| 정식 배포 스크립트 | [scripts/v1/deploy.ps1](scripts/v1/deploy.ps1) |

## 기술 스택

Django 4.x · DRF · PostgreSQL 15 (RDS Proxy) · Cloudflare R2 · AWS SQS · AWS Batch (video) · Docker (linux/arm64)

## 워커 분리 (절대 혼합 금지)

- **Video** (AWS Batch only; EC2 daemon/SQS path retired 2026-05-10)
- **Messaging** (SQS + EC2 t4g.small 상시)
- **AI** (SQS + EC2 ASG, scale-to-zero)

상세: [docs/architecture/설계.md](docs/architecture/설계.md)
