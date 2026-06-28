# V1 리소스 정리·재검증 결과

**리전:** ap-northeast-2 **갱신:** 2026-06-29T05:25:12.6421080+09:00 **모드:** DryRun
**SSOT:** docs/ssot/params.yaml

## 요약
| 항목 | 값 | 목표(V1 정상) |
|------|-----|----------------|
| running instances in VPC | 2 | API baseline 1 + messaging warm baseline 1 + active Batch/worker burst |
| Security Groups (VPC) | 5 | SSOT keep + in-use DB legacy SG only |
| Elastic IP total (region) | 3 | informational; academy ALB/public endpoints may use associated IPv4 |
| unassociated Elastic IP | 0 | 0 |
| ASG (academy/v1 + Batch-managed) | 6 | API/workers + Batch CE managed ASGs |

## Running instances (VPC)
| InstanceId | Name | Type | ASG |
|------------|------|------|-----|
| i-04f24828b1631da9d | academy-v1-messaging-worker | t4g.medium | academy-v1-messaging-worker-asg |
| i-03369fd74104bfedf | academy-v1-api | t4g.medium | academy-v1-api-asg |

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
| eipalloc-0eba69c4c7d97e209 | 3.34.175.213 | True | vpc-0831a2484f9b114c2 |
| eipalloc-0825e192fdd2d19a0 | 43.201.90.129 | True | vpc-0831a2484f9b114c2 |
| eipalloc-08adac5f5914cbac1 | 43.202.246.97 | True | vpc-0b89e02241aae4b0e |

## ASG (academy/v1 + Batch-managed)
| Name | Min | Desired | Max |
|------|-----|---------|-----|
| AWSBatch-academy-v1-video-batch-ce-200gb-asg-872e86dd-3192-3b90-80fe-0cdd8da6b3b7 | 0 | 0 | 0 |
| academy-v1-ai-worker-asg | 0 | 0 | 5 |
| academy-v1-api-asg | 1 | 1 | 3 |
| academy-v1-messaging-worker-asg | 1 | 1 | 3 |
| academy-v1-tools-worker-asg | 0 | 0 | 2 |
| academy-v1-video-ops-ce-asg-823f2525-3a00-318c-85cf-2ccfc033c170 | 0 | 0 | 0 |

---
실행: `pwsh -File scripts/v1/run-resource-cleanup.ps1 -AwsProfile default -Execute`

