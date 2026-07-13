# infrastructure

AWS/Cloudflare/RDS/SQS/R2 등 인프라 구조와 용량·비용 기준의 정본.

## 구조/용량

| 파일 | 용도 |
|------|------|
| [deployment-architecture.md](deployment-architecture.md) | API/Worker/RDS/SQS/R2/Cloudflare 전체 구성 |
| [connection-budget.md](connection-budget.md) | RDS connection 예산 |
| [video-cron-jobs.md](video-cron-jobs.md) | 영상 관련 cron/job 책임 분담 |
| [../ssot/runtime-current.md](../ssot/runtime-current.md) | 현재 운영 런타임/비용 baseline 스냅샷 |

## Historical / Reference

| 파일 | 용도 |
|------|------|
| [infrastructure-optimization.md](infrastructure-optimization.md) | historical optimization memo. 현재 구조 판단은 실행 파일과 active 인프라 문서를 우선 |

## 관련 운영 절차

절차형 runbook은 [../operations/runbooks/](../operations/runbooks/)에 둔다.

| 문서 | 용도 |
|------|------|
| [../operations/runbooks/deploy-checklist.md](../operations/runbooks/deploy-checklist.md) | 배포 전 체크리스트 |
| [../operations/runbooks/emergency-mode.md](../operations/runbooks/emergency-mode.md) | 긴급 모드 |
| [../operations/runbooks/incidents.md](../operations/runbooks/incidents.md) | 사고 일반 대응 |
| [../operations/runbooks/ops-prohibited.md](../operations/runbooks/ops-prohibited.md) | 운영 금지 사항 |

## 자동 생성 보고서

| 경로 | 용도 |
|------|------|
| [../reports/ci-build.latest.md](../reports/ci-build.latest.md) | CI 빌드 digest |
| `../reports/release-manifest.latest.json` | 마지막 완전 성공 배포의 6개 불변 digest (수동 deploy SSOT) |
| [../reports/runtime-images.latest.md](../reports/runtime-images.latest.md) | 운영 이미지 digest |
