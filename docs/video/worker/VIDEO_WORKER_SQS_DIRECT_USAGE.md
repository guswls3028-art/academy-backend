# Video Worker SQS Direct (Lambda-free) 사용법

## 개요
- **목적**: Lambda를 Scaling Path에서 제거, SQS 기본 메트릭 + CloudWatch Metric Math로 직접 스케일링
- **변경 전**: SQS → Lambda → Custom Metric (Academy/VideoProcessing) → ASG
- **변경 후**: SQS (Visible + NotVisible) → CloudWatch Metric Math → ASG

## 1. 변경된 ScalingPolicy 구조 (TargetTrackingConfiguration)

```json
{
  "CustomizedMetricSpecification": {
    "Metrics": [
      {
        "Id": "m1",
        "MetricStat": {
          "Metric": {
            "MetricName": "ApproximateNumberOfMessagesVisible",
            "Namespace": "AWS/SQS",
            "Dimensions": [{"Name": "QueueName", "Value": "academy-video-jobs"}]
          },
          "Stat": "Sum",
          "Period": 60
        },
        "ReturnData": false
      },
      {
        "Id": "m2",
        "MetricStat": {
          "Metric": {
            "MetricName": "ApproximateNumberOfMessagesNotVisible",
            "Namespace": "AWS/SQS",
            "Dimensions": [{"Name": "QueueName", "Value": "academy-video-jobs"}]
          },
          "Stat": "Sum",
          "Period": 60
        },
        "ReturnData": false
      },
      {
        "Id": "e1",
        "Expression": "m1 + m2",
        "Label": "VideoQueueDepthTotal",
        "ReturnData": true
      }
    ]
  },
  "TargetValue": 1.0,
  "DisableScaleIn": false,
  "EstimatedInstanceWarmup": 180
}
```

- **TargetValue 1.0**: worker 1대당 메시지 1개 처리 기준
- **ScaleOutCooldown / ScaleInCooldown**: EC2 ASG Target Tracking에서 미지원으로 제외

## 2. scripts\video_worker_scaling_sqs_direct.ps1 사용법

```powershell
# 기본 적용 (ap-northeast-2)
.\scripts\video_worker_scaling_sqs_direct.ps1

# 리전 지정
.\scripts\video_worker_scaling_sqs_direct.ps1 -Region ap-northeast-2

# 프로필 지정
.\scripts\video_worker_scaling_sqs_direct.ps1 -Region ap-northeast-2 -Profile myprofile

# DryRun: 백업 + 적용할 Metric Math JSON 출력, 실제 적용 없음
.\scripts\video_worker_scaling_sqs_direct.ps1 -DryRun
.\scripts\video_worker_scaling_sqs_direct.ps1 -Region ap-northeast-2 -DryRun
```

## 3. 롤백 방법

```powershell
# 백업된 정책으로 복원 (backups\video_worker\metricmath_<timestamp>)
.\scripts\video_worker_scaling_sqs_direct.ps1 -Rollback
.\scripts\video_worker_scaling_sqs_direct.ps1 -Region ap-northeast-2 -Rollback
```

- 롤백 시 `metricmath_*` 폴더 중 최신 백업을 사용
- 복원 후에는 이전에 사용하던 Lambda 기반 정책으로 돌아감

## 4. 검증 CLI 예시

```powershell
# 정책 확인: Namespace=AWS/SQS 여야 함 (Academy/VideoProcessing이면 FAIL)
aws autoscaling describe-policies `
  --auto-scaling-group-name academy-video-worker-asg `
  --region ap-northeast-2 `
  --output json

# Metrics 배열에 AWS/SQS 메트릭이 있어야 함
aws autoscaling describe-policies `
  --auto-scaling-group-name academy-video-worker-asg `
  --region ap-northeast-2 `
  --query "ScalingPolicies[?PolicyName=='video-backlogcount-tt'].TargetTrackingConfiguration.CustomizedMetricSpecification" `
  --output json

# Lambda 미사용 확인: 위 결과에 Namespace=Academy/VideoProcessing 이 없어야 함
```

## 5. ACCEPTANCE TEST

| 테스트 | 기대 결과 |
|--------|-----------|
| 영상 1개 업로드 | SQS visible=1 → ASG desired≈1 → 처리 완료 → 0 |
| 영상 5개 업로드 | desired≈5 → 처리 완료 → 0 |
| Lambda 중지 | 스케일링 정상 동작 (Lambda 미사용이므로 영향 없음) |

## 6. Lambda 처리
- `academy-worker-queue-depth-metric`는 **삭제하지 않음** (AI/Messaging 영향 가능성)
- Video Worker ASG Scaling Path에서는 **완전 제외**
