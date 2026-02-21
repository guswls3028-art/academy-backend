# B1 Metric Schema Extraction Report

**조사 일시**: 2025-02-21  
**조사 기준**: 실제 코드 / IaC 증거, 추측 금지

---

## 1) queue_depth_lambda 메트릭 발행 스키마 추출

### 파일 경로

`infra/worker_asg/queue_depth_lambda/lambda_function.py`

### NAMESPACE 값

```32:32:infra/worker_asg/queue_depth_lambda/lambda_function.py
NAMESPACE = os.environ.get("METRIC_NAMESPACE", "Academy/Workers")
```

- 기본값: `"Academy/Workers"`

### MetricName 실제 문자열

```33:33:infra/worker_asg/queue_depth_lambda/lambda_function.py
METRIC_NAME = os.environ.get("METRIC_NAME", "QueueDepth")
```

- 기본값: `"QueueDepth"`

### Dimensions 구성 (Name/Value)

| Name       | Value     |
|------------|-----------|
| WorkerType | AI        |
| WorkerType | Video     |
| WorkerType | Messaging |

### Unit 값

```225:247:infra/worker_asg/queue_depth_lambda/lambda_function.py
    now = __import__("datetime").datetime.utcnow()
    metric_data = [
        {
            "MetricName": METRIC_NAME,
            "Dimensions": [{"Name": "WorkerType", "Value": "AI"}],
            "Value": float(ai_total),
            "Timestamp": now,
            "Unit": "Count",
        },
        {
            "MetricName": METRIC_NAME,
            "Dimensions": [{"Name": "WorkerType", "Value": "Video"}],
            "Value": float(video_visible),
            "Timestamp": now,
            "Unit": "Count",
        },
        {
            "MetricName": METRIC_NAME,
            "Dimensions": [{"Name": "WorkerType", "Value": "Messaging"}],
            "Value": float(messaging_visible),
            "Timestamp": now,
            "Unit": "Count",
        },
    ]
    cw.put_metric_data(Namespace=NAMESPACE, MetricData=metric_data)
```

- Unit: `"Count"`

### Period (Lambda 호출 주기)

- EventBridge `rate(1 minute)` 로 호출 → 메트릭은 **1분 간격**으로 publish됨.
- `put_metric_data` 호출 시 Period 파라미터는 없음 (조회 시 `get_metric_statistics`의 `--period`로 지정).

### put_metric_data 호출부 전체

```224:247:infra/worker_asg/queue_depth_lambda/lambda_function.py
    now = __import__("datetime").datetime.utcnow()
    metric_data = [
        {
            "MetricName": METRIC_NAME,
            "Dimensions": [{"Name": "WorkerType", "Value": "AI"}],
            "Value": float(ai_total),
            "Timestamp": now,
            "Unit": "Count",
        },
        {
            "MetricName": METRIC_NAME,
            "Dimensions": [{"Name": "WorkerType", "Value": "Video"}],
            "Value": float(video_visible),
            "Timestamp": now,
            "Unit": "Count",
        },
        {
            "MetricName": METRIC_NAME,
            "Dimensions": [{"Name": "WorkerType", "Value": "Messaging"}],
            "Value": float(messaging_visible),
            "Timestamp": now,
            "Unit": "Count",
        },
    ]
    cw.put_metric_data(Namespace=NAMESPACE, MetricData=metric_data)
```

---

## 2) ASG 스케일링이 "어떤 메트릭"을 소비하는지 IaC 위치 추출

### grep 결과

```
grep -R "TargetTracking" infra/
grep -R "ScalingPolicy" infra/
grep -R "Academy/Workers" infra/
```

- `infra/worker_asg/README.md`
- `infra/worker_asg/iam_policy_queue_depth_lambda.json`
- `infra/worker_asg/iam_policy_queue_depth_lambda.min.json`

스케일 정책은 `scripts/deploy_worker_asg.ps1`에서 생성됨 (IaC는 infra/, 스크립트는 scripts/).

### Video Worker ASG 스케일링 정책

**Video Worker ASG는 TargetTracking 정책을 사용하지 않음.**

```297:324:scripts/deploy_worker_asg.ps1
# Video: Lambda 단독 컨트롤. TargetTracking 정책 제거 (delete).
$policyMessaging = @"
...
"@
...
aws application-autoscaling delete-scaling-policy --service-namespace ec2 --resource-id $ResourceIdVideo `
    --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" --policy-name "QueueDepthTargetTracking" --region $Region 2>$null
```

- Video ASG: `delete-scaling-policy`로 QueueDepthTargetTracking 제거.
- 스케일링: Lambda 내 `set_video_worker_desired()` → `autoscaling_client.set_desired_capacity()` 직접 호출.
- CloudWatch 메트릭 `Academy/Workers QueueDepth WorkerType=Video`는 **발행만** 되고, Video ASG 스케일링에는 사용되지 않음.

### AI / Messaging Worker ASG — TargetTracking 정책

**파일**: `scripts/deploy_worker_asg.ps1`

```277:312:scripts/deploy_worker_asg.ps1
$policyAi = @"
{
  "TargetTrackingScalingPolicyConfiguration": {
    "TargetValue": $TargetMessagesPerInstance,
    "PredefinedMetricSpecification": null,
    "CustomizedMetricSpecification": {
      "MetricName": "QueueDepth",
      "Namespace": "Academy/Workers",
      "Dimensions": [{"Name": "WorkerType", "Value": "AI"}],
    "Statistic": "Average"
    },
    "ScaleInCooldown": 300,
    "ScaleOutCooldown": 60
  }
}
"@
# Video: Lambda 단독 컨트롤. TargetTracking 정책 제거 (delete).
$policyMessaging = @"
{
  "TargetTrackingScalingPolicyConfiguration": {
    "TargetValue": $TargetMessagesPerInstance,
    "CustomizedMetricSpecification": {
      "MetricName": "QueueDepth",
      "Namespace": "Academy/Workers",
      "Dimensions": [{"Name": "WorkerType", "Value": "Messaging"}],
      "Statistic": "Average"
    },
    "ScaleInCooldown": 300,
    "ScaleOutCooldown": 60
  }
}
"@
```

| 항목 | AI | Messaging |
|------|----|-----------|
| 정책명 | QueueDepthTargetTracking | QueueDepthTargetTracking |
| MetricName | QueueDepth | QueueDepth |
| Namespace | Academy/Workers | Academy/Workers |
| Dimensions | WorkerType=AI | WorkerType=Messaging |
| Statistic | Average | Average |
| TargetValue | $TargetMessagesPerInstance (기본 20) | 동일 |
| ScaleInCooldown | 300 | 300 |
| ScaleOutCooldown | 60 | 60 |

### Video ASG 요약

| 항목 | 값 |
|------|-----|
| TargetTracking 사용 여부 | 사용 안 함 (delete-scaling-policy) |
| 스케일 제어 방식 | Lambda `set_desired_capacity` 직접 호출 |
| CloudWatch 메트릭 소비 | 없음 (발행만 됨) |

---

## 3) DB backlog 메트릭 후보 구현 가능성 확인

### Video 모델 — tenant_id 접근 경로

**파일**: `apps/support/video/models.py`

```39:45:apps/support/video/models.py
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="videos",
    )
```

- Video에 `tenant_id` 필드 없음.
- Video → Session (ForeignKey) → Lecture (ForeignKey) → Tenant (ForeignKey).
- tenant 접근: `video.session.lecture.tenant_id` (필드 직접 아님, **JOIN 필요**).

### get_video_queryset_with_relations 정의

**파일**: `academy/adapters/db/django/repositories_video.py`

```24:29:academy/adapters/db/django/repositories_video.py
def get_video_queryset_with_relations():
    """VideoViewSet 기본 queryset. upload_complete enqueue 시 video.session.lecture.tenant 필요."""
    from apps.support.video.models import Video
    return Video.objects.all().select_related(
        "session", "session__lecture", "session__lecture__tenant"
    )
```

- `select_related`: `session`, `session__lecture`, `session__lecture__tenant`
- tenant 도달 경로: `Video → Session → Lecture → Tenant`

### tenant별 backlog count 시 JOIN 필요 여부

**필요함.**

- Video 테이블에 `tenant_id` 컬럼 없음.
- `session__lecture__tenant_id` 또는 `session__lecture__tenant`로 접근해야 하므로 JOIN 필요.

예시 쿼리:

```python
from django.db.models import Count
from apps.support.video.models import Video

# tenant별 UPLOADED + PROCESSING count
Video.objects.filter(
    status__in=[Video.Status.UPLOADED, Video.Status.PROCESSING]
).values("session__lecture__tenant_id").annotate(count=Count("id"))
```

- `values("session__lecture__tenant_id")` → `video → session → lecture` JOIN 후 `lecture.tenant_id` 기준 GROUP BY.
- JOIN 비용: `video INNER JOIN session ON ... INNER JOIN lecture ON ...` 필요.

---

## 4) 검증 CLI 결과

### 실행 명령

```bash
aws cloudwatch list-metrics --namespace "Academy/Workers" --region ap-northeast-2
```

### 실행 결과

```
An error occurred (InvalidClientTokenId) when calling the ListMetrics operation: The security token included in the request is invalid.
```

- AWS 자격 증명 미설정/만료로 실행 불가.
- 배포 계정에서 아래 명령으로 검증 가능.

### 검증용 CLI (배포 계정에서 실행)

```bash
# 1) Academy/Workers 메트릭 목록
aws cloudwatch list-metrics --namespace "Academy/Workers" --region ap-northeast-2

# 2) Video QueueDepth 메트릭 통계 (최근 30분, 60초 주기, Sum)
# Linux/macOS
aws cloudwatch get-metric-statistics \
  --namespace "Academy/Workers" \
  --metric-name "QueueDepth" \
  --dimensions Name=WorkerType,Value=Video \
  --start-time "$(date -u -d '30 minutes ago' +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --period 60 \
  --statistics Sum \
  --region ap-northeast-2

# Windows PowerShell
$end = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$start = (Get-Date).AddMinutes(-30).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
aws cloudwatch get-metric-statistics --namespace "Academy/Workers" --metric-name "QueueDepth" --dimensions Name=WorkerType,Value=Video --start-time $start --end-time $end --period 60 --statistics Sum --region ap-northeast-2
```

---

## 요약 표

| 항목 | 값 |
|------|-----|
| NAMESPACE | Academy/Workers |
| MetricName | QueueDepth |
| Dimensions | WorkerType=AI, WorkerType=Video, WorkerType=Messaging |
| Unit | Count |
| Period (Lambda 호출) | 1분 (EventBridge rate) |
| Video ASG 스케일링 | TargetTracking 미사용, Lambda 직접 set_desired_capacity |
| AI/Messaging ASG | TargetTracking, QueueDepth, WorkerType별 |
| Video → tenant_id | session.lecture.tenant_id, JOIN 필요 |
| tenant별 backlog count | JOIN 필요 (session, lecture) |
