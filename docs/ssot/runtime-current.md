# Current Production Runtime SSOT

**Verified:** 2026-08-05T04:25:00+09:00
**Scope:** Academy V1 production, AWS account `809466760795`, region `ap-northeast-2`.
**Truth sources:** AWS `describe-*` reads with profile `default`, `docs/ssot/params.yaml`, `docs/reports/drift.latest.md`, `docs/reports/resource-cleanup.latest.md`, `docs/reports/cost-waste-audit.latest.md`.

This document records the verified current runtime shape. `params.yaml` remains the executable desired-state SSOT; this file is the operator-facing current-state SSOT and must be refreshed after infra/cost/deploy changes.

## Compute Baseline

| Component | Current runtime | Cost posture |
|-----------|-----------------|--------------|
| API ASG | `academy-v1-api-asg`, `t4g.medium`, min=1 desired=1 max=3, 1 running instance | warm baseline, CPU target tracking |
| Messaging ASG | `academy-v1-messaging-worker-asg`, `t4g.small`, min=1 desired=1 max=3, 1 running instance | measured right-size; warm baseline retained for account recovery and Alimtalk latency |
| AI ASG | `academy-v1-ai-worker-asg`, `t4g.medium`, min=0 desired=0 max=5, 0 running instances | scale-to-zero |
| Tools ASG | `academy-v1-tools-worker-asg`, `t4g.small`, min=0 desired=0 max=2, 0 running instances | scale-to-zero |
| Standard Video Batch CE | `academy-v1-video-batch-ce-200gb`, `SPOT`, desired=0 max=40 vCPU, `c6g.4xlarge`/`c6g.2xlarge`/`c6g.xlarge` | video encoding burst only |
| Video Ops Batch CE | `academy-v1-video-ops-ce`, `EC2`, desired=0 max=1 vCPU, `m6g.medium` | lightweight recovery burst only |

Steady-state running EC2 in the academy VPC is only API 1 + Messaging 1. Batch-managed ASGs should have desired 0 when no Batch job is active.

## Data Stores

| Component | Current runtime |
|-----------|-----------------|
| RDS | `academy-db`, PostgreSQL `15.17`, `db.t4g.medium`, Single-AZ, 20 GB, status `available`, pending `{}`; direct-RDS API runtime uses `DB_CONN_MAX_AGE=0` |
| Redis | `academy-v1-redis-001`, Redis `7.1.0`, `cache.t4g.small`, 1 node, status `available` |

## Video Batch And Ops

| Resource | Current runtime |
|----------|-----------------|
| Standard queue | `academy-v1-video-batch-queue` |
| Standard job definition | `academy-v1-video-batch-jobdef` |
| Ops queue | `academy-v1-video-ops-queue` |
| Ops job definitions | `academy-v1-video-ops-reconcile`, `academy-v1-video-ops-scanstuck`, `academy-v1-video-ops-netprobe`, `academy-v1-video-ops-enqueue-uploaded`, `academy-v1-video-ops-purge-raw`, `academy-v1-video-ops-detect-stuck` |

## EventBridge Schedules

| Rule | Schedule | State |
|------|----------|-------|
| `academy-v1-reconcile-video-jobs` | `rate(1 hour)` | `ENABLED` |
| `academy-v1-video-scan-stuck-rate` | `rate(1 hour)` | `ENABLED` |
| `academy-v1-enqueue-uploaded-videos` | `rate(1 hour)` | `ENABLED` |
| `academy-v1-detect-stuck-videos` | `rate(30 minutes)` | `ENABLED` |
| `academy-v1-recover-dead-video-jobs` | `rate(2 hours)` | `ENABLED` |
| `academy-v1-purge-raw-videos` | `cron(0 18 * * ? *)` | `ENABLED` |
| `academy-v1-cleanup-orphan-video-storage` | `cron(0 19 ? * SAT *)` | `ENABLED` |

`enqueue_uploaded_videos` is a fallback for concurrency-limited `UPLOADED` videos, not the normal immediate enqueue path. Its current 1-hour cadence intentionally reduces ops Batch EC2 wakeups.

## Cost Guardrails

| Guardrail | Current decision |
|-----------|------------------|
| API | Keep 1 warm `t4g.medium`; do not scale to zero. |
| Messaging | Keep 1 warm `t4g.small`; 90-day CPU averaged 0.53% with a 57.41% peak, and post-change live memory had 1.24 GiB available. |
| AI/Tools | Keep min/desired 0; SQS alarms/API wake-up own burst scale-out. |
| Standard video encoding | Use AWS Batch Spot and desired 0 when idle. |
| Video ops | Keep desired 0 when idle; run hourly fallback/recovery jobs as short bursts. |
| RDS | Keep `db.t4g.medium` until connection/memory data proves another move safe. |
| Redis | Keep `cache.t4g.small`; right-size only after CPU/memory/eviction data review. |

## Verification

Latest local verification after the cost pass:

- 2026-08-05 read-only optimization audit: API/Messaging ASGs remained healthy
  at desired 1; AI/Tools remained desired 0. Over the latest 24 hours API CPU
  averaged 9.36% (5-minute maximum 61.15%), RDS CPU averaged 4.13%, and RDS
  connections averaged 2.02 with a maximum of 6. ALB response time averaged
  43ms at p50, 159ms at p95, and 453ms at p99 across populated five-minute
  windows. These measurements do not justify a runtime size, DB connection, or
  cache topology change; release-build parallelism is the lower-risk current
  optimization boundary.

- `pwsh scripts/v1/apply-messaging-rightsize.ps1 -AwsProfile default` -> Launch Template v28, instance refresh `Successful`, immutable image preserved.
- `pwsh scripts/v1/run-production-canary.ps1 -Mode PostDeploy -AwsProfile default -WriteReport` -> PASS=30 WARN=0 FAIL=0.
- `pwsh scripts/v1/run-cost-waste-audit.ps1 -AwsProfile default` -> 30/90-day usage captured; `Project` cost-allocation tag Active.
- Messaging runtime -> `t4g.small`, 1.8 GiB total, 1.24 GiB available, container 62.82 MiB, SQS visible/in-flight/DLQ all 0.
- Full deploy verification is intentionally deferred until the stale local release manifest is reconciled with the ECR-valid `origin/main` manifest; the targeted refresh and production canary are the deployment evidence for this change.
- 2026-07-30 connection-incident readback: RDS `max_connections=400`,
  `superuser_reserved_connections=3`; `/academy/api/env` version 74 applies
  `DB_CONN_MAX_AGE=0`. The rollback-protected API env refresh passed both
  `/healthz` and database-backed `/health`, and the API container's established
  PostgreSQL socket count fell from 390 to 0.
