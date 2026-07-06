# Cost/Waste Audit - Current Runtime

**Generated:** 2026-07-06T17:46:30.9988750+09:00
**Scope:** academy V1 production resources in `ap-northeast-2`.
**Mode:** read-only AWS describe/get/list + cleanup dry-runs.
**Truth sources:** AWS actual state, `docs/ssot/params.yaml`, Cost Explorer, AWS Budget, ECR/Batch cleanup dry-runs, and resource cleanup checks.

## Confirmed Facts

| Check | Result | Disposition |
|-------|--------|-------------|
| AWS Budget | actual=45.88 USD, limit=380, forecast=303.31, used=12.1% | ok |
| Cost Explorer | ok; period 2026-07-01 through 2026-07-06 | monthly-to-date |
| ECR cleanup dry-run | 0 image(s), 0 GB reclaimable, status=ok | no ECR deletion needed |
| Batch jobdef cleanup dry-run | keep=42, drop=0, status=ok | no deregistration needed |
| RDS class | db.t4g.medium, status=available, pending={} | matches SSOT |
| Redis node | cache.t4g.small, status=available | matches SSOT |
| Running EC2 in academy VPC | 2 | API/Messaging warm baseline plus active worker/batch bursts |
| NAT Gateway | 0 available | matches NAT-off posture |

## Capacity SSOT vs Actual

| Component | SSOT | Actual | Disposition |
|-----------|------|--------|-------------|
| API ASG | min=1 desired=1 max=3 | min=1 desired=1 max=3, healthy=1 | confirmed |
| Messaging worker ASG | min=1 desired=1 max=3 | min=1 desired=1 max=3, healthy=1 | confirmed |
| AI worker ASG | min=0 desired=0 max=5 | min=0 desired=0 max=5, healthy=0 | confirmed |
| Tools worker ASG | min=0 desired=0 max=2 | min=0 desired=0 max=2, healthy=0 | confirmed |
| Video Batch CE | min=0 max=40 type=SPOT, types=c6g.4xlarge,c6g.2xlarge,c6g.xlarge | SPOT, state=ENABLED/VALID, min=0 desired=0 max=40, types=c6g.4xlarge,c6g.2xlarge,c6g.xlarge | confirmed |
| Video Ops CE | min=0 max=1 type=EC2, types=m6g.medium | EC2, state=ENABLED/VALID, min=0 desired=0 max=1, types=m6g.medium | confirmed |

## Waste Checks

| Check | Result | Disposition |
|-------|--------|-------------|
| Unassociated Elastic IP | 0 | clean |
| Unused Security Group | 0 / total SG 5 | clean |
| Available EBS volume | 0, 0 GiB | clean |
| Orphan EC2 in academy VPC | 0 | clean |
| Batch compute | standard=SPOT, state=ENABLED/VALID, min=0 desired=0 max=40, types=c6g.4xlarge,c6g.2xlarge,c6g.xlarge; ops=EC2, state=ENABLED/VALID, min=0 desired=0 max=1, types=m6g.medium | idle desired should remain 0 outside jobs |
| SQS academy-v1-messaging-queue | visible=0, in-flight=0, DLQ=0 | clean |
| SQS academy-v1-ai-queue | visible=0, in-flight=0, DLQ=0 | clean |
| SQS academy-v1-tools-queue | visible=0, in-flight=0, DLQ=0 | clean |

## Cost Explorer Snapshot

Time period: 2026-07-01 through 2026-07-06, unblended cost, estimated.

| Service | Cost |
|---------|------|
| Amazon Relational Database Service | 16.89 USD |
| Amazon Elastic Compute Cloud - Compute | 13.07 USD |
| Amazon ElastiCache | 4.61 USD |
| Tax | 4.16 USD |
| Amazon Virtual Private Cloud | 3.38 USD |
| Amazon Elastic Load Balancing | 2.70 USD |
| EC2 - Other | 0.76 USD |
| Amazon EC2 Container Registry (ECR) | 0.14 USD |
| AWS Secrets Manager | 0.13 USD |
| Amazon Simple Storage Service | 0.02 USD |
| AWS Systems Manager | 0.02 USD |

## Recommended Actions

| Action |
|--------|
| No immediate deletion or downsize target found in this audit. |

## Policy Decisions Retained

| Item | Status |
|------|--------|
| API warm baseline | kept at one `t4g.medium`; target tracking keeps headroom for public API latency. |
| Messaging worker warm baseline | kept at one `t4g.medium`; account recovery and Alimtalk wait paths should not cold-start. |
| AI/Tools workers | scale-to-zero policy retained; queue alarms/API wake-up own burst scale-out. |
| Standard video encoding | Spot Batch CE retained; paid encode tests are not submitted by this audit. |
| RDS/Redis | current small baseline retained until metric evidence supports a safer right-size move. |
