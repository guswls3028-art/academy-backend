# VIDEO WORKER ASG DOWNSCALE ROOT CAUSE — Strict Investigation Report

**조사 일시**: 2025-02-21  
**조사 기준**: 실제 코드, 추측 금지

---

## 1. 현재 Scaling 계산식

### 1.1 Lambda 함수

**파일**: `infra/worker_asg/queue_depth_lambda/lambda_function.py`

#### SQS 조회

```50:68:infra/worker_asg/queue_depth_lambda/lambda_function.py
def get_queue_counts(sqs_client, queue_name: str) -> tuple[int, int]:
    """(visible, inflight) = ApproximateNumberOfMessagesVisible, ApproximateNumberOfMessagesNotVisible."""
    ...
        visible = int(a.get("ApproximateNumberOfMessages", 0))
        in_flight = int(a.get("ApproximateNumberOfMessagesNotVisible", 0))
        return visible, in_flight
```

- `visible` = `ApproximateNumberOfMessages` (SQS 표준 속성: visible messages)
- `inflight` = `ApproximateNumberOfMessagesNotVisible` (수신되어 visibility timeout 대기 중인 메시지)

#### VIDEO_SCALE_VISIBLE_ONLY 분기

```40:42:infra/worker_asg/queue_depth_lambda/lambda_function.py
# 1이면 desired = visible 기반만 (inflight 제외). Worker fast ACK 사용 시 권장.
VIDEO_SCALE_VISIBLE_ONLY = os.environ.get("VIDEO_SCALE_VISIBLE_ONLY", "0") == "1"
```

- 기본값: `"0"` → `False`
- `deploy_worker_asg.ps1` L82-83: `VIDEO_FAST_ACK=1` 사용 시 `VIDEO_SCALE_VISIBLE_ONLY=1` 설정

#### video_visible, video_inflight 사용

```215:220:infra/worker_asg/queue_depth_lambda/lambda_function.py
    (video_visible, video_in_flight) = get_queue_counts(sqs, VIDEO_QUEUE)
    ...
    video_scale_result = set_video_worker_desired(autoscaling, ssm, video_visible, video_in_flight)
```

- `video_visible`, `video_in_flight`는 **SQS `get_queue_attributes`만** 사용. DB 조회 없음.

#### video_backlog_add, video_new_desired, desired_candidate 계산식

```127:130:infra/worker_asg/queue_depth_lambda/lambda_function.py
    backlog_add = min(visible, MAX_BACKLOG_ADD)
    desired_candidate = backlog_add if VIDEO_SCALE_VISIBLE_ONLY else (inflight + backlog_add)
    new_desired_raw = max(VIDEO_WORKER_ASG_MIN, min(VIDEO_WORKER_ASG_MAX, desired_candidate))
```

| 단계 | 수식 | VIDEO_SCALE_VISIBLE_ONLY=1 | VIDEO_SCALE_VISIBLE_ONLY=0 |
|------|------|----------------------------|----------------------------|
| backlog_add | min(visible, MAX_BACKLOG_ADD) | min(visible, 5) | min(visible, 5) |
| desired_candidate | 위 분기 | **backlog_add만** | inflight + backlog_add |
| new_desired_raw | clamp(MIN, MAX, desired_candidate) | clamp(1, 20, backlog_add) | clamp(1, 20, inflight+backlog_add) |

#### Scale-in 조건

```134:151:infra/worker_asg/queue_depth_lambda/lambda_function.py
    if visible > 0 or inflight > 0:
        _delete_stable_zero_param(ssm_client)
        new_desired = new_desired_raw
        decision = "scale_out" if new_desired > 0 else "hold"
    else:
        stable_since = stable_zero_since_epoch
        if stable_since == 0:
            _set_stable_zero_since(ssm_client, now_ts)
            new_desired = None  # do not change (keep current)
            decision = "hold"
        elif (now_ts - stable_since) >= STABLE_ZERO_SECONDS:
            new_desired = VIDEO_WORKER_ASG_MIN
            _delete_stable_zero_param(ssm_client)
            decision = "scale_in"
        else:
            new_desired = None
            decision = "hold"
```

- **scale-in**: `visible==0 AND inflight==0` 가 `STABLE_ZERO_SECONDS`(기본 1200초) 이상 지속 시 `new_desired = VIDEO_WORKER_ASG_MIN`(1)

---

## 2. FAST_ACK 영향 분석

### 2.1 FAST_ACK 동작 (코드 근거)

**파일**: `apps/worker/video_worker/sqs_main.py`

- `VIDEO_FAST_ACK = os.environ.get("VIDEO_FAST_ACK", "0") == "1"`
- FAST_ACK=1: receive 직후 `delete_message` 호출 → SQS에서 메시지 즉시 삭제
- 처리 결과(ok/fail 등)와 무관하게 이미 delete된 상태

### 2.2 SQS visible / inflight 변화

| 시점 | visible | inflight |
|------|---------|----------|
| 메시지 enqueue 직후 | +1 | 0 |
| Worker receive | -1 | +1 |
| Worker delete (FAST_ACK) | 0 | -1 |
| Lambda 1분 폴링 시점 | 0 | 0 |

- FAST_ACK 사용 시 receive → delete가 짧은 구간에서 일어나므로, Lambda 폴링 시점에는 대부분 `visible=0`, `inflight=0`.

### 2.3 VIDEO_SCALE_VISIBLE_ONLY=1 + FAST_ACK 조합

```130:130:infra/worker_asg/queue_depth_lambda/lambda_function.py
    desired_candidate = backlog_add if VIDEO_SCALE_VISIBLE_ONLY else (inflight + backlog_add)
```

- `VIDEO_SCALE_VISIBLE_ONLY=1` → `desired_candidate = backlog_add = min(visible, 5)`
- `visible=0` → `backlog_add=0` → `desired_candidate=0`
- `new_desired_raw = max(1, min(20, 0)) = max(1, 0) = 1`

즉, **visible=0이면 desired_candidate=0, new_desired_raw=1**이다.

- `visible=0`, `inflight=0`인 경우 scale-in 로직 진입:
  - 최초 0,0: `new_desired = None` (유지)
  - 1200초 후: `new_desired = VIDEO_WORKER_ASG_MIN = 1` → **scale-in**

---

## 3. 다운스케일 발생 조건

코드 기준으로 다음이 모두 만족되면 DesiredCapacity가 1로 감소한다.

1. `visible == 0`
2. `inflight == 0`
3. 위 상태가 `STABLE_ZERO_SECONDS`(1200초) 이상 유지
4. `VIDEO_SCALE_VISIBLE_ONLY == 1` (FAST_ACK 사용 시 권장 설정)

이때 `set_desired_capacity(DesiredCapacity=1)`가 호출된다.

---

## 4. Processing 누락 여부

### 4.1 Lambda 입력 데이터

```215:220:infra/worker_asg/queue_depth_lambda/lambda_function.py
    (video_visible, video_in_flight) = get_queue_counts(sqs, VIDEO_QUEUE)
    ...
    video_scale_result = set_video_worker_desired(autoscaling, ssm, video_visible, video_in_flight)
```

- Lambda는 **SQS `get_queue_attributes`만** 사용
- DB나 `status='PROCESSING'` 개수는 **절대 참조하지 않음**

### 4.2 DB 모델 — PROCESSING count 가능 여부

**파일**: `apps/support/video/models.py`

```32:37:apps/support/video/models.py
class Video(TimestampModel):
    class Status(models.TextChoices):
        ...
        PROCESSING = "PROCESSING", "처리중"
```

```74:80:apps/support/video/models.py
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
```

```119:120:apps/support/video/models.py
    leased_until = models.DateTimeField(null=True, blank=True)
    leased_by = models.CharField(max_length=64, blank=True, default="")
```

- `status='PROCESSING'` 조회 가능 (`status` 필드 존재, `db_index=True`)
- `leased_until` 필드 존재
- 예: `Video.objects.filter(status=Video.Status.PROCESSING).count()`

### 4.3 repositories_video.py — PROCESSING count 전용 함수

**파일**: `academy/adapters/db/django/repositories_video.py`

- `status='PROCESSING'` 개수를 반환하는 전용 함수는 **없음**
- `get_video_status`, `get_video_for_update` 등 단건 조회만 존재
- 하지만 `Video.objects.filter(status=Video.Status.PROCESSING).count()` 형태로 직접 호출 가능

### 4.4 결론: Processing이 Scaling에 반영되는가?

- Lambda는 SQS visible, inflight만 사용
- DB `status='PROCESSING'` 개수는 사용하지 않음
- 따라서 **FAST_ACK=1 + VIDEO_SCALE_VISIBLE_ONLY=1 환경에서 PROCESSING 작업은 scaling backlog에 전혀 반영되지 않는다.**

---

## 5. Root Cause 결론

### 5.1 가설 검증

> FAST_ACK 환경에서는 SQS visible=0이 되므로, Lambda는 backlog=0으로 판단 → Desired=1로 다운스케일 → PROCESSING 작업 중인 Worker 종료

**코드 상 검증 결과: 가능하다.**

| 조건 | 코드 근거 |
|------|-----------|
| FAST_ACK → visible≈0 | receive 직후 delete → 큐에 메시지 없음 |
| inflight≈0 | delete 후 inflight에서도 제거 |
| Lambda는 SQS만 사용 | `get_queue_counts` → `get_queue_attributes` only |
| desired = visible 기반 | `VIDEO_SCALE_VISIBLE_ONLY=1` 시 `desired_candidate = backlog_add` |
| backlog_add = min(visible, 5) | visible=0 → backlog_add=0 |
| scale-in = 1 | `visible=0 and inflight=0` 1200초 유지 시 `DesiredCapacity=1` |

### 5.2 Root Cause

**Video Worker ASG가 처리 중에도 DesiredCapacity=1로 줄어드는 이유:**

1. Lambda는 SQS `ApproximateNumberOfMessages`(visible), `ApproximateNumberOfMessagesNotVisible`(inflight)만 사용
2. FAST_ACK 사용 시 메시지는 receive 직후 delete → visible=0, inflight=0
3. `VIDEO_SCALE_VISIBLE_ONLY=1` 설정으로 `desired_candidate = backlog_add`만 사용 → inflight 배제
4. visible=0 → backlog_add=0 → desired_candidate=0 → new_desired_raw=1
5. visible=0, inflight=0가 1200초 유지되면 scale-in → `DesiredCapacity=1`로 설정
6. 실제 DB에 `status=PROCESSING`인 작업이 있어도 Lambda는 이를 사용하지 않음

**즉, PROCESSING 작업은 scaling 인자에 포함되지 않고, SQS visible=0·inflight=0만 기준으로 scale-in이 발생한다.**

---

## 6. Enterprise 환경 권장 Scaling 기준

현재 방식:

```
desired_candidate = backlog_add (VIDEO_SCALE_VISIBLE_ONLY=1)
                 = min(visible, MAX_BACKLOG_ADD)
```

권장 방식 (DB 기반):

```
desired = QUEUED(DB) + PROCESSING(DB)
```

- `QUEUED`: SQS에 남아 있는 메시지 수 또는 `status=UPLOADED` 개수
- `PROCESSING`: `status=PROCESSING` 개수 (인코딩 중인 작업)

Lambda에서 DB 조회를 추가하려면:

- RDS/VPC 연결 구성
- `Video.objects.filter(status__in=[Video.Status.UPLOADED, Video.Status.PROCESSING]).count()` 또는
- `Video.objects.filter(status=Video.Status.UPLOADED).count() + Video.objects.filter(status=Video.Status.PROCESSING).count()`

또는 DB 대신 SQS만 사용하되, FAST_ACK 사용 시 visibility timeout을 짧게 유지해 inflight를 유지하는 방안도 고려 가능하다.  
다만 `VIDEO_SCALE_VISIBLE_ONLY=1` 설계상 inflight를 제외하고 있으므로, 현재 구조에서는 DB 기반 backlog 산출이 더 적합하다.

---

## 부록: 주요 코드 발췌

### Lambda desired 계산 흐름

```
visible, inflight = get_queue_counts(sqs, VIDEO_QUEUE)  # SQS만
backlog_add = min(visible, MAX_BACKLOG_ADD)
desired_candidate = backlog_add if VIDEO_SCALE_VISIBLE_ONLY else (inflight + backlog_add)
new_desired_raw = max(VIDEO_WORKER_ASG_MIN, min(VIDEO_WORKER_ASG_MAX, desired_candidate))

if visible > 0 or inflight > 0:
    new_desired = new_desired_raw
else:
    # 0,0 지속 1200초 후
    new_desired = VIDEO_WORKER_ASG_MIN  # 1
```

### 배포 시 VIDEO_SCALE_VISIBLE_ONLY 설정

```82:83:scripts/deploy_worker_asg.ps1
    # VIDEO_FAST_ACK=1 사용 시 VIDEO_SCALE_VISIBLE_ONLY=1 필수 (영상 1=1 워커. inflight 제외 → desired=backlog_add만)
    $envJson = '{"Variables":{"VIDEO_SCALE_VISIBLE_ONLY":"1"}}'
```
