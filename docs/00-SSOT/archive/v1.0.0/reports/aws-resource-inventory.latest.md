# AWS 리소스 인벤토리 (V1 SSOT 기준)

**리전:** ap-northeast-2 **VPC:** vpc-0831a2484f9b114c2 **생성:** 2026-03-07T11:11:32.7819859+09:00

## EC2 인스턴스
| InstanceId | State | Name | SSOT |
|------------|-------|------|------|
| i-0b9b43e1b92b8b163 | running |  | KEEP |
| i-0d3754df9ed309ed8 | running |  | KEEP |
| i-02787ed4dff4ac569 | running |  | KEEP |
| i-0920cd2d020f4e35a | running |  | KEEP |
| i-0301556c841a7b64d | running |  | KEEP |
| i-00afdead6de5cda5c | running | academy-v1-ai-worker | KEEP |
| i-0638ed686087129ec | running | academy-v1-messaging-worker | KEEP |
| i-08a4938bc3c495f8b | running | academy-v1-api | KEEP |
| i-0664458ac92fa5f1c | running | academy-v1-api | KEEP |

## Auto Scaling Groups
| Name | Desired | Min | Max | SSOT |
|------|---------|-----|-----|------|
| academy-v1-ai-worker-asg | 1 | 1 | 5 | KEEP |
| academy-v1-api-asg | 1 | 1 | 2 | KEEP |
| academy-v1-messaging-worker-asg | 1 | 1 | 3 | KEEP |
| academy-v1-video-batch-ce-asg-a7df1435-25da-3f7e-ae62-5a97ab0ef7e3 | 12 | 0 | 12 | KEEP |
| academy-v1-video-ops-ce-asg-823f2525-3a00-318c-85cf-2ccfc033c170 | 0 | 0 | 0 | KEEP |

## Elastic IPs
| AllocationId | PublicIp | AssociationId | SSOT |
|--------------|----------|---------------|------|
| eipalloc-002ef833f162d04a8 | 3.35.64.225 | eipassoc-00921b844598cd01a | KEEP |
| eipalloc-0f252f3b5ff3cb865 | 52.79.34.81 | eipassoc-06b51bf4abab936f5 | KEEP |
| eipalloc-02bcb9e54f8f9cca3 | 54.180.207.91 | eipassoc-0e330c41df3a67367 | KEEP |

## Security Groups (VPC)
| GroupId | GroupName | ENICount | SSOT |
|---------|-----------|--------|------|
| sg-0f4069135b6215cad | academy-redis-sg | 1 | LEGACY_CANDIDATE |
| sg-06cfb1f23372e2597 | academy-rds | 1 | LEGACY_CANDIDATE |
| sg-01ecb5d8fc423d9e4 | academy-v1-vpce-sg | 4 | LEGACY_CANDIDATE |
| sg-0f04876abb91d1606 | academy-v1-sg-data | 1 | KEEP |
| sg-0d5305dcafd3ccc4d | academy-v1-sg-batch | 5 | KEEP |
| sg-0405c1afe368b4e6b | default | 2 | KEEP |
| sg-03cf8c8f38f477687 | academy-v1-sg-app | 4 | KEEP |

## Batch Compute Environments
| Name | State | Status | SSOT |
|------|-------|--------|------|
| academy-v1-video-batch-long-ce | ENABLED | VALID | LEGACY_CANDIDATE |
| academy-v1-video-batch-ce | ENABLED | VALID | KEEP |
| academy-v1-video-ops-ce | ENABLED | VALID | KEEP |

## Batch Job Queues
| Name | State | SSOT |
|------|-------|------|
| academy-v1-video-batch-long-queue | ENABLED | LEGACY_CANDIDATE |
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

