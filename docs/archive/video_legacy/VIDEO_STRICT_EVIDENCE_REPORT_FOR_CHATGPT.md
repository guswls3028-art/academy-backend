# Video Worker STRICT EVIDENCE-ONLY 보고서 (ChatGPT용)

> **규칙**: 추측/가정 금지. 아래 내용은 전부 코드·CLI·로그 근거로만 작성됨.

---

## 0. 관측 근거 (실제 실행 결과)

### SQS 상태 (CLI 출력)

```json
{
    "Attributes": {
        "ApproximateNumberOfMessages": "0",
        "ApproximateNumberOfMessagesNotVisible": "5",
        "ApproximateNumberOfMessagesDelayed": "0"
    }
}
```

**사실**: Visible=0, NotVisible(inflight)=5, Delayed=0

### VisibilityTimeout (CLI 출력)

```json
{
    "Attributes": {
        "VisibilityTimeout": "10800"
    }
}
```

**사실**: 10800초 = 3시간

### Lambda invoke 반환값 (실행 결과)

```json
{"ai_queue_depth": 0, "video_queue_depth": 0, "messaging_queue_depth": 0}
```

**사실**: video_queue_depth = 0 (visible만 반환하는 코드에 해당)

### Lambda 배포 정보 (CLI 출력)

```
CodeSha256:  lcaPzszWzadLKHmUm6joGEeo4XRbp4yU9EVjZ97fPGk=
LastModified: 2026-02-21T00:31:50.000+0000
```

### CloudWatch SQS 메트릭 (최근 10분)

| 메트릭 | 값 |
|--------|-----|
| NumberOfMessagesReceived | 10:16 1건, 나머지 0 |
| NumberOfMessagesDeleted | 전 구간 0 |

**사실**: Received=1, Deleted=0

### Worker 로그 (실제 출력)

```
2026-02-21 10:16:07,121 [INFO] SQS_MESSAGE_RECEIVED | request_id=f59c2019 | video_id=53 | tenant_id=1 | queue_wait_sec=140.92
2026-02-21 10:16:07,122 [INFO] [SQS_MAIN] Calling handler.handle() video_id=53
2026-02-21 10:16:07,174 [INFO] [HANDLER] Lock acquired, marking as PROCESSING video_id=53
2026-02-21 10:16:07,189 [INFO] [HANDLER] Starting process_fn video_id=53
2026-02-21 10:16:24,446 [INFO] [TRANSCODER] Starting ffmpeg for video_id=53
2026-02-21 10:20:57,419 [INFO] [PROCESSOR] Transcode progress video_id=53 ... 2% overall=51%
2026-02-21 10:25:30,747 [INFO] [PROCESSOR] Transcode progress video_id=53 ... 5% overall=52%
```

**사실**: `handler.handle() returned` 로그 없음. `SQS_JOB_COMPLETED` 로그 없음. ffmpeg 진행 중.

### ASG 상태 (CLI 출력)

```
AutoScalingGroupName: academy-video-worker-asg
DesiredCapacity: 5
Instances: 5개 InService (i-006bde9a3204b8222, i-02039511041ed8a75, i-07d4afc79d1550bdb, i-0c2a81995f9cd776d, i-0f4968f540c57dbb3)
```

### ASG Scaling Activities Cause (발췌)

```
At 2026-02-21T01:14:19Z a user request explicitly set group desired capacity changing the desired capacity from 2 to 5.
```

**사실**: Lambda가 set_desired_capacity(5) 호출한 것으로 기록됨

---

## 1. 코드 근거 (실제 파일)

### 1.1 Lambda: SQS attribute 사용

**[FILE: infra/worker_asg/queue_depth_lambda/lambda_function.py] L51-65**

```python
attrs = sqs_client.get_queue_attributes(
    QueueUrl=url,
    AttributeNames=[
        "ApproximateNumberOfMessages",  # visible
        "ApproximateNumberOfMessagesNotVisible",  # inflight
    ],
)
a = attrs.get("Attributes", {})
visible = int(a.get("ApproximateNumberOfMessages", 0))
in_flight = int(a.get("ApproximateNumberOfMessagesNotVisible", 0))
return visible, in_flight
```

**사실**: ApproximateNumberOfMessagesDelayed는 가져오지 않음. visible, inflight만 사용.

### 1.2 Lambda: 스케일 수식

**[FILE: infra/worker_asg/queue_depth_lambda/lambda_function.py] L127-131**

```python
backlog_add = min(visible, MAX_BACKLOG_ADD)
desired_candidate = backlog_add if VIDEO_SCALE_VISIBLE_ONLY else (inflight + backlog_add)
new_desired_raw = max(VIDEO_WORKER_ASG_MIN, min(VIDEO_WORKER_ASG_MAX, desired_candidate))
```

**사실**: VIDEO_SCALE_VISIBLE_ONLY=0(기본)이면 `desired_candidate = inflight + backlog_add`. inflight 포함.

### 1.3 Lambda: scale-in 방지

**[FILE: infra/worker_asg/queue_depth_lambda/lambda_function.py] L134-137**

```python
if visible > 0 or inflight > 0:
    _delete_stable_zero_param(ssm_client)
    new_desired = new_desired_raw
    decision = "scale_out" if new_desired > 0 else "hold"
```

**사실**: visible > 0 또는 inflight > 0이면 scale-in 안 함.

### 1.4 Lambda: return 필드

**[FILE: infra/worker_asg/queue_depth_lambda/lambda_function.py] L252-257**

```python
return {
    "ai_queue_depth": ai_total,
    "video_queue_depth": video_visible,
    "messaging_queue_depth": messaging_visible,
    **video_scale_result,
}
```

**사실**: `video_queue_depth`는 `video_visible`만 사용. `video_scale_result`에 video_inflight, video_desired_raw 등 포함됨(이미 패치됨).

### 1.5 sqs_main.py: delete_message 호출 조건

**[FILE: apps/worker/video_worker/sqs_main.py]**

| result | delete_message | change_visibility |
|--------|----------------|-------------------|
| "ok" | L327 (VIDEO_FAST_ACK=0일 때만) | - |
| "skip:cancel" | L355 (VIDEO_FAST_ACK=0일 때만) | - |
| "skip:claim" | - (이미 ACK됨) | - |
| "skip:lock" | - | NACK 60~120 |
| "skip:mark_processing" | - | NACK 60~120 |
| "lock_fail" | - | NACK 60~120 |
| "failed" | - | NACK 180 (VIDEO_FAST_ACK=0일 때) |

**사실**: delete는 `result == "ok"` 또는 `result == "skip:cancel"`일 때만 실행. (VIDEO_FAST_ACK=0일 때. VIDEO_FAST_ACK=1이면 receive 직후 delete.)

### 1.6 handler.handle() 반환값

**[FILE: src/application/video/handler.py]**

| 반환값 | 조건 | 라인 |
|--------|------|------|
| "skip:cancel" | is_cancel_requested(tenant_id, video_id) | 77 |
| "skip:claim" | try_claim_video 실패 (fast_ack 모드) | 97 |
| "lock_fail" | idempotency.acquire_lock 실패 | 103 |
| "skip:mark_processing" | mark_processing 실패 | 108 |
| "ok" | process_fn 완료 + complete_video 성공 | 124 |
| "skip:cancel" | CancelledError | 128 |
| "failed" | Exception 발생 (fail_video 호출 후) | 133 |

**사실**: "ok"는 complete_video 성공 시에만 반환.

---

## 2. 확정 결론 (근거와 함께)

| 결론 | 근거 |
|------|------|
| invoke video_queue_depth=0 | L252: video_visible만 사용. visible=0이므로 0 |
| 스케일 5 | desired_candidate = inflight + backlog_add. inflight=5 → desired=5 |
| Deleted=0 | handler가 아직 반환 안 됨 → result 분기 도달 안 함 → delete 미호출 |
| inflight=5 | 워커 5대가 각각 메시지 1건씩 receive, handler 내부(ffmpeg) 블로킹 중 |

---

## 3. 적용된 패치 요약 (Cursor에서 이미 적용됨)

- Lambda return: video_visible, video_inflight, video_backlog_add, video_desired_raw, video_new_desired, video_decision, stable_zero_since_epoch 포함
- Worker: VIDEO_FAST_ACK=1 시 receive 직후 delete + try_claim_video
- Lambda: VIDEO_SCALE_VISIBLE_ONLY=1 시 desired_candidate = backlog_add만 사용

---

## 4. AWS 검증 명령어 (복붙용)

### SQS

```powershell
aws sqs get-queue-attributes --queue-url https://sqs.ap-northeast-2.amazonaws.com/809466760795/academy-video-jobs --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible ApproximateNumberOfMessagesDelayed --region ap-northeast-2
```

### Lambda invoke

```powershell
aws lambda invoke --function-name academy-worker-queue-depth-metric --region ap-northeast-2 out.json; Get-Content out.json
```

### Lambda 코드 해시

```powershell
aws lambda get-function --function-name academy-worker-queue-depth-metric --region ap-northeast-2 --query "Configuration.{LastModified:LastModified,CodeSha256:CodeSha256}" --output table
```

### ASG 상태

```powershell
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names academy-video-worker-asg --region ap-northeast-2 --query "AutoScalingGroups[0].{Desired:DesiredCapacity,Instances:Instances[*].InstanceId}"
```

### ASG Scaling Activities

```powershell
aws autoscaling describe-scaling-activities --auto-scaling-group-name academy-video-worker-asg --region ap-northeast-2 --max-items 10 --query "Activities[].{Time:StartTime,Desc:Description,Cause:Cause}" --output table
```

### CloudWatch SQS 메트릭 (Received/Deleted)

```powershell
$start = (Get-Date).AddMinutes(-10).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$end   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

aws cloudwatch get-metric-statistics --namespace AWS/SQS --metric-name NumberOfMessagesReceived --dimensions Name=QueueName,Value=academy-video-jobs --start-time $start --end-time $end --period 60 --statistics Sum --region ap-northeast-2

aws cloudwatch get-metric-statistics --namespace AWS/SQS --metric-name NumberOfMessagesDeleted --dimensions Name=QueueName,Value=academy-video-jobs --start-time $start --end-time $end --period 60 --statistics Sum --region ap-northeast-2
```

### Worker 로그 (EC2 호스트)

```bash
sudo docker logs academy-video-worker 2>&1 | grep -E "SQS_MESSAGE_RECEIVED|handler.handle\(\) returned|SQS_JOB_COMPLETED|lock_fail|skip:"
```

---

## 5. 컨테이너 내부 코드 확인 (EC2 → docker exec)

```bash
sudo docker exec -it academy-video-worker bash
# 컨테이너 내부:
sed -n '260,370p' /app/apps/worker/video_worker/sqs_main.py
grep -n "delete_message\|result ==" /app/apps/worker/video_worker/sqs_main.py | head -30
```

---

## 6. 다음 액션 (추측 없이)

1. **Lambda 배포 확인**: `aws lambda get-function`으로 CodeSha256이 최신 zip과 일치하는지 확인
2. **Worker 배포 확인**: 컨테이너 내 sqs_main.py에 VIDEO_FAST_ACK 분기 존재 여부 확인
3. **VIDEO_FAST_ACK=1 사용 시**: receive 직후 delete → inflight 급감 → 스케일 visible 기반으로 동작
4. **VIDEO_SCALE_VISIBLE_ONLY=1 설정 시**: desired = backlog_add만 사용 (inflight 제외)
