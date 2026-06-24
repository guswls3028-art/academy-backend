# Cost/Waste Audit — Current Runtime

**Generated:** 2026-06-24T21:25:00+09:00
**Scope:** academy V1 production resources in `ap-northeast-2`.
**Truth sources:** AWS actual state, `docs/ssot/params.yaml`, `docs/reports/aws-resource-inventory.latest.md`, `docs/reports/resource-cleanup.latest.md`, Cost Explorer, and CloudWatch.

## Capacity SSOT vs Actual

| Component | SSOT | Actual | Disposition |
|-----------|------|--------|-------------|
| API ASG | min=1 desired=1 max=3 | min=1 desired=1 max=3, 1 healthy | repo-confirmed + runtime-confirmed |
| Messaging worker ASG | min=0 desired=0 max=3 | min=0 desired=0 max=3 | repo-confirmed + runtime-confirmed |
| AI worker ASG | min=0 desired=0 max=5 | min=0 desired=0 max=5 | repo-confirmed + runtime-confirmed |
| Tools worker ASG | min=0 desired=0 max=2 | min=0 desired=0 max=2 | repo-confirmed + runtime-confirmed |
| Video Batch CE | min=0 max=40 vCPU | desired >0 while video jobs are running | active workload, not idle waste |
| Video Ops CE | min=0 max=2 vCPU | returns to 0 after scheduled jobs | expected transient burst |

## Waste Checks

| Check | Result | Disposition |
|-------|--------|-------------|
| Unassociated Elastic IP | 0 | fixed/clean |
| NAT Gateway | 0 available in academy VPC | clean |
| Unattached EBS volume | 0 | clean |
| Worker queues | Messaging/AI/Tools visible=0, in-flight=0, delayed=0 | clean |
| Worker scale-in alarms | Messaging/Tools scale-in `TreatMissingData=breaching`; scale-out `notBreaching` | clean |
| Cleanup dry-run | no delete/release target | clean |
| Batch compute | standard video Batch had active running jobs during audit | intentionally running |

## Cost Explorer Snapshot

Time period: 2026-06-01 through 2026-06-25, unblended cost, estimated.

| Service | Cost |
|---------|------|
| Amazon RDS | 134.69 USD |
| EC2 Compute | 83.45 USD |
| Tax | 28.24 USD |
| ElastiCache | 25.38 USD |
| VPC | 19.36 USD |
| Elastic Load Balancing | 12.49 USD |
| EC2 - Other | 5.05 USD |

## Right-Size Notes

- RDS is the largest non-burst cost. Actual class is `db.t4g.large`; 7-day CloudWatch sample showed low CPU average (~5.17%), max spike ~50.26%, average connections ~2.27, max 14, and freeable memory around 4.7GB. This is a downsize candidate only after slow-query, connection-budget, and peak workload review.
- API is already at the intended cost floor for user-facing HTTP: one warm `t4g.medium` baseline with target tracking up to 3.
- Messaging/AI/Tools have no idle EC2 baseline. Do not reserve worker instances while their SSOT remains scale-to-zero.
