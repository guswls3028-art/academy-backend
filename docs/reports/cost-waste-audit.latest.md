# Cost/Waste Audit - Current Runtime

**Generated:** 2026-10-25T20:17:42.9711238+09:00
**Scope:** Academy V1 production resources in `ap-northeast-2`; account-wide billing is labeled separately until project-tagged cost data accumulates.
**Mode:** AWS describe/get/list, CloudWatch 30/90-day history, read-only OS measurement via SSM, and cleanup dry-runs.
**Truth sources:** AWS actual state, `docs/ssot/params.yaml`, CloudWatch, SSM host memory, AWS Price List, Cost Explorer, AWS Budget, and cleanup dry-runs.

## Confirmed Facts

| Check | Result | Disposition |
|-------|--------|-------------|
| AWS Budget | actual=249.83 USD, limit=380, forecast=319.08, used=65.7% | ok |
| Cost Explorer (AWS account) | ok; period 2026-10-01 through 2026-10-25 | account-wide monthly-to-date; not Academy-only |
| Project cost-allocation tag | Project status=Active; Academy tagged-cost status=ok | future Academy isolation enabled |
| ECR cleanup dry-run | 0 image(s), 0 GB reclaimable, status=ok | no ECR deletion needed |
| Batch jobdef cleanup dry-run | keep=42, drop=48, status=ok | cleanup candidate |
| RDS class | db.t4g.medium, status=available, pending={} | matches SSOT |
| Redis node | cache.t4g.small, status=available | matches SSOT |
| Running EC2 in academy VPC | 7 | API/Messaging/AI/Tools warm baseline + persistent development runtime + active Batch bursts |
| Persistent development EC2 | 1 exact-tagged instance(s) | confirmed |
| NAT Gateway | 0 available | matches NAT-off posture |

## Capacity SSOT vs Actual

| Component | SSOT | Actual | Disposition |
|-----------|------|--------|-------------|
| API ASG | min=1 desired=1 max=3, type=t4g.medium | min=1 desired=1 max=3, healthy=1, type=t4g.medium | confirmed |
| Messaging worker ASG | min=1 desired=1 max=3, type=t4g.small | min=1 desired=1 max=3, healthy=1, type=t4g.small | confirmed |
| AI worker ASG | min=1 desired=1 max=5, type=t4g.medium | min=1 desired=1 max=5, healthy=1, type=t4g.medium | confirmed |
| Tools worker ASG | min=1 desired=1 max=2, type=t4g.small | min=1 desired=1 max=2, healthy=1, type=t4g.small | confirmed |
| Video Batch CE | min=0 max=40 type=SPOT, types=c6g.4xlarge,c6g.2xlarge,c6g.xlarge | SPOT, state=ENABLED/VALID, min=0 desired=4 max=40, types=c6g.4xlarge,c6g.2xlarge,c6g.xlarge | confirmed |
| Video Ops CE | min=0 max=1 type=EC2, types=m6g.medium | EC2, state=ENABLED/VALID, min=0 desired=1 max=1, types=m6g.medium | confirmed |

## Cumulative Usage Evidence

| Component / signal | Last 30 days | Last 90 days | Interpretation |
|--------------------|----------------------|---------------------|----------------|
| API CPU | avg=11.19%, peak=99.79% | avg=11.86%, peak=99.85% | keep t4g.medium; burst CPU reached 99.85% |
| API live memory | used=0.98 GiB, available=2.58 GiB (68.9%), measured | historical OS memory unavailable | academy-api 655.5MiB / 3.742GiB |
| Messaging CPU | avg=0.42%, peak=4.08% | avg=0.42%, peak=55.70% | keep t4g.small (right-sized) |
| Messaging live memory | used=0.41 GiB, available=1.22 GiB (67.6%), measured | historical OS memory unavailable | academy-messaging-worker 87.4MiB / 1.8GiB |
| AI CPU | avg=1.36%, peak=88.66% | avg=3.17%, peak=88.66% | keep one warm baseline; active bursts need current CPU class |
| Tools CPU | avg=0.46%, peak=43.00% | avg=1.36%, peak=61.05% | keep one warm baseline t4g.small |
| Messaging SQS sent | 809 messages | 6,861 messages | cumulative CloudWatch Sum |
| AI SQS sent | 740 messages | 1,650 messages | cumulative CloudWatch Sum |
| Tools SQS sent | 98 messages | 328 messages | cumulative CloudWatch Sum |
| RDS CPU / connections | CPU avg=5.52%, peak=52.87%; connections peak=30 | CPU avg=5.59%, peak=53.27%; connections peak=391 | keep db.t4g.medium; memory/connection guardrail blocks another downsize |
| RDS memory / credits | free low=0.25 GiB, swap peak=0.16 GiB, credit low=575.34 | free low=0.25 GiB, swap peak=0.16 GiB, credit low=574.55 | memory guardrail blocks db.t4g.small |
| Redis CPU / dataset | host peak=18.23%, engine peak=0.43%, dataset peak=0.01 GiB | host peak=18.23%, engine peak=0.47%, dataset peak=0.01 GiB | review cache.t4g.micro in a maintenance window |
| Redis memory / connections | DB memory peak=1.32%, free low=1.23 GiB, connections peak=54 | DB memory peak=1.32%, free low=1.23 GiB, connections peak=54, evictions=0 | review cache.t4g.micro in a maintenance window; any resize still requires a compatible single-node maintenance plan |

## Right-Sizing Decisions

| Component | Decision | Evidence status |
|-----------|----------|-----------------|
| API | keep t4g.medium; burst CPU reached 99.85% | CloudWatch samples=361; current SSM memory=measured |
| Messaging | keep t4g.small (right-sized) | CloudWatch samples=361; current SSM memory=measured |
| AI / Tools | retain current one-instance warm baselines | user-facing cold-start policy + confirmed burst CPU |
| RDS | keep db.t4g.medium; memory/connection guardrail blocks another downsize | confirmed CloudWatch memory, swap, credits, connections |
| Redis | review cache.t4g.micro in a maintenance window | confirmed CloudWatch dataset, memory, credits, connections, evictions |

## Waste Checks

| Check | Result | Disposition |
|-------|--------|-------------|
| Unassociated Elastic IP | 0 | clean |
| Unused Security Group | 0 / total SG 6 | clean |
| Available EBS volume | 0, 0 GiB | clean |
| Orphan EC2 in academy VPC | 0 | clean |
| Batch compute | standard=SPOT, state=ENABLED/VALID, min=0 desired=4 max=40, types=c6g.4xlarge,c6g.2xlarge,c6g.xlarge; ops=EC2, state=ENABLED/VALID, min=0 desired=1 max=1, types=m6g.medium | idle desired should remain 0 outside jobs |
| SQS academy-v1-messaging-queue | visible=0, in-flight=0, DLQ=0 | clean |
| SQS academy-v1-ai-queue | visible=0, in-flight=0, DLQ=0 | clean |
| SQS academy-v1-tools-queue | visible=0, in-flight=0, DLQ=0 | clean |

## Cost Explorer Snapshot - AWS Account Wide

Time period: 2026-10-01 through 2026-10-25, unblended cost, estimated. This table is context only and must not be reported as Academy-only cost.

| Service | Cost |
|---------|------|
| Amazon Elastic Compute Cloud - Compute | 87.05 USD |
| Amazon Relational Database Service | 63.62 USD |
| Amazon ElastiCache | 27.31 USD |
| Tax | 22.71 USD |
| Amazon Virtual Private Cloud | 21.38 USD |
| Amazon Elastic Load Balancing | 13.22 USD |
| EC2 - Other | 6.25 USD |
| Amazon Bedrock | 4.33 USD |
| Amazon EC2 Container Registry (ECR) | 1.90 USD |
| AmazonCloudWatch | 1.50 USD |
| AWS Systems Manager | 0.23 USD |
| Amazon Simple Storage Service | 0.15 USD |
| Amazon Simple Queue Service | 0.14 USD |
| AWS Cost Explorer | 0.05 USD |

## Cost Explorer Snapshot - Academy Project Tag

| Service | Cost |
|---------|------|
| Amazon Elastic Compute Cloud - Compute | 82.64 USD |
| Amazon Relational Database Service | 63.62 USD |
| Amazon ElastiCache | 27.31 USD |
| Amazon Elastic Load Balancing | 13.22 USD |
| EC2 - Other | 2.56 USD |
| Amazon EC2 Container Registry (ECR) | 1.90 USD |
| Amazon Simple Queue Service | 0.14 USD |

## Projections

| Projection | Amount | Basis |
|------------|--------|-------|
| New Messaging compute savings | none established | Current class=t4g.small; keep t4g.small (right-sized). An already applied reduction is not a new saving. |

## Unverified

| Item | Status |
|------|--------|
| EC2 historical OS memory | CloudWatch Agent memory metrics are not installed; SSM values are current point-in-time measurements only. |
| Realized billing reduction | Projection remains unverified until the next complete Cost Explorer billing interval after deployment. |
| Academy historical tagged cost | Cost allocation cannot be reconstructed before the Project tag activation date; only future tagged charges can be isolated. |

## Policy Conflicts

| Alternative | Conflict / disposition |
|-------------|------------------------|
| API scale-to-zero or smaller class | Rejected: public API warm-baseline policy plus observed CPU burst near saturation. |
| Messaging scale-to-zero | Rejected: account recovery and Alimtalk latency require one warm worker; instance class can still be reduced. |
| AI/Tools scale-to-zero | Rejected: student import, AI, and document conversion are user-facing wait paths; each keeps one warm worker. |
| RDS further downsize | keep db.t4g.medium; memory/connection guardrail blocks another downsize |
| Redis further downsize | review cache.t4g.micro in a maintenance window; a single-node resize needs a reviewed continuity/maintenance plan. |

## Recommended Actions

| Action |
|--------|
| Run `python scripts/v1/batch-jobdef-cleanup.py --execute --keep 5` to deregister 48 old ACTIVE job definition revision(s). |

## Policy Decisions Retained

| Item | Status |
|------|--------|
| API warm baseline | kept at one `t4g.medium`; target tracking keeps headroom for public API latency. |
| Messaging worker warm baseline | kept at one `t4g.small`; account recovery and Alimtalk wait paths should not cold-start. |
| AI/Tools workers | one warm instance retained for first-request latency; queue alarms own burst scale-out. |
| Standard video encoding | Spot Batch CE retained; paid encode tests are not submitted by this audit. |
| RDS/Redis | RDS: keep db.t4g.medium; memory/connection guardrail blocks another downsize. Redis: review cache.t4g.micro in a maintenance window. This read-only audit does not resize either service. |
