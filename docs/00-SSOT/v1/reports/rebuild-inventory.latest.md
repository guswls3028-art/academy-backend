# V1 Stateless Compute 재구축 — 인벤토리 (스냅샷)

**Generated:** 2026-03-06T15:45:38.8679257+09:00  
**리전:**   
**SSOT:** docs/00-SSOT/v1/params.yaml

## EC2 running (Project=academy)
| InstanceId | Name | SubnetId | PublicIp | PrivateIp |
|------------|------|----------|----------|-----------|
| (none) | - | - | - | - |

## ASG (academy-v1-*)
| Name | Min | Desired | Max | Subnets |
|------|-----|---------|-----|---------|
| (none) | - | - | - | - |

## ALB/TG health
| ALB | TG | HealthPath | Healthy/Total |
|-----|----|------------|--------------|
| academy-v1-api-alb | academy-v1-api-tg |  | 0/0 |

## Batch (SSOT names)
| Type | Name | Status/State | Notes |
|------|------|--------------|------|
| CE | academy-v1-video-batch-ce | not found/ |  |
| CE | academy-v1-video-ops-ce | not found/ |  |
| Queue | academy-v1-video-batch-queue | not found |  |
| Queue | academy-v1-video-ops-queue | not found |  |
| JobDef | academy-v1-video-batch-jobdef | not found |  |
| JobDef | academy-v1-video-ops-reconcile | not found |  |
| JobDef | academy-v1-video-ops-scanstuck | not found |  |
| JobDef | academy-v1-video-ops-netprobe | not found |  |

## EventBridge (SSOT rules)
| Rule | State | Targets |
|------|-------|---------|
| academy-v1-reconcile-video-jobs | not found | 0 |
| academy-v1-video-scan-stuck-rate | not found | 0 |

## NAT/EIP/SG
| Item | Value | Notes |
|------|-------|------|
| NAT gateways (non-deleted) | 0 | network.natEnabled=false 목표 |
| EIP total (all) | 0 | 참고 |
| EIP service-managed (alb/rds 등) | 0 | AWS 서비스 관리 (보통 직접 release 불가) |
| EIP user-managed | 0 | **목표=0** |
| EIP user-managed orphan | 0 | orphan이면 즉시 release 후보 |
| Security groups (VPC) | 0 | 목표 ≤ 8 |

## Security Groups (VPC)
| GroupId | GroupName | ENI count |
|---------|-----------|----------|

