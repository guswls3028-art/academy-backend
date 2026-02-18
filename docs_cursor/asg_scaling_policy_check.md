# ASG 스케일링 정책 확인 및 CPU 기반 정책 제거 가이드

## 현재 구조

- **Video Worker ASG**: `academy-video-worker-asg`
- **스케일링 방식**: SQS 큐 깊이 기반 Target Tracking (이미 설정됨)
- **Lambda**: `academy-worker-queue-depth-metric` (1분마다 SQS 메트릭 퍼블리시)

## 1. 현재 스케일링 정책 확인

```bash
# Video Worker ASG의 모든 스케일링 정책 확인
aws application-autoscaling describe-scaling-policies \
  --service-namespace ec2 \
  --resource-id "auto-scaling-group/academy-video-worker-asg" \
  --region ap-northeast-2 \
  --output json

# ASG 자체의 스케일링 정책 확인 (Step Scaling, Simple Scaling 등)
aws autoscaling describe-policies \
  --auto-scaling-group-name academy-video-worker-asg \
  --region ap-northeast-2 \
  --output json
```

## 2. CPU 기반 정책이 있는 경우 제거

### Application Auto Scaling (Target Tracking) 정책 제거

```bash
# CPU 기반 Target Tracking 정책이 있다면 제거
aws application-autoscaling delete-scaling-policy \
  --service-namespace ec2 \
  --resource-id "auto-scaling-group/academy-video-worker-asg" \
  --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" \
  --policy-name "CPUTargetTracking" \
  --region ap-northeast-2
```

### EC2 Auto Scaling 정책 제거

```bash
# CPU 기반 Step Scaling 또는 Simple Scaling 정책 제거
aws autoscaling delete-policy \
  --auto-scaling-group-name academy-video-worker-asg \
  --policy-name "CPU-based-scaling" \
  --region ap-northeast-2
```

## 3. 올바른 정책 확인 (SQS 기반만 남아야 함)

```bash
# 정상: QueueDepthTargetTracking 정책만 있어야 함
aws application-autoscaling describe-scaling-policies \
  --service-namespace ec2 \
  --resource-id "auto-scaling-group/academy-video-worker-asg" \
  --region ap-northeast-2 \
  --query "ScalingPolicies[?PolicyName=='QueueDepthTargetTracking']" \
  --output json
```

## 4. 정책 설정 확인

현재 설정되어야 하는 정책:

```json
{
  "PolicyName": "QueueDepthTargetTracking",
  "PolicyType": "TargetTrackingScaling",
  "TargetTrackingScalingPolicyConfiguration": {
    "TargetValue": 20.0,
    "CustomizedMetricSpecification": {
      "MetricName": "QueueDepth",
      "Namespace": "Academy/Workers",
      "Dimensions": [{"Name": "WorkerType", "Value": "Video"}],
      "Statistic": "Average"
    },
    "ScaleInCooldown": 300,
    "ScaleOutCooldown": 60
  }
}
```

## 5. Scale-in 안전성 확인

현재 설정:
- **ScaleInCooldown: 300초 (5분)**
- **TargetValue: 20** (큐에 20개 메시지당 1 인스턴스)

이 의미:
- 큐가 비어도 **즉시 scale-in 안 함**
- 5분 쿨다운 후에만 scale-in 시도
- 하지만 **작업 중인 메시지(NotVisible)는 Lambda가 직접 조정하지 않음**

## 6. 개선 사항 (선택)

Video Worker도 Lambda에서 직접 조정하도록 수정하면 더 안전:

```python
# lambda_function.py에 추가
def set_video_worker_asg_desired(autoscaling_client, video_visible: int, video_in_flight: int) -> None:
    """Video 워커: 작업 중이면 scale-in 안 함."""
    VIDEO_WORKER_ASG_NAME = "academy-video-worker-asg"
    VIDEO_WORKER_ASG_MAX = 20
    
    video_total = video_visible + video_in_flight
    if video_total > 0:
        # 작업 있으면 최소 1대 유지
        new_desired = min(VIDEO_WORKER_ASG_MAX, max(1, math.ceil(video_total / 20)))
    else:
        # 작업 없으면 min=1이므로 1대 유지
        new_desired = 1
    
    # 현재 desired와 다르면 업데이트
    # ... (AI 워커와 동일한 로직)
```

## 7. 확인 체크리스트

- [ ] CPU 기반 정책 없음 확인
- [ ] QueueDepthTargetTracking 정책만 존재 확인
- [ ] Lambda가 정상 실행 중인지 확인 (CloudWatch Logs)
- [ ] CloudWatch 메트릭 `Academy/Workers QueueDepth WorkerType=Video` 확인
- [ ] ScaleInCooldown 300초 이상 확인

## 8. 문제 발생 시

만약 작업 중에 scale-in이 발생한다면:

1. **ScaleInCooldown 증가**: 300 → 600초 (10분)
2. **Lambda에서 직접 조정**: Video Worker도 Lambda가 desired capacity 직접 관리
3. **TargetValue 조정**: 20 → 10 (더 보수적으로)
