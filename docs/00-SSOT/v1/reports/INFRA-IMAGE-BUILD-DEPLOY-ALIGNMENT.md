# 이미지 빌드(GitHub Actions) ↔ 풀배포(deploy.ps1) 정합 및 인프라 최신화

**갱신:** 2026-03-11  
**SSOT:** docs/00-SSOT/v1/params.yaml

---

## 1. 정합 요약

| 구분 | 내용 |
|------|------|
| **이미지 빌드** | GitHub Actions `v1-build-and-push-latest.yml` — 5개 이미지(academy-base, academy-api, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu) **:latest** 푸시 후 API ASG instance refresh |
| **풀배포** | `scripts/v1/deploy.ps1` — ECR **:latest** 사용(Bootstrap·JobDef·API LT). 빌드 서버 미사용, 이미지는 CI에서만 푸시 |
| **SSOT 정합** | `ecr.useLatestTag: true`, `ecr.immutableTagRequired: false` — CI가 :latest만 푸시하므로 풀배포도 :latest 사용으로 일치 |

---

## 2. 검증 완료 상태 (2026-03-11)

- **풀배포:** deploy.ps1 실행 완료 (Sync env, API instance refresh, JobDef :latest 반영)
- **API:** ALB 타깃 1개 이상 healthy, /healthz 200 (공개 URL·ALB 직접)
- **워커·연결:** SSM VIDEO_BATCH_* 일치, Batch CE/Queue/JobDef 존재, Messaging/AI ASG 1대 each, Redis available, DLQ 0
- **배포 검증:** run-deploy-verification.ps1 — CONDITIONAL GO (WARNING: API LT drift 1건, 배포 반영으로 정상)

---

## 3. 인프라 정보 (SSOT 기준·검증 반영)

- **리전:** ap-northeast-2
- **API ASG:** academy-v1-api-asg (min=1, desired=1, max=2), LT academy-v1-api-lt
- **ALB/TG:** academy-v1-api-alb, academy-v1-api-tg, health /healthz
- **워커 ASG:** academy-v1-messaging-worker-asg, academy-v1-ai-worker-asg (각 desired=1)
- **Batch:** academy-v1-video-batch-ce, academy-v1-video-batch-long-ce, academy-v1-video-ops-ce / 해당 큐·JobDef (이미지 academy-video-worker:latest)
- **Redis:** academy-v1-redis (available)
- **RDS:** academy-db
- **SSM:** /academy/api/env, /academy/workers/env

상세 수치는 `reports/audit.latest.md`, `reports/drift.latest.md` 참고.

---

## 4. 참고

- **이미지 빌드는 GitHub Actions로만 수행.** 룰 `07_deployment_orchestrator.mdc`, Runbook §0.
- **풀배포 후 연결 검증:** `pwsh -File scripts/v1/verify-video-batch-connection.ps1`, `pwsh -File scripts/v1/run-deploy-verification.ps1 -AwsProfile default`
