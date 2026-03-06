# V1 리소스 정리·재검증 결과

**리전:** ap-northeast-2 **갱신:** 2026-03-06T15:17:40.8948221+09:00 **모드:** DryRun
**SSOT:** docs/00-SSOT/v1/params.yaml

## 요약
| 항목 | 값 | 목표(V1 정상) |
|------|-----|----------------|
| running instances | 0 | 3 |
| Security Groups (VPC) | 13 | 6~8 |
| Elastic IP | 4 | 0 |
| ASG (academy/v1) | 4 | 3 + Batch ops |

## Running instances (Project=academy)
| InstanceId | Name | Type |
|------------|------|------|

## Security Groups (VPC)
| GroupId | GroupName |
|---------|-----------|
| sg-0405c1afe368b4e6b | default |
| sg-011ed1d9eb4a65b8f | academy-video-batch-sg |
| sg-0ba6fc12209bec7de | academy-v1-sg-batch |
| sg-0051cc8f79c04b058 | academy-api-sg |
| sg-02692600fbf8e26f7 | academy-worker-sg |
| sg-0944a30cabd0c022e | academy-lambda-endpoint-sg |
| sg-0f4069135b6215cad | academy-redis-sg |
| sg-00d2fb147d61f5cd8 | academy-v1-vpce-sg |
| sg-088fa3315c12754d0 | academy-v1-sg-app |
| sg-0ff11f1b511861447 | academy-lambda-internal-sg |
| sg-0caaa6c43e12758e6 | academy-lambda-video-sg |
| sg-0f04876abb91d1606 | academy-v1-sg-data |
| sg-06cfb1f23372e2597 | academy-rds |

## Elastic IP
| AllocationId | PublicIp | Associated |
|--------------|----------|------------|
| eipalloc-005028ec477ae0819 | 3.37.217.75 | True |
| eipalloc-0339e2c05fdf6d349 | 43.203.92.254 | True |
| eipalloc-0cf9f6d0e100d6787 | 54.180.111.188 | True |
| eipalloc-02bcb9e54f8f9cca3 | 54.180.207.91 | True |

## ASG (academy/v1)
| Name | Min | Desired | Max |
|------|-----|---------|-----|
| academy-v1-ai-worker-asg | 1 | 1 | 10 |
| academy-v1-api-asg | 2 | 2 | 4 |
| academy-v1-messaging-worker-asg | 1 | 1 | 10 |
| academy-v1-video-ops-ce-asg-823f2525-3a00-318c-85cf-2ccfc033c170 | 0 | 0 | 0 |

---
실행: `pwsh -File scripts/v1/run-resource-cleanup.ps1 -AwsProfile default -Execute`

