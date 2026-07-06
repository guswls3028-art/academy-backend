# AWS 리소스 인벤토리 (V1 SSOT 기준)

**리전:** ap-northeast-2 **VPC:** vpc-0831a2484f9b114c2 **생성:** 2026-07-06T17:31:05.0416023+09:00

## EC2 인스턴스
| InstanceId | State | Name | SSOT |
|------------|-------|------|------|
| i-0e77e903787b2d638 | running | academy-v1-api | KEEP |
| i-0f63c1190fbb1bb80 | running | academy-v1-messaging-worker | KEEP |

## Auto Scaling Groups
| Name | Desired | Min | Max | SSOT |
|------|---------|-----|-----|------|
| AWSBatch-academy-v1-video-batch-ce-200gb-asg-872e86dd-3192-3b90-80fe-0cdd8da6b3b7 | 0 | 0 | 0 | KEEP |
| academy-v1-ai-worker-asg | 0 | 0 | 5 | KEEP |
| academy-v1-api-asg | 1 | 1 | 3 | KEEP |
| academy-v1-messaging-worker-asg | 1 | 1 | 3 | KEEP |
| academy-v1-tools-worker-asg | 0 | 0 | 2 | KEEP |
| academy-v1-video-ops-ce-asg-823f2525-3a00-318c-85cf-2ccfc033c170 | 0 | 0 | 0 | KEEP |

## Elastic IPs
| AllocationId | PublicIp | AssociationId | NetworkInterfaceId | VpcId | SSOT |
|--------------|----------|---------------|--------------------|-------|------|
| eipalloc-042542895df5cef09 | 13.125.8.19 | eipassoc-05b1d36f6f446c9fc | eni-0033153054cd07090 | vpc-0831a2484f9b114c2 | KEEP |
| eipalloc-058fcc33fc7f2cdca | 15.164.79.63 | eipassoc-0cdaffe3c103fe4a8 | eni-03ecb9aa6bc83687d | vpc-0831a2484f9b114c2 | KEEP |
| eipalloc-08adac5f5914cbac1 | 43.202.246.97 | eipassoc-083ab37ccaf139a89 | eni-062aaa3574f3b4e29 | vpc-0b89e02241aae4b0e | OUT_OF_SCOPE |

## Security Groups (VPC)
| GroupId | GroupName | ENICount | SSOT |
|---------|-----------|--------|------|
| sg-0f04876abb91d1606 | academy-v1-sg-data | 1 | KEEP |
| sg-0d5305dcafd3ccc4d | academy-v1-sg-batch | 0 | KEEP |
| sg-0405c1afe368b4e6b | default | 2 | KEEP |
| sg-03cf8c8f38f477687 | academy-v1-sg-app | 2 | KEEP |
| sg-06cfb1f23372e2597 | academy-rds | 5 | KEEP |

## Batch Compute Environments
| Name | State | Status | Type | Max vCPU | InstanceTypes | SSOT |
|------|-------|--------|------|----------|---------------|------|
| academy-v1-video-batch-ce-200gb | ENABLED | VALID | SPOT | 40 | c6g.2xlarge,c6g.4xlarge,c6g.xlarge | KEEP |
| academy-v1-video-ops-ce | ENABLED | VALID | EC2 | 1 | m6g.medium | KEEP |

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

