# Current Production Runtime SSOT

**Verified:** 2026-08-20T13:34:28+09:00
**Scope:** Academy V1 production, AWS account `809466760795`, region `ap-northeast-2`.
**Truth sources:** AWS `describe-*` reads with profile `default`, `docs/ssot/params.yaml`, `docs/reports/drift.latest.md`, `docs/reports/resource-cleanup.latest.md`, `docs/reports/cost-waste-audit.latest.md`.

This document records the verified current runtime shape. `params.yaml` remains the executable desired-state SSOT; this file is the operator-facing current-state SSOT and must be refreshed after infra/cost/deploy changes.

## Compute Baseline

| Component | Current runtime | Cost posture |
|-----------|-----------------|--------------|
| API ASG | `academy-v1-api-asg`, `t4g.medium`, min=1 desired=1 max=3, 1 running instance | warm baseline, CPU target tracking |
| Messaging ASG | `academy-v1-messaging-worker-asg`, `t4g.small`, min=1 desired=1 max=3, 1 running instance | measured right-size; warm baseline retained for account recovery and Alimtalk latency |
| AI ASG | `academy-v1-ai-worker-asg`, `t4g.medium`, min=1 desired=1 max=5, 1 running instance | warm baseline for student import and AI wait paths; SQS burst scale-out |
| Tools ASG | `academy-v1-tools-worker-asg`, `t4g.small`, min=1 desired=1 max=2, 1 running instance | warm baseline for document conversion wait paths; SQS burst scale-out |
| Standard Video Batch CE | `academy-v1-video-batch-ce-200gb`, `SPOT`, desired=0 max=40 vCPU, `c6g.4xlarge`/`c6g.2xlarge`/`c6g.xlarge` | video encoding burst only |
| Video Ops Batch CE | `academy-v1-video-ops-ce`, `EC2`, desired=0 max=1 vCPU, `m6g.medium` | lightweight recovery burst only |

Steady-state running EC2 in the academy VPC is API 1 + Messaging 1 + AI 1 +
Tools 1, plus exactly one termination-protected, SSM-only persistent development
instance. Batch-managed ASGs should have desired 0 when no Batch job is active.

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
| AI/Tools | Keep min/desired 1; user-facing first work stays warm and SQS alarms own burst scale-out above the baseline. |
| Standard video encoding | Use AWS Batch Spot and desired 0 when idle. |
| Video ops | Keep desired 0 when idle; run hourly fallback/recovery jobs as short bursts. |
| RDS | Keep `db.t4g.medium` until connection/memory data proves another move safe. |
| Redis | Keep `cache.t4g.small`; right-size only after CPU/memory/eviction data review. |

## Verification

Latest verification after the runtime-audit hardening release:

- GitHub run `32330381855`, source
  `fc4f748dfbf47575eb9424ee301f6771c47116de`, passed immutable builds, exact
  ECR scan identity, persistent development, isolated preprod, migration,
  launch-before-terminate API/worker refreshes, runtime digest verification,
  Video Batch verification, and successful release-manifest promotion.
- `docs/reports/release-manifest.latest.json` is `complete=true`,
  `status=successful`, and records the same source SHA and run-bound image tag.
- `pwsh scripts/v1/run-production-canary.ps1 -Mode PostDeploy -AwsProfile default -StrictWarnings -WriteReport`
  -> `PASS=30 WARN=0 FAIL=0`; API/worker ASGs were 1/1 healthy, ALB 1/1,
  RDS/Redis available, all three queues and DLQs empty, Batch valid, Django
  checks/migrations/invariants passed, and explicit production E2E residue was 0.
- `pwsh scripts/v1/run-cost-waste-audit.ps1 -AwsProfile default -PythonExecutable <venv-python>`
  -> 30/90-day usage captured, warm-baseline SSOT matched AWS, exact persistent
  development and dynamic Batch runtimes excluded from orphan candidates,
  ECR/Batch cleanup candidates were 0, and no immediate deletion or downsize
  target remained.
- 2026-07-30 connection-incident readback: RDS `max_connections=400`,
  `superuser_reserved_connections=3`; `/academy/api/env` version 74 applies
  `DB_CONN_MAX_AGE=0`. The rollback-protected API env refresh passed both
  `/healthz` and database-backed `/health`, and the API container's established
  PostgreSQL socket count fell from 390 to 0.
