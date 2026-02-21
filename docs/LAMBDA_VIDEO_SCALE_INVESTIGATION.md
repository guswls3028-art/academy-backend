# Lambda Video Scale — STRICT INVESTIGATION 보고서

## 1) 코드 근거 수집

### [FILE: infra/worker_asg/queue_depth_lambda/lambda_function.py]

#### SQS get_queue_attributes로 가져오는 attribute 목록

**L53-L65 (get_queue_counts):**
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

**사실:** `ApproximateNumberOfMessagesDelayed`는 가져오지 않음. visible, inflight만 사용.

---

#### backlog_add 계산식

**L124:**
```python
backlog_add = min(visible, MAX_BACKLOG_ADD)
```

**사실:** `backlog_add = min(visible, 5)` (MAX_BACKLOG_ADD=5)

---

#### desired_candidate 및 new_desired_raw/new_desired 계산식

**L124-L126:**
```python
backlog_add = min(visible, MAX_BACKLOG_ADD)
desired_candidate = inflight + backlog_add
new_desired_raw = max(VIDEO_WORKER_ASG_MIN, min(VIDEO_WORKER_ASG_MAX, desired_candidate))
```

**사실:** `desired_candidate = inflight + backlog_add`, `new_desired_raw = clamp(1, 20, desired_candidate)`

---

#### scale-in 방지 조건: visible > 0 or inflight > 0

**L128-L133:**
```python
if visible > 0 or inflight > 0:
    _delete_stable_zero_param(ssm_client)
    new_desired = new_desired_raw
    decision = "scale_out" if new_desired > 0 else "hold"
else:
    stable_since = _get_stable_zero_since(ssm_client)
    ...
```

**사실:** visible > 0 또는 inflight > 0이면 scale-in 절대 안 함. new_desired = new_desired_raw 유지.

---

#### stable_zero_param SSM 키

**L40:**
```python
SSM_STABLE_ZERO_PARAM = os.environ.get("SSM_STABLE_ZERO_PARAM", "/academy/workers/video/zero_since_epoch")
```

**L98-L105 (_delete_stable_zero_param):**
```python
def _delete_stable_zero_param(ssm_client) -> None:
    try:
        ssm_client.delete_parameter(Name=SSM_STABLE_ZERO_PARAM)
```

**L136-L145 (visible==0 and inflight==0일 때):**
- stable_since == 0 → put_parameter로 현재 epoch 저장, new_desired=None
- stable_since 존재하고 (now - stable_since) >= 1200 → new_desired=MIN, delete_param, decision=scale_in
- 그 외 → new_desired=None, decision=hold

**사실:** `/academy/workers/video/zero_since_epoch` 키로 0,0 유지 시작 시각 저장. 20분 지속 시에만 scale-in.

---

#### set_video_worker_desired 호출

**L202:**
```python
set_video_worker_desired(autoscaling, ssm, video_visible, video_in_flight)
```

**사실:** 항상 호출. video_visible, video_in_flight 둘 다 전달.

---

## 2) invoke 반환값 — video_queue_depth=0 원인 확정

**L235-239:**
```python
return {
    "ai_queue_depth": ai_total,
    "video_queue_depth": video_visible,
    "messaging_queue_depth": messaging_visible,
}
```

**확정:** `video_queue_depth`는 `video_visible`만 리턴함. inflight 미포함.

- visible=0, inflight=5 → return `video_queue_depth: 0`
- 스케일 계산에는 inflight 사용하므로 desired=5로 set_desired 호출됨

---

## 3) Patch 제안 (스케일 수식 변경 금지)

### Patch 옵션 A (추천): 디버깅용 필드 추가

`set_video_worker_desired`가 decision/new_desired 등을 반환하도록 변경하거나,  
handler에서 video 관련 값들을 모두 return에 포함.

- `set_video_worker_desired`를 수정해 `(new_desired, decision, backlog_add, desired_candidate)` 반환
- return에 `video_visible`, `video_inflight`, `video_delayed`, `video_desired`, `video_backlog_add`, `video_decision` 추가

### Patch 옵션 B: video_queue_depth만 visible+inflight로 변경

- `"video_queue_depth": video_visible + video_in_flight`
- 기존 `video_queue_depth`가 "실제 부하"를 나타내도록 변경

---

## 4) AWS 검증 명령어 (복붙용)

```powershell
# SQS
aws sqs get-queue-attributes --queue-url https://sqs.ap-northeast-2.amazonaws.com/809466760795/academy-video-jobs --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible ApproximateNumberOfMessagesDelayed --region ap-northeast-2

# ASG desired/instances
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names academy-video-worker-asg --region ap-northeast-2 --query "AutoScalingGroups[0].{Desired:DesiredCapacity,Min:MinSize,Max:MaxSize,Instances:Instances[*].InstanceId}"

# ASG scaling activities
aws autoscaling describe-scaling-activities --auto-scaling-group-name academy-video-worker-asg --region ap-northeast-2 --max-items 5 --query "Activities[].{Time:StartTime,Desc:Description,Cause:Cause}"

# Lambda
aws lambda get-function --function-name academy-worker-queue-depth-metric --region ap-northeast-2 --query "Configuration.{LastModified:LastModified,CodeSha256:CodeSha256}"

# Lambda invoke
aws lambda invoke --function-name academy-worker-queue-depth-metric --region ap-northeast-2 out.json; Get-Content out.json

# CloudWatch logs (최신 5분)
aws logs filter-log-events --log-group-name /aws/lambda/academy-worker-queue-depth-metric --start-time $(([int][double]::Parse((Get-Date -UFormat %s)) - 300) * 1000) --region ap-northeast-2 --filter-pattern "video_asg" --query "events[*].message"
```

---

## 5) 최종 정리

### 스케일이 5까지 뜨는 이유 (코드 근거)

- `desired_candidate = inflight + backlog_add`
- visible=0, inflight=5 → backlog_add=0, desired_candidate=5
- new_desired_raw = clamp(1, 20, 5) = 5
- visible > 0 or inflight > 0 → new_desired=5, scale-in 안 함
- `set_desired_capacity(DesiredCapacity=5)` 호출

### 영상 3개인데 inflight 5가 되는 이유 (코드/큐 관점)

- SQS는 메시지 단위. 영상 1개 ≠ 메시지 1개가 항상 아님.
- lock_fail → NACK(visibility 60) → 동일 video_id 메시지가 여러 워커에 동시 in-flight.
- retry API 호출 → 추가 메시지 enqueue.
- enqueue_delete_r2 → delete_r2 메시지 추가.
- SQS at-least-once로 인해 동일 메시지가 visibility timeout 전 여러 번 deliver될 수 있음.
