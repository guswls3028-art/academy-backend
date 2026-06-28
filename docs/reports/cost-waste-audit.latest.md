# Cost/Waste Audit - Current Runtime

**Generated:** 2026-06-29T05:08:00+09:00
**Scope:** academy V1 production resources in `ap-northeast-2`.
**Truth sources:** AWS actual state, `docs/ssot/params.yaml`, `docs/reports/aws-resource-inventory.latest.md`, `docs/reports/resource-cleanup.latest.md`, Cost Explorer, ECR/Batch cleanup dry-runs, production canary, and deploy verification.

## Confirmed Facts

| Check | Result | Disposition |
|-------|--------|-------------|
| Deploy plan | NoOp | SSOT resources exist and Batch CE type now matches SSOT |
| Production canary | PASS=30 WARN=0 FAIL=0 | public HTTP, AWS infra, and remote Django invariants healthy |
| Deploy verification | PASS / GO | drift/evidence/runtime/front reports regenerated |
| Cleanup dry-run | no delete target | no destructive cleanup executed |
| ECR cleanup dry-run | 0 images / 0.0 GB reclaimable | no ECR deletion needed |
| Batch jobdef cleanup dry-run | keep=42, drop=0 | no deregistration needed |
| RDS downsize | `db.t4g.medium`, pending `{}` | previous `db.t4g.large -> db.t4g.medium` optimization is applied |
| Video Batch CE | `EC2 -> SPOT` applied in-place | standard encoding jobs now use Spot capacity; CE is `VALID/ENABLED` |

## Capacity SSOT vs Actual

| Component | SSOT | Actual | Disposition |
|-----------|------|--------|-------------|
| API ASG | min=1 desired=1 max=3 | min=1 desired=1 max=3, 1 healthy | confirmed |
| Messaging worker ASG | min=1 desired=1 max=3 | min=1 desired=1 max=3, 1 healthy | confirmed warm baseline |
| AI worker ASG | min=0 desired=0 max=5 | min=0 desired=0 max=5 | confirmed scale-to-zero |
| Tools worker ASG | min=0 desired=0 max=2 | min=0 desired=0 max=2 | confirmed scale-to-zero |
| Video Batch CE | min=0 max=40 vCPU, Spot | `SPOT`, `VALID/ENABLED`, desired=0 | cost drift fixed |
| Video Ops CE | min=0 max=1 vCPU, EC2 | `EC2`, `VALID/ENABLED`, max=1 | one-instance ops burst cap retained |

## Optimizations Applied

| Item | Before | After | Result |
|------|--------|-------|--------|
| RDS class | `db.t4g.large` | `db.t4g.medium` | fixed non-burst baseline cost |
| Standard Video Batch CE type | `EC2` on-demand | `SPOT` | lowers future video encoding compute cost |
| Video ops enqueue cadence | `rate(10 minutes)` | `rate(1 hour)` | prevents lightweight recovery jobs from keeping an `m6g.medium` ops Batch instance warm |
| Batch CE drift guard | existence-only plan | type/allocation/max/types comparison | future EC2/Spot drift is visible in `deploy -Plan` |
| Video CE rebuild safety | drift skipped | active-job guard before rebuild | prevents queue/CE rebuild while video jobs are active |
| Resource inventory | CE state/status only | CE type/max/instance types included | cost posture visible in reports |

## Waste Checks

| Check | Result | Disposition |
|-------|--------|-------------|
| Running EC2 in academy VPC | 2 (`academy-v1-api`, `academy-v1-messaging-worker`) | clean; matches API + Messaging warm baselines |
| Unassociated Elastic IP | 0 | clean |
| NAT Gateway | 0 available in academy VPC | clean |
| Unattached EBS volume | 0 in cleanup scope | clean |
| Worker queues | Messaging/AI/Tools visible=0, in-flight=0, DLQ=0 | clean |
| EventBridge Batch targets | 1 target per managed video ops rule | clean |
| Cleanup dry-run | no delete/release target | clean |
| Batch compute | standard Batch idle at desired=0; ops Batch capped at max=1 and fallback enqueue is hourly | clean |

## Cost Explorer Snapshot

Time period: 2026-06-01 through 2026-06-29, unblended cost, estimated.

| Service | Cost |
|---------|------|
| Amazon RDS | 154.36 USD |
| EC2 Compute | 99.59 USD |
| Tax | 33.31 USD |
| ElastiCache | 30.41 USD |
| VPC | 22.41 USD |
| Elastic Load Balancing | 16.35 USD |
| EC2 - Other | 7.35 USD |
| Amazon ECR | 0.84 USD |
| AWS Secrets Manager | 0.75 USD |
| CloudWatch | 0.68 USD |
| AWS Systems Manager | 0.13 USD |
| AWS Cost Explorer | 0.12 USD |
| Amazon S3 | 0.11 USD |

## Projection

| Projection | Basis |
|------------|-------|
| RDS medium saves about 0.101 USD/hour before storage/backup/tax versus the previous large class | prior measured ap-northeast-2 Single-AZ PostgreSQL hourly prices in this report lineage |
| Future standard video encoding jobs should be cheaper than the June month-to-date EC2 BoxUsage snapshot | Batch CE now reports `computeResources.type=SPOT`; Cost Explorer still contains earlier on-demand usage |
| Video ops fallback should stop behaving like a warm instance | `enqueue_uploaded_videos` was lengthened from 10 minutes to 1 hour after observing an idle `m6g.medium` ops Batch instance with desired vCPU 1 |
| No immediate ECR/jobdef savings remain | cleanup dry-runs found 0 deletable images and 0 deregistrations |

## Unverified

| Item | Status |
|------|--------|
| Exact realized Spot discount for future video encodes | not visible until new jobs accrue Cost Explorer line items |
| End-to-end real video encode on Spot | not submitted in this audit to avoid creating a paid test encode; queue/CE/jobdef/SSM references are verified |
| User-visible fallback latency for concurrency-limited video uploads | can now be up to 1 hour instead of 10 minutes; normal immediate enqueue path is unchanged |

## Policy Conflicts

| Item | Status |
|------|--------|
| Messaging worker scale-to-zero | not applied; policy requires one warm baseline for account recovery and Alimtalk latency |
| API downsize below `t4g.medium` | not applied; current cost floor keeps one warm API instance with target tracking headroom |
