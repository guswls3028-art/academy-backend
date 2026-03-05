# AWS 리소스 인벤토리 (V1 SSOT 기준)

**리전:** ap-northeast-2 **VPC:** vpc-0831a2484f9b114c2 **생성:** 2026-03-06T07:34:52.4078832+09:00

## EC2 인스턴스
| InstanceId | State | Name | SSOT |
|------------|-------|------|------|
| i-0afb67b956ae39197 | running | academy-v1-ai-worker | KEEP |
| i-0b44a50734e645639 | running | academy-v1-messaging-worker | KEEP |
| i-0a4c82c75894cd30a | shutting-down |  | KEEP |
| i-01d21908b072c9913 | shutting-down | academy-v1-api | KEEP |
| i-0d6e9350fd79eddb8 | running | academy-v1-api | KEEP |
| i-0be84ddc2c966a990 | running | academy-v1-api | KEEP |

## Auto Scaling Groups
| Name | Desired | Min | Max | SSOT |
|------|---------|-----|-----|------|
| academy-v1-ai-worker-asg | 1 | 1 | 10 | KEEP |
| academy-v1-api-asg | 2 | 2 | 4 | KEEP |
| academy-v1-messaging-worker-asg | 1 | 1 | 10 | KEEP |
| academy-v1-video-ops-ce-asg-823f2525-3a00-318c-85cf-2ccfc033c170 | 0 | 0 | 0 | KEEP |

## Elastic IPs
| AllocationId | PublicIp | AssociationId | SSOT |
|--------------|----------|---------------|------|
| eipalloc-005028ec477ae0819 | 3.37.217.75 | eipassoc-04d5b7de4fce4e59d | KEEP |
| eipalloc-0339e2c05fdf6d349 | 43.203.92.254 | eipassoc-0f30f7524e6937e06 | KEEP |
| eipalloc-0cf9f6d0e100d6787 | 54.180.111.188 | eipassoc-090d318237815043c | KEEP |
| eipalloc-02bcb9e54f8f9cca3 | 54.180.207.91 | eipassoc-0e330c41df3a67367 | KEEP |

## Security Groups (VPC)
| GroupId | GroupName | ENICount | SSOT |
|---------|-----------|--------|------|
| sg-0405c1afe368b4e6b | default | 2 | KEEP |
| sg-011ed1d9eb4a65b8f | academy-video-batch-sg | 25 | KEEP |
| sg-0ba6fc12209bec7de | academy-v1-sg-batch | 1 | KEEP |
| sg-0051cc8f79c04b058 | academy-api-sg | 0 | LEGACY_CANDIDATE |
| sg-02692600fbf8e26f7 | academy-worker-sg | 0 | LEGACY_CANDIDATE |
| sg-0944a30cabd0c022e | academy-lambda-endpoint-sg | 1 | LEGACY_CANDIDATE |
| sg-0f4069135b6215cad | academy-redis-sg | 1 | LEGACY_CANDIDATE |
| sg-00d2fb147d61f5cd8 | academy-v1-vpce-sg | 0 | LEGACY_CANDIDATE |
| sg-088fa3315c12754d0 | academy-v1-sg-app | 5 | KEEP |
| sg-0ff11f1b511861447 | academy-lambda-internal-sg | 1 | LEGACY_CANDIDATE |
| sg-0caaa6c43e12758e6 | academy-lambda-video-sg | 1 | LEGACY_CANDIDATE |
| sg-0f04876abb91d1606 | academy-v1-sg-data | 1 | KEEP |
| sg-06cfb1f23372e2597 | academy-rds | 1 | LEGACY_CANDIDATE |

## Batch Compute Environments
| Name | State | Status | SSOT |
|------|-------|--------|------|
| academy-v1-video-batch-ce | DISABLED | VALID | KEEP |
| academy-v1-video-ops-ce | ENABLED | VALID | KEEP |

## Batch Job Queues
| Name | State | SSOT |
|------|-------|------|
| academy-v1-video-batch-queue | ENABLED | KEEP |
| academy-v1-video-ops-queue | ENABLED | KEEP |

## Load Balancers
| Name | Scheme | VpcId | SSOT |
|------|--------|-------|------|
| academy-v1-api-alb | internet-facing | vpc-0831a2484f9b114c2 | KEEP |

## Target Groups
| Name | Port | VpcId | SSOT |
|------|------|-------|------|
| academy-v1-api-tg | 8000 | vpc-0831a2484f9b114c2 | KEEP |

---
SSOT keep: API ASG/ALB/TG, Workers ASG, Batch CE/Queue, academy-db, academy-v1-redis. Others LEGACY_CANDIDATE.

