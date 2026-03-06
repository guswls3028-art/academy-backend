# AWS 리소스 인벤토리 (V1 SSOT 기준)

**리전:** ap-northeast-2 **VPC:** vpc-0831a2484f9b114c2 **생성:** 2026-03-06T19:23:50.5230571+09:00

## EC2 인스턴스
| InstanceId | State | Name | SSOT |
|------------|-------|------|------|
| i-0dc5242f7d8e37c76 | running | academy-v1-messaging-worker | KEEP |
| i-0477231c3f5f6cb6c | running | academy-v1-api | KEEP |
| i-086e15bf1279bd6f2 | running | academy-v1-ai-worker | KEEP |

## Auto Scaling Groups
| Name | Desired | Min | Max | SSOT |
|------|---------|-----|-----|------|
| academy-v1-ai-worker-asg | 1 | 1 | 5 | KEEP |
| academy-v1-api-asg | 1 | 1 | 2 | KEEP |
| academy-v1-messaging-worker-asg | 1 | 1 | 3 | KEEP |

## Elastic IPs
| AllocationId | PublicIp | AssociationId | SSOT |
|--------------|----------|---------------|------|
| eipalloc-0f252f3b5ff3cb865 | 52.79.34.81 | eipassoc-06b51bf4abab936f5 | KEEP |
| eipalloc-02bcb9e54f8f9cca3 | 54.180.207.91 | eipassoc-0e330c41df3a67367 | KEEP |

## Security Groups (VPC)
| GroupId | GroupName | ENICount | SSOT |
|---------|-----------|--------|------|
| sg-0118032c04257cf27 | academy-v1-vpce-sg | 0 | LEGACY_CANDIDATE |
| sg-0405c1afe368b4e6b | default | 1 | KEEP |
| sg-06cfb1f23372e2597 | academy-rds | 1 | LEGACY_CANDIDATE |
| sg-0f04876abb91d1606 | academy-v1-sg-data | 1 | KEEP |
| sg-0f4069135b6215cad | academy-redis-sg | 1 | LEGACY_CANDIDATE |
| sg-0d5305dcafd3ccc4d | academy-v1-sg-batch | 0 | KEEP |
| sg-03cf8c8f38f477687 | academy-v1-sg-app | 3 | KEEP |

## Batch Compute Environments
| Name | State | Status | SSOT |
|------|-------|--------|------|
| academy-v1-video-batch-ce | ENABLED | VALID | KEEP |

## Batch Job Queues
| Name | State | SSOT |
|------|-------|------|
| academy-v1-video-batch-queue | ENABLED | KEEP |

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

