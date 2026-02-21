# B1 Scaling Data Extraction Report

**조사 일시**: 2025-02-21  
**조사 기준**: 실제 코드, grep 결과, 추측 금지

---

## 조사 대상 1: reconcile_video_processing Management Command

### grep 결과

```
grep -R "reconcile_video_processing" .
```

```
.\apps\support\video\management\commands\reconcile_video_processing.py
  1:# PATH: apps/support/video/management/commands/reconcile_video_processing.py
  9:  python manage.py reconcile_video_processing

.\docs\VIDEO_3GATE_PATCH_SUMMARY.md
  96:| `apps/support/video/management/commands/reconcile_video_processing.py` | 신규: Reclaim + Re-enqueue 커맨드 |
  ...
```

### 파일 경로

`apps/support/video/management/commands/reconcile_video_processing.py`

### Command class 코드 본문

```python
class Command(BaseCommand):
    help = "Reclaim PROCESSING videos (lease expired or no heartbeat) and re-enqueue to SQS"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only log what would be reclaimed, do not reclaim or enqueue",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        repo = DjangoVideoRepository()
        queue = VideoSQSQueue()

        now = timezone.now()
        # PROCESSING 상태인 비디오
        qs = (
            get_video_queryset_with_relations()
            .filter(status=Video.Status.PROCESSING)
            .order_by("id")
        )

        reclaimed = 0
        enqueued = 0

        for video in qs:
            tenant_id = _tenant_id_from_video(video)
            lease_expired = video.leased_until is not None and video.leased_until < now
            no_heartbeat = tenant_id is not None and not has_video_heartbeat(tenant_id, video.id)

            if not lease_expired and not no_heartbeat:
                continue

            prev_leased_by = getattr(video, "leased_by", "") or ""
            prev_leased_until = getattr(video, "leased_until", None)

            force = lease_expired or no_heartbeat
            if dry_run:
                self.stdout.write(
                    f"DRY-RUN reclaim | video_id={video.id} tenant_id={tenant_id} "
                    f"prev_leased_by={prev_leased_by} prev_leased_until={prev_leased_until} "
                    f"lease_expired={lease_expired} no_heartbeat={no_heartbeat} force={force}"
                )
                reclaimed += 1
                continue

            if not repo.try_reclaim_video(video.id, force=force):
                continue

            reclaimed += 1
            self.stdout.write(
                f"RECLAIMED | video_id={video.id} tenant_id={tenant_id} "
                f"prev_leased_by={prev_leased_by} prev_leased_until={prev_leased_until}"
            )

            video.refresh_from_db()
            if video.status != Video.Status.UPLOADED:
                self.stderr.write(f"WARNING: video {video.id} status={video.status} after reclaim")
                continue

            if queue.enqueue(video):
                enqueued += 1
                self.stdout.write(self.style.SUCCESS(f"RE_ENQUEUED | video_id={video.id}"))

        self.stdout.write(
            self.style.SUCCESS(f"Done: reclaimed={reclaimed} enqueued={enqueued}" + (" (dry-run)" if dry_run else ""))
        )
```

### 내부 호출 Service / Repository 구조

| 구성요소 | 출처 | 역할 |
|----------|------|------|
| `get_video_queryset_with_relations` | `academy.adapters.db.django.repositories_video` | Video queryset (select_related session, lecture, tenant) |
| `DjangoVideoRepository` | `academy.adapters.db.django.repositories_video` | `try_reclaim_video(video_id, force=force)` |
| `VideoSQSQueue` | `apps.support.video.services.sqs_queue` | `enqueue(video)` |
| `has_video_heartbeat` | `apps.support.video.redis_status_cache` | Redis 기반 heartbeat 여부 |

### Video 상태 조회 로직 존재 여부

**존재함.**

```python
qs = (
    get_video_queryset_with_relations()
    .filter(status=Video.Status.PROCESSING)
    .order_by("id")
)
```

- `status=Video.Status.PROCESSING` 조건으로 PROCESSING 상태 비디오만 조회

---

## 조사 대상 2: Video 모델 상태 카운트용 Django Query 가능 여부

### grep 결과

```
grep -R "class Video" .
```

(관련: `.\apps\support\video\models.py` L31 `class Video(TimestampModel)`)

### Video 모델 파일 본문 (관련 부분)

```python
class Video(TimestampModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "업로드 대기"
        UPLOADED = "UPLOADED", "업로드 완료"
        PROCESSING = "PROCESSING", "처리중"
        READY = "READY", "사용 가능"
        FAILED = "FAILED", "실패"

    # ... (중략)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # ...

    class Meta:
        ordering = ["order", "id"]
        indexes = [
            models.Index(fields=["status", "updated_at"]),
            models.Index(fields=["leased_until", "status"]),
        ]
```

### (status = UPLOADED OR PROCESSING) COUNT 가능 여부

**가능함.**

```python
Video.objects.filter(status__in=[Video.Status.UPLOADED, Video.Status.PROCESSING]).count()
```

- `status` 필드 존재
- `choices=Status.choices`로 `UPLOADED`, `PROCESSING` 값 사용 가능

### status 필드 DB Index 존재 여부

**존재함.**

- `status` 필드: `db_index=True` (L78)
- `Meta.indexes`:
  - `["status", "updated_at"]`
  - `["leased_until", "status"]`

---

## 조사 대상 3: queue_depth_lambda의 CloudWatch GetMetricData 권한 보유 여부

### grep 결과

```
grep -R "queue_depth_lambda" infra/
```

```
infra\worker_asg\README.md
  7:- **queue_depth_lambda**: 1분마다 SQS visible 메시지 수를 CloudWatch `Academy/Workers` 네임스페이스에 퍼블리시 ...
  18:   - `infra/worker_asg/iam_policy_queue_depth_lambda.json` 참고해 인라인 정책 추가 또는 기존 정책에 Statement 추가.
```

### IaC 파일 (IAM 정책)

`infra/worker_asg/iam_policy_queue_depth_lambda.json`

### 코드 본문 (포맷팅)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SQSGetQueueAttributes",
      "Effect": "Allow",
      "Action": ["sqs:GetQueueUrl", "sqs:GetQueueAttributes"],
      "Resource": "*"
    },
    {
      "Sid": "CloudWatchPutMetric",
      "Effect": "Allow",
      "Action": ["cloudwatch:PutMetricData"],
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "cloudwatch:namespace": "Academy/Workers"
        }
      }
    },
    {
      "Sid": "AutoScalingSetDesired",
      "Effect": "Allow",
      "Action": [
        "autoscaling:DescribeAutoScalingGroups",
        "autoscaling:SetDesiredCapacity"
      ],
      "Resource": "*"
    },
    {
      "Sid": "SSMVideoStableZero",
      "Effect": "Allow",
      "Action": ["ssm:GetParameter", "ssm:PutParameter", "ssm:DeleteParameter"],
      "Resource": [
        "arn:aws:ssm:*:*:parameter/academy/workers/video/*",
        "arn:aws:ssm:*:*:parameter/academy/video-worker-asg/*"
      ]
    }
  ]
}
```

### cloudwatch:GetMetricData / cloudwatch:GetMetricStatistics

**iam_policy_queue_depth_lambda.json에는 둘 다 없음.**

- CloudWatch 관련 권한: `cloudwatch:PutMetricData` 만 존재
- `cloudwatch:GetMetricData`, `cloudwatch:GetMetricStatistics`는 미포함

---

## 조사 대상 4: boto3 기반 CloudWatch Metric 유틸리티 존재 여부

### grep 결과

```
grep -R "boto3" .
grep -R "cloudwatch|put_metric_data|get_metric_data" .  # (각각)
```

**boto3 사용 파일** (CloudWatch 관련):
- `infra/worker_asg/queue_depth_lambda/lambda_function.py`

**put_metric_data**:
```
.\infra\worker_asg\queue_depth_lambda\lambda_function.py
  247:    cw.put_metric_data(Namespace=NAMESPACE, MetricData=metric_data)
```

**get_metric_data**:
```
(검색 결과 없음)
```

### 해당 파일 코드 본문

`infra/worker_asg/queue_depth_lambda/lambda_function.py`:

```python
import boto3
# ...
def lambda_handler(event: dict, context: Any) -> dict:
    sqs = boto3.client("sqs", region_name=REGION, config=BOTO_CONFIG)
    cw = boto3.client("cloudwatch", region_name=REGION, config=BOTO_CONFIG)
    autoscaling = boto3.client("autoscaling", region_name=REGION, config=BOTO_CONFIG)
    # ...
    cw.put_metric_data(Namespace=NAMESPACE, MetricData=metric_data)
```

### 결론

| 항목 | 존재 여부 | 위치 |
|------|-----------|------|
| boto3 CloudWatch Put | 예 | `queue_depth_lambda/lambda_function.py` (cw.put_metric_data) |
| boto3 CloudWatch Get (get_metric_data) | 아니오 | 프로젝트 내 사용 코드 없음 |
| 별도 CloudWatch 유틸리티 모듈 | 아니오 | Lambda 핸들러 내부 인라인 호출만 존재 |

---

## 요약 표

| 조사 대상 | 핵심 결과 |
|-----------|-----------|
| reconcile_video_processing | `apps/support/video/management/commands/reconcile_video_processing.py`, DjangoVideoRepository + VideoSQSQueue + get_video_queryset_with_relations, PROCESSING 조회 로직 있음 |
| Video (UPLOADED\|PROCESSING) count | 가능, status 필드 `db_index=True`, Meta.indexes에 status 포함 |
| queue_depth_lambda GetMetricData | IAM 정책에 **없음** (PutMetricData만 있음) |
| boto3 CloudWatch 유틸리티 | put_metric_data: Lambda 내부 사용. get_metric_data: 없음. 별도 유틸 모듈 없음 |
