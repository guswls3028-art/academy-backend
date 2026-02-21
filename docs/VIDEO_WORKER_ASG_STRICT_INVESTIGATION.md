# Video Worker ASG "1 worker = 1 job" / DesiredCapacity 미증가 — 코드 기준 조사

**조사 방식: grep + 실제 함수 본문만. 추측/가정/설계의도 설명 없음.**

---

## 1️⃣ Lambda Metric 공급 구조

**파일:** `infra/worker_asg/queue_depth_lambda/lambda_function.py`

### 1. Backlog 조회 URL

- **VIDEO_BACKLOG_API_INTERNAL**  
  - L39–43: `os.environ.get("VIDEO_BACKLOG_API_INTERNAL", "http://172.30.3.142:8000/api/v1/internal/video/backlog/").rstrip("/")`  
  - 기본값: `http://172.30.3.142:8000/api/v1/internal/video/backlog` (끝 슬래시 제거)

- **VIDEO_BACKLOG_API_URL**  
  - L44: `os.environ.get("VIDEO_BACKLOG_API_URL", "").rstrip("/")`

- **VIDEO_BACKLOG_FETCH_URL**  
  - L46–50:  
    - `VIDEO_BACKLOG_API_INTERNAL`이 truthy면 그대로 사용.  
    - 아니면 `VIDEO_BACKLOG_API_URL`이 있으면 `f"{VIDEO_BACKLOG_API_URL}/api/v1/internal/video/backlog-count/"`, 없으면 `None`.

[FACT]  
- Backlog 조회는 **VIDEO_BACKLOG_API_INTERNAL** 우선(전체 URL).  
- INTERNAL이 비어 있으면 **VIDEO_BACKLOG_API_URL** + `/api/v1/internal/video/backlog-count/` 사용.  
- 둘 다 없으면 `VIDEO_BACKLOG_FETCH_URL`은 `None`.

### 2. URL이 None일 때 fallback

- L101–103: `if not VIDEO_BACKLOG_FETCH_URL:` → `logger.warning("VIDEO_BACKLOG_FETCH_URL empty; skipping BacklogCount fetch.")` → `return None`.  
- None 반환 시 BacklogCount용 PutMetricData는 호출되지 않음(아래 2️⃣).

[FACT]  
- `VIDEO_BACKLOG_FETCH_URL`이 None이면 backlog 조회를 하지 않고 None 반환. **backlog=0으로 쓰는 fallback 없음.**

### 3. requests.get / timeout

- L111–112: `urllib.request.Request(url, ...)`, `urllib.request.urlopen(req, timeout=5)`.  
- **timeout=5초.**

[FACT]  
- Backlog API 호출 timeout은 **5초**.

### 4. None / Exception 시 PutMetricData 및 0 처리

- L116–131: HTTPError / URLError / Exception 시 모두 `return None`.  
- L206–225: `if video_backlog is not None:` 일 때만 `cw.put_metric_data(..., MetricName="BacklogCount", ...)`.  
- L224–226: `else:` → `logger.warning("BacklogCount metric skipped ...")` 만 수행. PutMetricData 호출 없음.

[FACT]  
- API 실패 또는 예외로 `_fetch_video_backlog_from_api()`가 None을 반환하면 **BacklogCount용 PutMetricData는 호출되지 않음.**  
- **backlog=0으로 대체하여 퍼블리시하는 코드 없음.**

---

## 2️⃣ Metric Push 실제 동작

**파일:** 동일 `lambda_function.py`

- L206–223:  
  - `if video_backlog is not None:` 블록 안에서  
  - `cw.put_metric_data(Namespace="Academy/VideoProcessing", MetricData=[{ "MetricName": "BacklogCount", "Dimensions": [{"Name": "WorkerType", "Value": "Video"}, {"Name": "AutoScalingGroupName", "Value": "academy-video-worker-asg"}], "Value": float(video_backlog), "Timestamp": now, "Unit": "Count" }])`.  
- `video_backlog`는 L179 `video_backlog = _fetch_video_backlog_from_api()` 결과.

[FACT]  
- **Namespace:** `"Academy/VideoProcessing"`.  
- **MetricName:** `"BacklogCount"`.  
- **Dimensions:** `WorkerType=Video`, `AutoScalingGroupName=academy-video-worker-asg`.  
- **Value:** `float(video_backlog)` (API에서 받은 backlog 정수).  
- BacklogCount용 `put_metric_data`는 **try/except로 감싸져 있지 않음.**  
- 따라서 `put_metric_data`에서 Exception이 나면 lambda_handler 전체가 예외로 종료되고, 그 호출에서는 BacklogCount 메트릭은 기록되지 않음.

[IMPACT]  
- API 호출 실패(타임아웃, 4xx/5xx, 연결 불가) 또는 `VIDEO_BACKLOG_FETCH_URL` 비어 있으면 BacklogCount가 한 번도 퍼블리시되지 않음.  
- TargetTracking은 해당 메트릭이 없으면 스케일 아웃을 하지 않음.  
- Lambda가 VPC 밖에 있으면 `172.30.3.142`(사설 IP)에 연결할 수 없어, 기본 INTERNAL URL로는 항상 실패 → BacklogCount 항상 skip.

---

## 3️⃣ ASG TargetTracking ScalingPolicy 연동

**파일:** `scripts/redeploy_worker_asg.ps1`  
- L29–41: `deploy_worker_asg.ps1`만 호출. BacklogCount / CustomizedMetricSpecification / TargetValue 정의는 없음.

**파일:** `scripts/deploy_worker_asg.ps1`  
- L367:  
  `$videoTtJson = '{"TargetValue":3.0,"CustomizedMetricSpecification":{"MetricName":"BacklogCount","Namespace":"Academy/VideoProcessing","Dimensions":[{"Name":"WorkerType","Value":"Video"},{"Name":"AutoScalingGroupName","Value":"academy-video-worker-asg"}],"Statistic":"Average","Unit":"Count"},"ScaleOutCooldown":60,"ScaleInCooldown":300}'`  
- L372: `aws autoscaling put-scaling-policy ... --target-tracking-configuration $videoTtPath`.

[FACT]  
- **MetricName:** `BacklogCount`.  
- **Namespace:** `Academy/VideoProcessing`.  
- **Dimensions:** `WorkerType=Video`, `AutoScalingGroupName=academy-video-worker-asg`.  
- **Statistic:** `Average`.  
- **TargetValue:** `3.0`.  
- 정책 이름: `video-backlogcount-tt` (L372).

[IMPACT]  
- Lambda가 위 Namespace/MetricName/Dimensions로 BacklogCount를 넣지 않으면, 이 TargetTracking 정책은 사용할 메트릭 데이터가 없어 DesiredCapacity를 올리지 않음.

---

## 4️⃣ Django API backlog 계산 엔드포인트

**URL 라우팅**  
- `apps/api/v1/urls.py` L100–107:  
  - `"internal/video/backlog-count/"` → `VideoBacklogCountView`.  
  - `"internal/video/backlog/"` → `VideoBacklogCountView` (동일 뷰).  
- `apps/api/config/urls.py` L37: `path("api/v1/", include("apps.api.v1.urls"))` → 전체 경로 `api/v1/internal/video/backlog/` 또는 `api/v1/internal/video/backlog-count/`.

**뷰 동작**  
- `apps/support/video/views/internal_views.py` L79–93:  
  - `VideoBacklogCountView.get()`: `redis_get_video_backlog_total()` 호출 후 `Response({"backlog": backlog})`.

**Redis**  
- `apps/support/video/redis_status_cache.py`:  
  - L206–207: `_video_backlog_key(tenant_id)` → `f"tenant:{tenant_id}:video:backlog_count"`.  
  - L210: `VIDEO_BACKLOG_KEY_PATTERN = "tenant:*:video:backlog_count"`.  
  - L213–221: `redis_incr_video_backlog(tenant_id)` → 해당 키 INCR (enqueue 시 호출).  
  - L226–239: `redis_decr_video_backlog(tenant_id)` → claim/dead 시 DECR.  
  - L242–260: `redis_get_video_backlog_total()` → `scan_iter(match=VIDEO_BACKLOG_KEY_PATTERN)` 후 값 합산.  
  - L248–249: `get_redis_client()`가 None이면 `return 0`.

[FACT]  
- Backlog 계산 엔드포인트는 존재함: `GET /api/v1/internal/video/backlog/` 또는 `.../backlog-count/`.  
- backlog 값은 Redis 키 `tenant:{id}:video:backlog_count` 합계.  
- INCR은 `sqs_queue.py` enqueue_by_job 성공 시, DECR은 워커 claim/dead 시.  
- Redis 미연결(get_redis_client() None)이면 엔드포인트는 `{"backlog": 0}` 반환.

[IMPACT]  
- API가 0을 반환하면 Lambda는 backlog=0으로 PutMetricData → TargetTracking이 0을 보고 스케일 아웃하지 않음.  
- Lambda가 이 API에 도달하지 못하면(URL/네트워크/인증) BacklogCount 자체가 퍼블리시되지 않음 → 동일 결과.

---

## 5️⃣ Worker 동시성 제한

**SQS receive_message**  
- `libs/queue/client.py` L132–137:  
  `response = self.sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=wait_time_seconds, MessageAttributeNames=["All"])`.  
- **MaxNumberOfMessages=1.**

**Worker 메인 루프**  
- `apps/worker/video_worker/sqs_main.py` L234–251:  
  - `while not _shutdown ...:` 안에서 `message = queue.receive_message(wait_time_seconds=wait_sec)` 한 번 호출.  
  - 메시지 있으면 처리(인코딩 또는 delete_r2) 후 다시 루프.  
  - 동시에 여러 메시지를 받거나 여러 스레드에서 receive하는 코드 없음.

**concurrency / ThreadPoolExecutor**  
- `apps/worker/video_worker/` 내 grep:  
  - `max_concurrency`는 `video/r2_uploader.py`의 HLS 업로드 동시성만 해당.  
  - SQS 폴링/처리 루프를 ThreadPoolExecutor 등으로 병렬화하는 코드 없음.

[FACT]  
- SQS receive는 **한 번에 1개**(MaxNumberOfMessages=1).  
- Worker 프로세스는 **단일 스레드 루프**: receive 1개 → 처리 완료 → 다음 receive.  
- 따라서 **프로세스당 동시에 처리하는 job 수는 1개.**

[IMPACT]  
- 인스턴스 1대 = 동시 1 job. DesiredCapacity가 1이면 여러 개 업로드해도 한 번에 하나만 처리됨.  
- DesiredCapacity를 늘리는 것은 오직 BacklogCount 메트릭이 퍼블리시되고, TargetTracking이 그 값을 보고 스케일 아웃할 때만 발생함.

---

## 출력 요약

### [FACT] (코드 위치 + 동작)

1. **Lambda** (`lambda_function.py`):  
   - Backlog URL은 `VIDEO_BACKLOG_API_INTERNAL`(기본 `http://172.30.3.142:8000/api/v1/internal/video/backlog`) 우선.  
   - URL 없으면 None 반환.  
   - API 실패/예외 시 None 반환.  
   - None일 때 BacklogCount PutMetricData 호출 안 함. backlog=0 fallback 없음.  
   - BacklogCount PutMetricData는 try/except 밖에 있음.

2. **ASG** (`deploy_worker_asg.ps1` L367):  
   - BacklogCount, Namespace `Academy/VideoProcessing`, Dimensions WorkerType=Video, AutoScalingGroupName=academy-video-worker-asg, TargetValue 3.0.

3. **Django** (`internal_views.py`, `redis_status_cache.py`):  
   - `GET api/v1/internal/video/backlog/` 또는 `backlog-count/` 존재.  
   - Redis `tenant:*:video:backlog_count` 합계 반환. Redis 없으면 0.

4. **Worker** (`libs/queue/client.py`, `sqs_main.py`):  
   - receive_message는 MaxNumberOfMessages=1.  
   - 단일 루프로 1개 처리 후 다음 1개 receive. 동시 처리 수 1.

### [IMPACT]

- BacklogCount가 퍼블리시되지 않으면(API 미도달, API 실패, URL 미설정) TargetTracking이 메트릭을 보지 못해 DesiredCapacity를 올리지 않음.  
- DesiredCapacity=1이면 워커 1대 = 1 job만 순차 처리.

### [ROOT CAUSE]

- **Lambda가 BacklogCount를 퍼블리시하지 않는 조건이 하나라도 만족하면** (기본 INTERNAL URL이 사설 IP라 VPC 밖 Lambda 접근 불가, 또는 API 오류/타임아웃, 또는 LAMBDA_INTERNAL_API_KEY 미일치로 403), **ASG는 BacklogCount 메트릭이 없어 스케일 아웃하지 않음.**  
- **Worker 코드는 receive 1개·처리 1개 단일 루프라, 인스턴스당 동시 1 job만 처리.**

### [FIX]

1. **Lambda가 Backlog API에 도달하도록:**  
   - Lambda를 VPC에 두고 API(172.30.3.142)에 접근하거나,  
   - `VIDEO_BACKLOG_API_INTERNAL`을 Lambda가 접근 가능한 URL(예: 공개 API base + path)로 설정.  
   - API 서버에 `LAMBDA_INTERNAL_API_KEY` 설정, Lambda env에 동일 값 설정.  
   - `VIDEO_BACKLOG_API_HOST`로 API가 허용하는 Host 헤더 사용.

2. **BacklogCount가 skip되지 않도록:**  
   - `VIDEO_BACKLOG_FETCH_URL`이 비어 있지 않게 유지.  
   - API `/api/v1/internal/video/backlog/` (또는 backlog-count/)가 200과 `{"backlog": <number>}`를 반환하는지 확인.  
   - API 쪽 Redis 연결 및 `redis_incr_video_backlog`(enqueue 시) 호출 확인.

3. **Worker “1 job at a time”:**  
   - 코드상 인스턴스당 1 job만 처리하는 구조이므로, 동시 처리량을 늘리려면 **DesiredCapacity를 BacklogCount 기반으로 늘리는 것**이 유일한 방법.  
   - 즉, 위 1·2가 선행되어 BacklogCount가 정상 퍼블리시되어야 함.

4. **선택:**  
   - BacklogCount용 `put_metric_data`를 try/except로 감싸 CloudWatch 예외 시에도 로그만 남기고 handler는 성공 반환하도록 할 수 있음. (메트릭 누락 원인 제거는 아님.)
