# AWS 리소스 인벤토리 (V1 SSOT 기준)

**리전:** ap-northeast-2 **VPC:** vpc-0831a2484f9b114c2 **생성:** 2026-06-24T21:54:14.9233172+09:00

## EC2 인스턴스
| InstanceId | State | Name | SSOT |
|------------|-------|------|------|
| i-0d2c253344dc8237d | running |  | KEEP |
| i-04c8ed852e4ca9bc6 | running |  | KEEP |
| i-08ebf442a47a3ce23 | running | academy-v1-api | KEEP |
| i-0b877bb67d0a838d8 | running |  | KEEP |
| i-0314fafcfcd05cd8f | running |  | KEEP |

## Auto Scaling Groups
| Name | Desired | Min | Max | SSOT |
|------|---------|-----|-----|------|
| AWSBatch-academy-v1-video-batch-ce-200gb-asg-872e86dd-3192-3b90-80fe-0cdd8da6b3b7 | 12 | 0 | 12 | KEEP |
| academy-v1-ai-worker-asg | 0 | 0 | 5 | KEEP |
| academy-v1-api-asg | 1 | 1 | 3 | KEEP |
| academy-v1-messaging-worker-asg | 0 | 0 | 3 | KEEP |
| academy-v1-tools-worker-asg | 0 | 0 | 2 | KEEP |
| academy-v1-video-ops-ce-asg-823f2525-3a00-318c-85cf-2ccfc033c170 | 0 | 0 | 0 | KEEP |

## Elastic IPs
| AllocationId | PublicIp | AssociationId | SSOT |
|--------------|----------|---------------|------|
| eipalloc-0ca89cf3a856fc873 | 13.209.151.18 | eipassoc-0e9fd9dcfa661bef6 | KEEP |
| eipalloc-08adac5f5914cbac1 | 43.202.246.97 | eipassoc-083ab37ccaf139a89 | KEEP |
| eipalloc-000eaa654e4f3799e | 54.116.238.41 | eipassoc-069135c6ff73ee31e | KEEP |

## Security Groups (VPC)
| GroupId | GroupName | ENICount | SSOT |
|---------|-----------|--------|------|
| sg-0f04876abb91d1606 | academy-v1-sg-data | 1 | KEEP |
| sg-0d5305dcafd3ccc4d | academy-v1-sg-batch | 4 | KEEP |
| sg-0405c1afe368b4e6b | default | 2 | KEEP |
| sg-03cf8c8f38f477687 | academy-v1-sg-app | 1 | KEEP |
| sg-06cfb1f23372e2597 | academy-rds | 5 | KEEP |

## Batch Compute Environments
| Name | State | Status | SSOT |
|------|-------|--------|------|
| academy-v1-video-batch-ce-200gb | ENABLED | VALID | KEEP |
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
SSOT keep: API ASG/ALB/TG, Workers ASG, Batch CE/Queue/managed ASG, academy-db/academy-rds SG, academy-v1-redis. Others LEGACY_CANDIDATE.

