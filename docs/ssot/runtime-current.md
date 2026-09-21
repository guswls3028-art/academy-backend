# Current Production Runtime SSOT

**Infrastructure snapshot verified:** 2026-08-20T13:34:28+09:00
**Scope:** Academy V1 production, AWS account `809466760795`, region `ap-northeast-2`.
**Truth sources:** AWS `describe-*` reads with profile `default`, `docs/ssot/params.yaml`, `docs/reports/drift.latest.md`, `docs/reports/resource-cleanup.latest.md`, `docs/reports/cost-waste-audit.latest.md`.

This document records the dated infrastructure snapshot below and separately identifies
deployment evidence. `params.yaml` remains the executable desired-state SSOT. Fresh AWS
readback takes precedence over this snapshot; a later application deployment does not
reverify every inventory or cost figure below.

## Deployment Identity

The [successful release manifest](../reports/release-manifest.latest.json) owns backend
source SHA, immutable service digests, release status and verification time. Each frontend
production domain's `/version.json` owns its built revision. The release notes' `CURRENT`
label identifies the last sealed notes, not an independently verified live build.

On 2026-09-21 KST, stabilization rollout
[35540757110](https://github.com/guswls3028-art/academy-backend/actions/runs/35540757110)
completed successfully, source `057403c653f4c5f26fc43dc524b974895a4ba432`.
All six new immutable images passed ECR with critical=0, acceptedCritical=0 and high=0.
Persistent isolated development Excel/PPT/R2, isolated preprod database denial/CDN,
confirmed temporary instance termination, production migration and healthy rolling
replacement, actual service digests, student playback chain, manifest promotion and
shared lock release passed. The candidate includes the paper OMR subjective-score
boundary fix and patched native/Python XML parsers.

On 2026-09-21 KST, frontend
[35555250585](https://github.com/guswls3028-art/academy-frontend/actions/runs/35555250585)
completed production deployment at `8f1d580dc4901631bed5a50eccc60628968c7667`, after
21 isolated real-use tests with no skips/flaky results and cleanup zero, latest PR E2E,
and additional same-artifact final UI QA with cleanup zero. Production login, tenant
availability and assessment read-only checks passed. Three public readback rounds on
godmin.kr and hakwonplus.com matched this revision and the exact bytes/hashes of 23
entry files and 43 critical assets; this is not a network check of all 611 bundle files.
Authenticated clinic read-only checks passed at 1366/390px on hakwonplus.com only:
four read API categories returned 200, UI validation/recovery/reload passed, and business
write attempts/forwarding were zero. One login was accepted; observation writes were zero.
No godmin credentials were reused, and production create/book/undo writes were not tested.
Those isolated writable journeys, staff lock recovery, manual-grade preservation and
690-second desktop/mobile video renewal are covered by the official real-use run.
See the [current stabilization handoff](../refactor/hardening-plan.md#현재-실행-상태--2026-09-21-kst)
for evidence limits, documentation/worktree handoff procedures, notification configuration
and the separate Messaging/DS HOLDs. This deployment does not refresh the older
infrastructure inventory below.

Previously, on 2026-09-20, activation rollout
[35509622551](https://github.com/guswls3028-art/academy-backend/actions/runs/35509622551)
completed at `2026-09-20T22:04:28+09:00`, source
`c88038d47a05e50ec7e8fd095698f88c910e080f`, with all service digests verified.
An additional read-only check confirmed the manifest's exact API digest, migration 0022,
the expanded clinic time constraint, DRF 3.17.2 and both clinic write flags true.
All six candidate images passed ECR scanning with critical=0, acceptedCritical=0 and high=0.
This is historical backend activation evidence; the newer deployment and UI verification
above supersede its then-pending stabilization checks.

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

Historical evidence for the infrastructure snapshot above (not the latest application release):

- GitHub run `32330381855`, source
  `fc4f748dfbf47575eb9424ee301f6771c47116de`, passed immutable builds, exact
  ECR scan identity, persistent development, isolated preprod, migration,
  launch-before-terminate API/worker refreshes, runtime digest verification,
  Video Batch verification, and successful release-manifest promotion.
- At that verification, the release manifest was `complete=true`,
  `status=successful`, and recorded the same source SHA and run-bound image tag.
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
