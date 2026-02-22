# 영상 워커 스케일링 운영 SSOT (Single Source of Truth)

**현행: Video = AWS Batch 전용.** 스케일링은 Batch CE/Job Queue가 담당. ASG/SQS 기반 Video 스케일링은 사용하지 않음.

---

## 1. 현행: Batch 전용

| 리소스 | 이름/값 | 설명 |
|--------|--------|------|
| Job Queue | academy-video-batch-queue | 제출된 Job 대기 |
| Compute Environment | academy-video-batch-ce-v3 | ARM64, SLR. 스케일은 Batch가 vCPU 기준으로 관리 |
| Job Definition | academy-video-batch-jobdef | retryStrategy.attempts=1, timeout 14400초 |
| 로그 | /aws/batch/academy-video-worker | CloudWatch Logs |

- **검증**: Batch job 제출 후 콘솔/CLI로 RUNNABLE → STARTING → RUNNING. 로그는 위 Log group에서 확인.
- **문서**: docs/VIDEO_BATCH_REFACTOR_PLAN_OF_RECORD.md, docs/VIDEO_BATCH_VERIFICATION_CHECKLIST.md

---

## 2. LEGACY: Video ASG/SQS 스케일링 (참고용, 미사용)

아래는 과거 Video ASG 방식 설명. **현재는 사용하지 않음.** (queue_depth_lambda ENABLE_VIDEO_METRICS=false, worker_autoscale ENABLE_VIDEO_WAKE=false 권장.)

### 2.1 리소스 및 역할 (레거시)

| 리소스 | 이름 | 설명 |
|--------|------|------|
| SQS 큐 | `academy-video-jobs` | 영상 작업 메시지. 스케일링 메트릭의 유일한 소스. |
| SQS DLQ | `academy-video-jobs-dlq` | 재시도 초과 메시지. |
| Lambda | `academy-worker-queue-depth-metric` | 1분 주기로 SQS(visible+notVisible) 합산 → `VideoQueueDepthTotal` 발행. |
| ASG | `academy-video-worker-asg` | TargetTracking 정책 `video-backlogcount-tt`: 메트릭 `VideoQueueDepthTotal`, TargetValue=1. |
| CloudWatch 메트릭 | Namespace `Academy/VideoProcessing`, MetricName `VideoQueueDepthTotal` | Dimensions: `WorkerType=Video`, `AutoScalingGroupName=academy-video-worker-asg`. |

### 2.2 스케일링 규칙 (레거시)

- **메트릭**: SQS `ApproximateNumberOfMessages` + `ApproximateNumberOfMessagesNotVisible` 합산만 사용.
- **DB backlog / internal API / 프론트 삭제 여부**는 스케일링에 사용하지 않음.
- **TargetValue**: 인스턴스 1대당 메시지 수 목표 (기본 1 → 영상 1개당 워커 1대).
- **Cooldown**: EC2 Auto Scaling put-scaling-policy TargetTracking does NOT support ScaleOutCooldown/ScaleInCooldown (Application Auto Scaling only). Use ASG default cooldowns.

---

### 2.3 스크립트 (레거시, 대부분 삭제됨)

| 용도 | 스크립트 | 사용 예 |
|------|----------|---------|
| 수정 적용(원큐) | `scripts\apply_video_worker_scaling_fix.ps1` | `.\scripts\apply_video_worker_scaling_fix.ps1 -Region ap-northeast-2` |
| 롤백 | 동일 스크립트 `-Rollback` | `.\scripts\apply_video_worker_scaling_fix.ps1 -Region ap-northeast-2 -Rollback` |
| 진단(원큐) | `scripts\diagnose_video_worker_full.ps1` | `.\scripts\diagnose_video_worker_full.ps1 -Region ap-northeast-2` |
| Lambda 코드만 배포 | `scripts\deploy_queue_depth_lambda.ps1` | `.\scripts\deploy_queue_depth_lambda.ps1 -Region ap-northeast-2` |
| ASG 정책만 적용 | `scripts\apply_video_target_tracking.ps1` | `.\scripts\apply_video_target_tracking.ps1 -Region ap-northeast-2` |

---

### 2.4 Lambda 코드 위치 및 동작 (레거시)

- **경로**: `infra/worker_asg/queue_depth_lambda/lambda_function.py`
- **동작**: EventBridge 1분 rate로 호출 → `academy-video-jobs` SQS get_queue_attributes(Visible, NotVisible) → 합산값을 `Academy/VideoProcessing` 네임스페이스에 `VideoQueueDepthTotal`로 PutMetricData. (Backlog API 호출 없음.)

---

### 2.5 검증 커맨드 (aws cli, 레거시)

```powershell
# 리전
$Region = "ap-northeast-2"

# SQS 상태
aws sqs get-queue-url --queue-name academy-video-jobs --region $Region --query QueueUrl --output text
aws sqs get-queue-attributes --queue-url <QueueUrl> --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --region $Region

# Lambda invoke → video_queue_depth_total 확인
aws lambda invoke --function-name academy-worker-queue-depth-metric --region $Region --cli-binary-format raw-in-base64-out response.json; Get-Content response.json

# 메트릭 (최근 1시간)
aws cloudwatch get-metric-statistics --region $Region --namespace Academy/VideoProcessing --metric-name VideoQueueDepthTotal --dimensions Name=WorkerType,Value=Video Name=AutoScalingGroupName,Value=academy-video-worker-asg --start-time (Get-Date).AddHours(-1).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") --end-time (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") --period 60 --statistics Average Maximum --output json

# ASG 정책
aws autoscaling describe-policies --auto-scaling-group-name academy-video-worker-asg --region $Region --output json

# ASG desired/min/max 및 활동
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names academy-video-worker-asg --region $Region --query "AutoScalingGroups[0].{Desired:DesiredCapacity,Min:MinSize,Max:MaxSize}"
aws autoscaling describe-scaling-activities --auto-scaling-group-name academy-video-worker-asg --region $Region --max-items 10 --output json
```

---

### 2.6 롤백 (레거시)

1. **정책만 롤백**: `.\scripts\apply_video_worker_scaling_fix.ps1 -Region ap-northeast-2 -Rollback`  
   → 백업된 TargetTracking 설정(이전 메트릭명)으로 복원. Lambda는 그대로 VideoQueueDepthTotal 발행.
2. **Lambda까지 롤백**(BacklogCount 사용으로 되돌리려는 경우):  
   - `infra/worker_asg/queue_depth_lambda/lambda_function.py`를 이전 커밋으로 되돌린 뒤  
   - `.\scripts\deploy_queue_depth_lambda.ps1 -Region ap-northeast-2` 실행.  
   - ASG 정책을 BacklogCount를 쓰는 설정으로 수동 변경하거나, 적용 스크립트 롤백 시 복원된 정책이 BacklogCount 기준이면 그대로 사용.

---

### 2.7 참고 문서

- `docs/VIDEO_WORKER_ASG_STRICT_INVESTIGATION.md` — 이전 BacklogCount 기반 구조 조사.
- `docs/SESSION_VIDEO_ASG_SSM_FINAL_STATE.md` — ASG/SSM 최종 상태 요약 (일부는 SQS 기반으로 변경됨).
