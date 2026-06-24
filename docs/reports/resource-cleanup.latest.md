# V1 리소스 정리·재검증 결과

**리전:** ap-northeast-2 **갱신:** 2026-06-25T02:58:36.2240759+09:00 **모드:** DryRun
**SSOT:** docs/ssot/params.yaml

## 요약
| 항목 | 값 | 목표(V1 정상) |
|------|-----|----------------|
| running instances in VPC | 1 | API baseline 1 + active Batch/worker burst |
| Security Groups (VPC) | 5 | SSOT keep + in-use DB legacy SG only |
| Elastic IP total (region) | 3 | informational; academy ALB/public endpoints may use associated IPv4 |
| unassociated Elastic IP | 0 | 0 |
| ASG (academy/v1 + Batch-managed) | 6 | API/workers + Batch CE managed ASGs |

## Running instances (VPC)
| InstanceId | Name | Type | ASG |
|------------|------|------|-----|
| i-08ebf442a47a3ce23 | academy-v1-api | t4g.medium | academy-v1-api-asg |

## Security Groups (VPC)
| GroupId | GroupName |
|---------|-----------|
| sg-0f04876abb91d1606 | academy-v1-sg-data |
| sg-0d5305dcafd3ccc4d | academy-v1-sg-batch |
| sg-0405c1afe368b4e6b | default |
| sg-03cf8c8f38f477687 | academy-v1-sg-app |
| sg-06cfb1f23372e2597 | academy-rds |

## Elastic IP
| AllocationId | PublicIp | Associated | VpcId |
|--------------|----------|------------|-------|
| eipalloc-0ca89cf3a856fc873 | 13.209.151.18 | True | vpc-0831a2484f9b114c2 |
| eipalloc-08adac5f5914cbac1 | 43.202.246.97 | True | vpc-0b89e02241aae4b0e |
| eipalloc-000eaa654e4f3799e | 54.116.238.41 | True | vpc-0831a2484f9b114c2 |

## ASG (academy/v1 + Batch-managed)
| Name | Min | Desired | Max |
|------|-----|---------|-----|
| AWSBatch-academy-v1-video-batch-ce-200gb-asg-872e86dd-3192-3b90-80fe-0cdd8da6b3b7 | 0 | 0 | 0 |
| academy-v1-ai-worker-asg | 0 | 0 | 5 |
| academy-v1-api-asg | 1 | 1 | 3 |
| academy-v1-messaging-worker-asg | 0 | 0 | 3 |
| academy-v1-tools-worker-asg | 0 | 0 | 2 |
| academy-v1-video-ops-ce-asg-823f2525-3a00-318c-85cf-2ccfc033c170 | 0 | 0 | 0 |

---
실행: `pwsh -File scripts/v1/run-resource-cleanup.ps1 -AwsProfile default -Execute`

