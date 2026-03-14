# V1 Stateless Compute 재구축 — 인벤토리 (스냅샷)

**Generated:** 2026-03-06T18:08:50.5675790+09:00  
**리전:**   
**SSOT:** docs/00-SSOT/v1/params.yaml

## EC2 running (Project=academy)
| InstanceId | Name | SubnetId | PublicIp | PrivateIp |
|------------|------|----------|----------|-----------|
| i-0d3aaca239fd1637e | academy-v1-api | subnet-09231ed7ecf59cfa4 | 54.181.1.24 | 172.30.1.31 |
| i-06b1d9b52935fd387 | academy-v1-api | subnet-09231ed7ecf59cfa4 | 43.203.114.222 | 172.30.1.132 |
| i-0851018cae061ea8d | academy-v1-ai-worker | subnet-07a8427d3306ce910 | 3.36.95.243 | 172.30.0.125 |
| i-0b47a6fce4975ec91 | academy-v1-messaging-worker | subnet-07a8427d3306ce910 | 3.35.136.68 | 172.30.0.144 |

## ASG (academy-v1-*)
| Name | Min | Desired | Max | Subnets |
|------|-----|---------|-----|---------|
| academy-v1-ai-worker-asg | 1 | 1 | 5 | subnet-07a8427d3306ce910,subnet-0548571ac21b3bbf3 |
| academy-v1-api-asg | 1 | 1 | 2 | subnet-049e711f41fdff71b,subnet-09231ed7ecf59cfa4 |
| academy-v1-messaging-worker-asg | 1 | 1 | 3 | subnet-07a8427d3306ce910,subnet-0548571ac21b3bbf3 |

## ALB/TG health
| ALB | TG | HealthPath | Healthy/Total |
|-----|----|------------|--------------|
| academy-v1-api-alb | academy-v1-api-tg | /healthz | 0/2 |

## Batch (SSOT names)
| Type | Name | Status/State | Notes |
|------|------|--------------|------|
| CE | academy-v1-video-batch-ce | VALID/ENABLED |  |
| CE | academy-v1-video-ops-ce | VALID/ENABLED |  |
| Queue | academy-v1-video-batch-queue | ENABLED |  |
| Queue | academy-v1-video-ops-queue | ENABLED |  |
| JobDef | academy-v1-video-batch-jobdef | 19 |  |
| JobDef | academy-v1-video-ops-reconcile | 19 |  |
| JobDef | academy-v1-video-ops-scanstuck | 19 |  |
| JobDef | academy-v1-video-ops-netprobe | 19 |  |

## EventBridge (SSOT rules)
| Rule | State | Targets |
|------|-------|---------|
| academy-v1-reconcile-video-jobs | ENABLED | 0 |
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

