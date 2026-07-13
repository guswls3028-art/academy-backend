# reports

검증 보고서, 감사 결과, 사고 기록. 현재 정책처럼 서술하지 않는다.

## latest

| 파일 | 생성 주체 | 갱신 |
|------|-----------|------|
| [ci-build.latest.md](ci-build.latest.md) | `.github/workflows/v1-build-and-push-latest.yml` | main push 시 |
| `release-manifest.latest.json` | 같은 workflow의 `verify-deployment` | 6개 이미지 배포·실런타임 검증 성공 후에만 |
| [production-canary.latest.md](production-canary.latest.md) | `scripts/v1/run-production-canary.ps1` | post-deploy canary 시 |
| [deploy-verification-latest.md](deploy-verification-latest.md) | `scripts/v1/run-deploy-verification.ps1` | 배포 검증 시 |
| [V1-FINAL-REPORT.md](V1-FINAL-REPORT.md) | `scripts/v1/run-deploy-verification.ps1` | 배포 검증 시 |
| [audit.latest.md](audit.latest.md) | `scripts/v1/run-deploy-verification.ps1` | 배포 검증 시 |
| [drift.latest.md](drift.latest.md) | `scripts/v1/run-deploy-verification.ps1` | 배포 검증 시 |
| [runtime-images.latest.md](runtime-images.latest.md) | `scripts/v1/run-deploy-verification.ps1` | 배포 검증 시 |
| [consistency.latest.md](consistency.latest.md) | `scripts/v1/run-deploy-verification.ps1` | 배포 검증 시 |
| [front-connection.latest.md](front-connection.latest.md) | `scripts/v1/run-deploy-verification.ps1` | 배포 검증 시 |
| [aws-resource-inventory.latest.md](aws-resource-inventory.latest.md) | `scripts/v1/run-resource-inventory.ps1` | 인프라 인벤토리 점검 시 |
| [resource-cleanup-plan.latest.md](resource-cleanup-plan.latest.md) | `scripts/v1/run-resource-inventory.ps1` | 인프라 인벤토리 점검 시 |
| [resource-cleanup.latest.md](resource-cleanup.latest.md) | `scripts/v1/run-resource-cleanup.ps1` | 리소스 정리 dry-run/execute 시 |
| [cost-waste-audit.latest.md](cost-waste-audit.latest.md) | `scripts/v1/run-cost-waste-audit.ps1` | 비용/낭비 감사 시 |

## history

[history/](history/)에는 audit/drift 스냅샷을 보관한다.

## incidents

| 파일 | 사건 |
|------|------|
| [incidents/incident-2026-03-23-db-auth-failure.md](incidents/incident-2026-03-23-db-auth-failure.md) | DB 인증 실패/connection exhaustion |

## one-off reports

| 파일 | 용도 |
|------|------|
| [structure-refactor-2026-04-13.md](structure-refactor-2026-04-13.md) | 구조 리팩터 보고서 |

## 출력 정책

- CI/script가 쓰는 `*.latest.md` 경로는 변경하지 않는다.
- 최신 보고서는 마지막 실행 결과만 포함한다.
- 과거 스냅샷과 사고 기록은 append-only로 보존한다.
