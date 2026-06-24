# Cost/Waste Audit — Current Runtime

**Generated:** 2026-06-25T02:59:16+09:00
**Scope:** academy V1 production resources in `ap-northeast-2`.
**Truth sources:** AWS actual state, `docs/ssot/params.yaml`, `docs/reports/aws-resource-inventory.latest.md`, `docs/reports/resource-cleanup.latest.md`, Cost Explorer, CloudWatch, and post-deploy canary.

## Post-Deploy Verification

| Check | Result | Disposition |
|-------|--------|-------------|
| Deploy plan | NoOp | SSOT resources exist; no deploy refresh needed |
| Production canary | PASS=30 WARN=0 FAIL=0 | public HTTP, AWS infra, and remote Django invariants healthy |
| Deploy verification | PASS / GO | drift/evidence/runtime/front reports regenerated |
| Cleanup dry-run | no delete target | no destructive cleanup executed |
| RDS downsize | pending `db.t4g.large -> db.t4g.medium` | scheduled for next RDS maintenance window, no immediate restart |

## Capacity SSOT vs Actual

| Component | SSOT | Actual | Disposition |
|-----------|------|--------|-------------|
| API ASG | min=1 desired=1 max=3 | min=1 desired=1 max=3, 1 healthy | repo-confirmed + runtime-confirmed |
| Messaging worker ASG | min=0 desired=0 max=3 | min=0 desired=0 max=3 | repo-confirmed + runtime-confirmed |
| AI worker ASG | min=0 desired=0 max=5 | min=0 desired=0 max=5 | repo-confirmed + runtime-confirmed |
| Tools worker ASG | min=0 desired=0 max=2 | min=0 desired=0 max=2 | repo-confirmed + runtime-confirmed |
| Video Batch CE | min=0 max=40 vCPU | CE ENABLED/VALID; idle ASG min=0 desired=0 max=0 | scale-to-zero confirmed |
| Video Ops CE | min=0 max=1 vCPU | CE ENABLED/VALID; idle ASG min=0 desired=0 max=0 | optimized from max=2 to max=1 |

## Optimizations Applied

| Item | Before | After | Result |
|------|--------|-------|--------|
| `academy-v1-detect-stuck-videos` EventBridge targets | 2 targets | 1 target | duplicate Batch job removed |
| `academy-v1-recover-dead-video-jobs` EventBridge targets | 2 targets | 1 target | duplicate Batch job removed |
| `academy-v1-video-ops-ce` max vCPU | 2 | 1 | ops burst EC2 peak capped at one `m6g.medium` |
| Resource inventory EIP scope | all associated EIPs looked like KEEP | academy VPC EIPs are KEEP, outside VPC EIP is OUT_OF_SCOPE | SSOT report no longer blends non-academy resources |

## Waste Checks

| Check | Result | Disposition |
|-------|--------|-------------|
| Running EC2 in academy VPC | 1 (`academy-v1-api`) after ops scale-down | clean |
| Unassociated Elastic IP | 0 | clean |
| NAT Gateway | 0 available in academy VPC | clean |
| Unattached EBS volume | 0 | clean |
| Worker queues | Messaging/AI/Tools visible=0, in-flight=0, DLQ=0 | clean |
| EventBridge Batch targets | 1 target per managed video ops rule | clean |
| Cleanup dry-run | no delete/release target | clean |
| Batch compute | standard Batch idle; ops Batch returns to desired=0 after scheduled jobs | clean |

## Cost Explorer Snapshot

Time period: 2026-06-01 through 2026-06-25, unblended cost, estimated.

| Service | Cost |
|---------|------|
| Amazon RDS | 136.44 USD |
| EC2 Compute | 84.84 USD |
| Tax | 28.61 USD |
| ElastiCache | 25.38 USD |
| VPC | 19.63 USD |
| Elastic Load Balancing | 12.64 USD |
| EC2 - Other | 5.11 USD |
| Amazon ECR | 0.70 USD |
| AWS Secrets Manager | 0.64 USD |
| CloudWatch | 0.36 USD |
| AWS Systems Manager | 0.11 USD |
| Amazon S3 | 0.10 USD |

## Right-Size Notes

- RDS is the largest non-burst cost. Downsize target is `db.t4g.medium`: current `db.t4g.large` price is $0.203/hr, target `db.t4g.medium` is $0.102/hr in ap-northeast-2 Single-AZ PostgreSQL, saving about $0.101/hr (~$73.73 per 730-hour month) before storage/backup/tax. The change is pending on `academy-db` and is scheduled for the RDS maintenance window (`thu:20:20-thu:20:50` UTC, Friday 05:20-05:50 KST).
- The 7-day CloudWatch sample supports the downsize: CPU average 5.18%, max 50.26%, DB connections average 2.30, max 14, freeable memory average ~4.72 GiB, swap usage ~1 MiB, and CPU credit balance stayed full. The 30-day connection spike reached 588 on 2026-06-08/10, so the connection alarm remains at 320 and DB connection budget docs were updated for medium.
- API is already at the intended cost floor for user-facing HTTP: one warm `t4g.medium` baseline with target tracking up to 3.
- Messaging/AI/Tools have no idle EC2 baseline. Do not reserve worker instances while their SSOT remains scale-to-zero.
- Video ops now has one-instance burst cap. If the 10-minute `enqueue_uploaded_videos` recovery schedule still keeps Batch warm too often, the next optimization is to move that tiny recovery check to an API-side scheduled command or lengthen the recovery interval after product latency review.
