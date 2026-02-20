# Video Worker 진단 체크리스트

영상 인코딩이 "인코딩 중" / "대기 중"에서 멈출 때, **정확한 진단을 위해** 아래 순서대로 확인하세요. (코드 기준: `apps/worker/video_worker/`, `apps/support/video/services/sqs_queue.py`, `scripts/check_sqs_worker_connectivity.py`)

---

## 1. 워커 프로세스 실행 여부

| 환경 | 확인 방법 |
|------|-----------|
| **Docker** | `docker ps \| findstr academy-video-worker` (Windows) / `docker ps \| grep academy-video-worker` (Linux) |
| **ASG(EC2)** | AWS 콘솔 → EC2 → Auto Scaling Groups → `academy-video-worker-asg` → Desired / InService 인스턴스 수 ≥ 1. 또는 `aws ec2 describe-instances --region ap-northeast-2 --filters "Name=tag:Name,Values=academy-video-worker" "Name=instance-state-name,Values=running" --query "Reservations[].Instances[].[InstanceId,State.Name]" --output table` |

- 컨테이너/인스턴스가 없으면 **메시지를 소비할 프로세스가 없는 것**이므로 인코딩이 진행되지 않음.

---

## 2. SQS 큐 연결 (API ↔ 워커 동일 큐 사용 여부)

- **큐 이름**: `VIDEO_SQS_QUEUE_NAME` (기본값 `academy-video-jobs`)
- **API**가 enqueue할 큐와 **Video Worker**가 receive할 큐 이름이 **완전히 동일**해야 함.

**진단 스크립트 (API와 동일한 환경변수로 실행):**

```bash
# Windows (PowerShell)
cd C:\academy
$env:DJANGO_SETTINGS_MODULE = "apps.api.config.settings.base"
python scripts/check_sqs_worker_connectivity.py
```

```bash
# Linux / API가 Docker인 경우
docker exec -it academy-api python scripts/check_sqs_worker_connectivity.py
```

- **확인 내용**: Video 큐 `get_queue_url` / `receive_message` 성공 여부, 실패 시 큐 미존재 / AWS 자격 증명·권한 오류 메시지.
- API와 워커가 **서로 다른 리전·계정**이면 큐가 달라질 수 있음 → 두 쪽의 `AWS_REGION`, `VIDEO_SQS_QUEUE_NAME`, 자격 증명이 같은지 확인.

---

## 3. 워커 필수 환경변수 (config 로드 실패 시 워커가 시작 직후 종료)

`apps/worker/video_worker/config.py`의 `load_config()`에서 **필수**로 읽는 값들:

| 변수 | 용도 |
|------|------|
| `API_BASE_URL` | API 서버 URL (끝 `/` 제거) |
| `INTERNAL_WORKER_TOKEN` | 내부 인증 |
| `R2_ENDPOINT` | R2 스토리지 |
| `R2_ACCESS_KEY` | R2 |
| `R2_SECRET_KEY` | R2 |
| `R2_BUCKET` 또는 `R2_VIDEO_BUCKET` | 영상 버킷 (기본 `academy-video`) |

- 하나라도 없으면 워커는 **시작 시 `config error: ...` 로 exit(1)**.  
- **확인**: 워커 컨테이너/프로세스 로그 맨 앞부분에 `config error` 또는 `Video Worker (SQS) started \| queue=academy-video-jobs` 가 있는지.

---

## 4. AWS 자격 증명 (SQS / R2 아님)

- Video Worker는 **SQS** receive/delete/changeVisibility 와 **R2** 업로드에 AWS 스타일 자격을 씀.
- **SQS**: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`(또는 `AWS_DEFAULT_REGION`).  
- **R2**: `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ENDPOINT` (R2는 보통 별도 키이지만, SQS는 AWS 키 필요).
- 로컬/비프로덕션에서 SQS만 쓰는 경우 `.env`에 `AWS_REGION=ap-northeast-2` 및 AWS 키가 설정되어 있는지 확인.

---

## 5. 워커 로그로 단계별 확인

워커가 정상 기동되면 다음 로그가 순서대로 나옴 (코드: `apps/worker/video_worker/sqs_main.py`).

| 로그 패턴 | 의미 |
|-----------|------|
| `Video Worker (SQS) started \| queue=academy-video-jobs \| wait_time=20` | 기동 성공, Long Polling 대기 중 |
| `SQS unavailable (AWS credentials invalid or missing?)` | SQS 연결 실패 → 60초 후 재시도 |
| `SQS_MESSAGE_RECEIVED \| request_id=... \| video_id=... \| tenant_id=...` | 메시지 수신, 해당 영상 처리 시작 |
| `[HANDLER] Lock acquired, marking as PROCESSING video_id=...` | DB 상태 PROCESSING으로 변경 |
| `[HANDLER] process_fn completed video_id=... hls_path=...` | 인코딩 파이프라인 완료 |
| `SQS_JOB_COMPLETED \| request_id=... \| video_id=...` | 성공 후 메시지 삭제 |
| `SQS_JOB_FAILED \| request_id=... \| video_id=...` | 실패 (이후 DLQ 등 재시도 정책에 따름) |
| `Video processing failed: video_id=...` | `process_video` 또는 DB 완료 처리 중 예외 |

- **메시지를 전혀 받지 못함**: 큐 이름 불일치, 리전/계정 불일치, 또는 큐가 비어 있음(API에서 enqueue 실패 여부는 API 로그로 확인).
- **메시지는 받지만 곧바로 실패**: `config error` / R2/ffmpeg 관련 예외 → 위 환경변수 및 R2/ffmpeg 설치·경로 확인.

---

## 6. API에서 enqueue 성공 여부

- 업로드 완료 시점에 API가 SQS로 메시지를 보냄 (`apps/support/video/views/video_views.py` → `VideoSQSQueue().enqueue(video)`).
- **확인**: API 로그에서 `Video job enqueued: video_id=<id>` 가 찍히는지.  
- 503 또는 enqueue 실패 로그가 있으면 **API 쪽 SQS 설정·자격 증명**을 2번과 동일하게 점검.

---

## 7. Redis (진행률/상태 표시용, 인코딩 완료 여부와는 별개)

- Worker는 **진행률**을 Redis에 쓰고, **완료/실패 상태**는 DB + Redis에 씀 (`academy/adapters/db/django/repositories_video.py`).
- Redis가 없거나 연결 실패해도 **인코딩 자체는 진행**될 수 있으나, 진행률 API(`GET /media/videos/{id}/progress/`)가 UNKNOWN 또는 오래된 상태를 반환할 수 있음.
- **확인**: 워커와 API가 같은 `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB` 를 보는지. (docker-compose 기준 `REDIS_HOST=redis`, 포트 6379.)

---

## 8. 요약: “인코딩이 안 된다”일 때 볼 것

1. **워커 프로세스**가 떠 있는지 (Docker/ASG).
2. **SQS**  
   - `scripts/check_sqs_worker_connectivity.py` 로 Video 큐 접근 가능 여부.  
   - API/워커의 `VIDEO_SQS_QUEUE_NAME`, `AWS_REGION`, 자격 증명 일치.
3. **워커 설정**  
   - `API_BASE_URL`, `INTERNAL_WORKER_TOKEN`, R2 관련 필수 env로 config 로드 성공 여부 (시작 로그).
4. **워커 로그**  
   - `SQS_MESSAGE_RECEIVED` → `SQS_JOB_COMPLETED` / `SQS_JOB_FAILED` / `Video processing failed` 까지 흐름으로, 어디에서 멈추거나 에러 나는지.
5. **API 로그**  
   - 업로드 완료 시 `Video job enqueued` 여부.

위 순서대로 확인하면, “워커가 안 떠 있는지 / 큐가 안 맞는지 / 설정이 빠진지 / 처리 중에 터지는지”를 정확히 구분할 수 있습니다.

---

## 부록: 자주 나오는 로그별 원인

---

## 9. 워커 3대인데 일부(111, 222)만 처리 안 되고 333만 되는 경우

**증상**: 동시에 111, 222, 333 업로드했는데 333만 인코딩되고 111·222는 대기.

**가능한 원인** (Handler가 skip 반환 시 메시지는 삭제됨 → 영상은 큐에 안 남음):

| 원인 | Handler 로그 | 대응 |
|------|--------------|------|
| Redis idempotency 락 잔류 | `[HANDLER] Lock acquisition failed, skipping` | 이전 워커 크래시 후 lock 미해제. TTL(4h) 대기 또는 `redis-cli DEL job:encode:111:lock` 후 retry |
| DB status ≠ UPLOADED | `[HANDLER] Cannot mark video N as PROCESSING, skipping` | 해당 영상이 이미 PROCESSING/READY/FAILED. retry API로 UPLOADED 후 재 enqueue |
| 취소 요청 | `[HANDLER] Cancel requested for video_id=N, skipping` | API retry 시 cancel 플래그 설정됨. TTL 만료 후 재시도 |

**진단 스크립트**:

```bash
python scripts/check_video_stuck_diagnosis.py 111 222 333
# Docker: docker exec -it academy-api python scripts/check_video_stuck_diagnosis.py 111 222 333
```

- DB status: UPLOADED / PROCESSING / READY / FAILED
- Redis `job:encode:{id}:lock`: 있으면 TTL 내에 다른 워커가 처리 불가 → skip → 메시지 삭제 → 영상 대기 상태 유지

---

## 부록: 자주 나오는 로그별 원인

### `[HANDLER] Cannot mark video N as PROCESSING, skipping`
- **의미**: `mark_processing(video_id)` 가 False를 반환함.
- **원인**: DB에서 해당 영상 상태가 **UPLOADED가 아님**. 이미 PROCESSING(이전 시도에서 변경됨), READY, FAILED 등.
- **대응**: 재시도 메시지로 같은 영상이 다시 들어온 경우 정상 동작(스킵). 처음 업로드인데 계속 스킵되면 API에서 enqueue 시점의 `video.status`가 UPLOADED인지 확인.

### `Video processing failed: video_id=N, error=Failed to complete video: not_found`
- **의미**: 인코딩은 끝났지만 `complete_video` 호출 시 **해당 Video 행을 찾지 못함** (`get_video_for_update` → None).
- **가능한 원인**  
  1. **인코딩 중에 Video(또는 부모 Session/Lecture)가 삭제됨**  
     - `Video`는 `Session`에 FK, `on_delete=CASCADE`. Session/강의 삭제 시 Video도 삭제됨.  
  2. **API와 워커가 서로 다른 DB를 바라봄**  
     - 워커의 `DB_HOST` / `DB_NAME` 이 API와 동일한지 확인.
- **대응**  
  - 인코딩이 끝날 때까지 해당 강의/세션/영상 삭제하지 않기.  
  - API·워커 환경변수에서 `DB_HOST`, `DB_NAME`, `DB_USER` 등이 같은지 점검.  
  - 다음 배포부터는 `complete_video` 실패 시 로그에 `row exists=True/False`가 찍히므로, True면 연결/락 이슈, False면 삭제 또는 다른 DB로 구분 가능.

---

## 부록 B: 성공 1회 달성용 체크리스트 v2

**목표**: 오늘 당장 1개라도 끝까지 성공시키기.

### 0. 긴 영상(video 17) 때문에 짧은 영상 대기 → 워커 재시작으로 우회

- video 17 메시지는 **visibility 6시간**으로 잡혀 있어서 다른 소비자가 못 봄.
- **1111 메시지는 visible** → 워커를 재시작하면 새 워커가 1111을 먼저 가져감.

```bash
# 워커 서버에서
sudo docker restart academy-video-worker
```

- 재시작 후 워커 로그에서 `SQS_MESSAGE_RECEIVED | video_id=...` (1111의 id) 확인.
- video 17은 visibility 만료 후 다시 visible 되고, 나중에 다시 처리될 수 있음.

### 1. 오토스케일 구조: Lambda 기반 (ASG 정책 아님)

이 프로젝트는 **Lambda `academy-worker-queue-depth-metric`** 가 1분마다 SQS 큐 깊이를 보고 **직접** ASG desired를 조정함.  
따라서 `describe-policies` / `describe-alarms` 가 **비어 있는 것은 정상**임 (ASG 정책·알람 미사용).

- **Lambda**: `academy-worker-queue-depth-metric` (`infra/worker_asg/queue_depth_lambda/`)
- **트리거**: EventBridge `academy-worker-queue-depth-rate` (rate 1 minute)
- **로직**: SQS `academy-video-jobs` visible+in_flight 조회 → `set_desired_capacity` 호출

### 2. 오토스케일 수동 점검

```powershell
# MaxSize 확인 (1이면 절대 안 늘어남)
aws autoscaling describe-auto-scaling-groups --region ap-northeast-2 --auto-scaling-group-names academy-video-worker-asg --query "AutoScalingGroups[0].{Min:MinSize,Desired:DesiredCapacity,Max:MaxSize,Instances:length(Instances)}" --output table

# 수동 desired=2 테스트 (Max >= 2 일 때만)
aws autoscaling set-desired-capacity --region ap-northeast-2 --auto-scaling-group-name academy-video-worker-asg --desired-capacity 2

# Lambda 존재 여부 (이게 있어야 오토스케일 동작)
aws lambda get-function --function-name academy-worker-queue-depth-metric --region ap-northeast-2

# EventBridge 규칙(1분 주기) 확인
aws events list-rules --region ap-northeast-2 --query "Rules[?Name=='academy-worker-queue-depth-rate']"

# Lambda 최근 로그 (큐 깊이, desired 변경 여부)
# AWS 콘솔 → CloudWatch → Log groups → /aws/lambda/academy-worker-queue-depth-metric
```

### 3. SQS visibility 확인 (코드상 이미 6h 설정됨)

- 워커가 **작업 시작 시** `change_message_visibility(receipt_handle, 21600)` 호출 (6h).
- 3시간 영상도 visibility로는 문제 없음.

### 4. (참고) 별도 Lambda `infra/worker_autoscale_lambda/` 사용 시

- EventBridge 1분 주기로 Lambda 호출.
- `ApproximateNumberOfMessages`(visible) >= 1 이면 stopped 인스턴스 1대 Start.
- `MAX_INSTANCES_PER_TYPE=1` 기본값 → 2대로 늘리려면 이 값 또는 Lambda 로직 수정 필요.
- Lambda 로그에서 큐 depth, Start 호출 여부 확인.

### 5. Lambda가 없거나 동작 안 할 때 — 수동 배포

Lambda `academy-worker-queue-depth-metric` 이 없거나 EventBridge가 호출 안 하면 스케일이 안 됨.

```powershell
# deploy_worker_asg.ps1 실행 (Lambda + EventBridge 배포 포함)
.\scripts\deploy_worker_asg.ps1
```

또는 ASG 정책 방식으로 전환하려면 아래 절차 (Lambda 비사용 시 대안).

#### 5-A. ASG 정책 방식 (Lambda 미사용 시 대안)

**4-1) Scale-Out 정책 생성 (먼저 PolicyARN 확보)**

```bash
POLICY_ARN=$(aws autoscaling put-scaling-policy \
  --region ap-northeast-2 \
  --auto-scaling-group-name academy-video-worker-asg \
  --policy-name video-queue-scaleout \
  --policy-type StepScaling \
  --adjustment-type ChangeInCapacity \
  --step-adjustments "[{\"MetricIntervalLowerBound\":0,\"ScalingAdjustment\":1}]" \
  --cooldown 300 \
  --query PolicyARN --output text)
echo $POLICY_ARN
```

**4-2) CloudWatch Alarm 생성 (큐 메시지 ≥ 1 → 정책 실행)**

```bash
aws cloudwatch put-metric-alarm \
  --region ap-northeast-2 \
  --alarm-name academy-video-queue-depth-scaleout \
  --alarm-description "Video queue has messages - scale out" \
  --namespace AWS/SQS \
  --metric-name ApproximateNumberOfMessagesVisible \
  --dimensions Name=QueueName,Value=academy-video-jobs \
  --statistic Sum \
  --period 60 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 1 \
  --alarm-actions "$POLICY_ARN"
```

**4-3) 확인**

```bash
aws autoscaling describe-policies --region ap-northeast-2 --auto-scaling-group-name academy-video-worker-asg
aws cloudwatch describe-alarms --region ap-northeast-2 --alarm-names academy-video-queue-depth-scaleout
```

큐에 메시지가 쌓이면 Alarm → 정책 → DesiredCapacity 증가. (대안: CPU 60% 기준 Target Tracking으로 간단히 설정 가능)
