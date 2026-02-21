# Video Upload Complete → Enqueue 파이프라인: 필수 ENV 및 SSM 분리

**STRICT INVESTIGATION — 코드 grep/실제 참조만 사용.**

---

## 1. REQUIRED ENV FOR UPLOAD COMPLETE (API)

### 1.1 코드 출처

| 경로 | 용도 | 사용 설정/환경변수 |
|------|------|-------------------|
| `apps/support/video/views/video_views.py` | `_upload_complete_impl` | `head_object(file_key)`, `create_presigned_get_url(key=...)`, `VideoSQSQueue().create_job_and_enqueue(video)` |
| `libs/s3_client/client.py` | R2 head_object | `settings.R2_ENDPOINT`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `settings.R2_VIDEO_BUCKET` |
| `libs/s3_client/presign.py` | presigned GET URL | `settings.R2_ENDPOINT`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `settings.R2_VIDEO_BUCKET` |
| `apps/support/video/services/sqs_queue.py` | create_job_and_enqueue → enqueue_by_job | `settings.VIDEO_SQS_QUEUE_NAME`, `queue_client.send_message()`, `redis_incr_video_backlog(tenant_id)` |
| `libs/queue/client.py` | SQS send_message | `os.getenv("AWS_REGION", "ap-northeast-2")`, boto3 기본 자격증명 (IAM 또는 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) |
| `libs/redis/client.py` | get_redis_client (backlog INCR) | `os.getenv("REDIS_HOST")` 필수, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB` (선택, 기본값 있음) |
| `apps/api/config/settings/base.py` | R2/SQS 이름 | `os.getenv("R2_ACCESS_KEY")`, `R2_SECRET_KEY`, `R2_ENDPOINT`, `R2_VIDEO_BUCKET`, `VIDEO_SQS_QUEUE_NAME` |

**참고:** 코드에는 `CLOUDFLARE_R2_*`, `VIDEO_BUCKET_NAME`, `REDIS_URL` 이름이 없음. 모두 `R2_*`, `R2_VIDEO_BUCKET`, `REDIS_HOST` 등으로 참조됨.

### 1.2 REQUIRED_API_ENV (업로드 완료 → SQS enqueue + Redis backlog)

```
# R2 (head_object, presigned GET)
R2_ENDPOINT
R2_ACCESS_KEY
R2_SECRET_KEY
R2_VIDEO_BUCKET

# SQS enqueue (send_message)
AWS_REGION
VIDEO_SQS_QUEUE_NAME

# Redis (backlog INCR on enqueue)
REDIS_HOST
REDIS_PORT
REDIS_PASSWORD
REDIS_DB

# AWS 자격증명 (EC2 IAM Role이면 생략 가능; 로컬/다른 호스트는 필요)
# AWS_ACCESS_KEY_ID
# AWS_SECRET_ACCESS_KEY
```

**API 앱 기동에 공통으로 필요한 것 (upload_complete 외):**  
`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `SECRET_KEY` 등은 base.py 기준으로 필요. SSM `/academy/api/env`에는 최소한 위 목록 + DB/공통을 넣어야 503/미등록이 해소됨.

---

## 2. REQUIRED_WORKER_ENV (Video Worker SQS Poller + HLS Upload)

### 2.1 코드 출처

| 경로 | 용도 | 환경변수 |
|------|------|----------|
| `apps/worker/video_worker/config.py` | load_config() | `API_BASE_URL`, `INTERNAL_WORKER_TOKEN`, `WORKER_ID`, `R2_BUCKET` or `R2_VIDEO_BUCKET`, `R2_PREFIX`, `R2_ENDPOINT`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_REGION` |
| `apps/api/config/settings/worker.py` | Django settings (worker) | `VIDEO_SQS_QUEUE_NAME`, `AWS_REGION`, `DB_*`, `R2_*`, 기타 |
| `libs/queue/client.py` | SQS receive/delete/visibility | `AWS_REGION`, boto3 자격증명 |
| `libs/redis/client.py` | heartbeat, idempotency, progress, backlog decr | `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB` |

### 2.2 REQUIRED_WORKER_ENV

```
# API 연동
API_BASE_URL
INTERNAL_WORKER_TOKEN
WORKER_ID

# R2 (다운로드 원본, HLS 업로드)
R2_BUCKET or R2_VIDEO_BUCKET
R2_PREFIX
R2_ENDPOINT
R2_ACCESS_KEY
R2_SECRET_KEY
R2_REGION

# SQS poll
AWS_REGION
VIDEO_SQS_QUEUE_NAME

# Redis
REDIS_HOST
REDIS_PORT
REDIS_PASSWORD
REDIS_DB

# DB (Django ORM)
DB_NAME
DB_USER
DB_PASSWORD
DB_HOST
DB_PORT
SECRET_KEY
```

---

## 3. DIFF (API vs Worker)

| 구분 | API 전용 (워커에 불필요) | 공통 | Worker 전용 (API에 불필요) |
|------|-------------------------|------|----------------------------|
| R2 | - | R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY, R2_VIDEO_BUCKET | R2_PREFIX, R2_REGION (워커 config), R2_BUCKET(워커는 R2_VIDEO_BUCKET과 동일값 가능) |
| SQS | send_message만 (enqueue) | VIDEO_SQS_QUEUE_NAME, AWS_REGION | receive_message, delete_message, change_message_visibility (동일 큐) |
| Redis | incr backlog (enqueue 시) | REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB | decr backlog, heartbeat, idempotency, progress |
| 기타 | - | - | API_BASE_URL, INTERNAL_WORKER_TOKEN, WORKER_ID, CDN_HLS_BASE_URL 등 |

**정리:** API는 R2 읽기 + SQS enqueue + Redis INCR만 필요. Worker는 R2 읽기/쓰기 + SQS poll + Redis 읽기/쓰기 + API 호출용 토큰 필요. API SSM에 워커 전용(API_BASE_URL, INTERNAL_WORKER_TOKEN, WORKER_ID 등)이 없어도 되고, 워커 SSM에 API 전용이 빠져도 됨.

---

## 4. SAFE SPLIT: SSM 파라미터 분리

- **`/academy/api/env`**  
  API 컨테이너용. R2, VIDEO_BUCKET, SQS enqueue 권한, Redis, DB, SECRET_KEY 등 API에 필요한 키만 포함.  
  API는 **워커 전용 env에 의존하지 않도록** 함 (동일 .env 덮어쓰기로 워커용만 넣었을 때 R2/VIDEO_BUCKET이 빠지는 상황 방지).

- **`/academy/workers/env`**  
  워커(ASG/EC2)용. SQS poll, R2 read/write, Redis, DB, API_BASE_URL, INTERNAL_WORKER_TOKEN 등 워커에 필요한 키만 포함.

현재 `upload_env_to_ssm.ps1`은 **동일 .env 내용**을 두 파라미터에 모두 덮어쓰므로, 한쪽만 채워진 .env로 업로드하면 다른 쪽에서 R2/VIDEO_BUCKET 등이 빠질 수 있음.  
→ **API용 .env**와 **워커용 .env**를 나누어 각각 `/academy/api/env`, `/academy/workers/env`에 올리거나, 하나의 .env에 두 역할에 필요한 키를 모두 넣은 뒤 각 SSM에 올리는 방식**으로 정리 필요.

---

## 5. Corrected .env content for API (최소)

아래는 **upload complete → enqueue 파이프라인**이 동작하는 데 필요한 최소 키. 실제 값은 운영 환경에 맞게 채움.

```bash
# DB (API 공통)
DB_NAME=
DB_USER=
DB_PASSWORD=
DB_HOST=
DB_PORT=

# R2 (head_object, presign)
R2_ENDPOINT=
R2_ACCESS_KEY=
R2_SECRET_KEY=
R2_VIDEO_BUCKET=academy-video

# SQS enqueue
AWS_REGION=ap-northeast-2
VIDEO_SQS_QUEUE_NAME=academy-video-jobs

# Redis (backlog incr)
REDIS_HOST=
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0

# Django
SECRET_KEY=
DEBUG=false
```

EC2에서 IAM Role로 SQS 접근 시 `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`는 생략 가능.  
그 외 base.py에서 쓰는 키(LAMBDA_INTERNAL_API_KEY, SITE_URL, SOLAPI_* 등)는 API 전체 기능을 위해 기존처럼 .env에 두고 SSM에도 넣어두면 됨.

---

## 6. AWS CLI: /academy/api/env 업로드

API용 .env 파일을 로컬에 `api.env`로 두었다고 가정.

**PowerShell (Windows):**

```powershell
aws ssm put-parameter `
  --name "/academy/api/env" `
  --type "SecureString" `
  --value (Get-Content -Raw -Path "C:\path\to\api.env") `
  --overwrite `
  --region ap-northeast-2
```

**Bash (Linux / EC2):**

```bash
aws ssm put-parameter \
  --name "/academy/api/env" \
  --type "SecureString" \
  --value "$(cat /path/to/api.env)" \
  --overwrite \
  --region ap-northeast-2
```

내용이 4096자 초과 시 `--tier Advanced` 추가.

---

## 7. API EC2에서 SSM 복원 및 컨테이너 재시작

```bash
aws ssm get-parameter \
  --name /academy/api/env \
  --with-decryption \
  --region ap-northeast-2 \
  --query Parameter.Value \
  --output text > /home/ec2-user/.env

docker restart academy-api
```

기존 .env와 병합하려면 (SSM에 없는 키는 기존 .env 값 유지):

```bash
bash /home/ec2-user/academy/scripts/merge_ssm_into_env.sh /home/ec2-user/.env ap-northeast-2 /academy/api/env
bash /home/ec2-user/academy/scripts/refresh_api_container_env.sh
```

---

## 8. VALIDATION STEP (코드 실제 사용 변수명 기준)

코드는 **CLOUDFLARE_R2_* / VIDEO_BUCKET_NAME / REDIS_URL**를 사용하지 않음.  
아래는 **실제 코드에서 참조하는 변수명**으로 검사.

```bash
docker exec -it academy-api bash -lc '
python - << "PY"
import os
for k in [
  "R2_ENDPOINT",
  "R2_ACCESS_KEY",
  "R2_SECRET_KEY",
  "R2_VIDEO_BUCKET",
  "VIDEO_SQS_QUEUE_NAME",
  "AWS_REGION",
  "REDIS_HOST",
]:
  print(k, "=", "SET" if os.getenv(k) else "MISSING")
PY'
```

**모두 SET이어야** upload complete → head_object → presign → create_job_and_enqueue → SQS send + Redis incr 이 정상 동작함.

(요청하신 `CLOUDFLARE_R2_ENDPOINT`, `CLOUDFLARE_R2_ACCESS_KEY_ID`, `CLOUDFLARE_R2_SECRET_ACCESS_KEY`, `VIDEO_BUCKET_NAME`, `REDIS_URL`는 이 코드베이스에 없으므로, 다른 시스템과 맞추려면 API 서버에서 해당 이름을 R2_* / REDIS_HOST 등으로 매핑하는 래퍼나 설정이 필요.)

---

## 9. 요약

| 항목 | 내용 |
|------|------|
| **A) REQUIRED_API_ENV** | R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY, R2_VIDEO_BUCKET, VIDEO_SQS_QUEUE_NAME, AWS_REGION, REDIS_HOST (+ REDIS_PORT, REDIS_PASSWORD, REDIS_DB 선택), DB_*, SECRET_KEY |
| **B) REQUIRED_WORKER_ENV** | 위 R2/SQS/Redis + API_BASE_URL, INTERNAL_WORKER_TOKEN, WORKER_ID, R2_PREFIX, R2_REGION 등 (config.py + settings.worker 기준) |
| **C) diff** | API: enqueue + R2 read + Redis incr. Worker: poll + R2 read/write + Redis read/write + API 호출 |
| **D) API .env** | 위 §5 Corrected .env 참고 |
| **E) SSM 업로드** | §6 AWS CLI put-parameter |
| **F) EC2 복원** | §7 get-parameter → .env, docker restart (또는 merge 스크립트 후 refresh) |
| **Validation** | §8 — R2_*, VIDEO_SQS_QUEUE_NAME, AWS_REGION, REDIS_HOST 모두 SET 확인 |

이대로 적용하면 동시 업로드 시 SQS enqueue와 BacklogCount 기반 Auto Scaling이 동작하는 데 필요한 API env가 SSM과 EC2에서 누락되지 않습니다.
