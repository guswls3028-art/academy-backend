# V1 배포 인프라 연결 참조 감사 보고서

**목적:** 배포 후 **연결 참조 불일치**(broken connection references) 여부 검증.  
프로세스 재시작 이슈가 아닌, **서비스가 올바른 인프라 리소스 이름을 참조하는지**만 검사.

**범위:** API, Messaging Worker, AI Worker, Video Batch, Redis, 배포 스크립트, SSOT.

---

## 1. SSOT 기대 리소스 이름 (Expected resource names from SSOT)

**소스:** `docs/00-SSOT/v1/params.yaml`  
**로더:** `scripts/v1/core/ssot.ps1` → `Load-SSOT` 후 `$script:*` 변수

| 리소스 유형 | params.yaml 경로 | 기대값 (v1) |
|-------------|------------------|-------------|
| **Batch Standard** | | |
| Job Queue | videoBatch.standard.videoQueueName | `academy-v1-video-batch-queue` |
| Job Definition | videoBatch.standard.workerJobDefName | `academy-v1-video-batch-jobdef` |
| Compute Environment | videoBatch.standard.computeEnvironmentName | `academy-v1-video-batch-ce` |
| **Batch Long** | | |
| Job Queue | videoBatch.long.videoQueueName | `academy-v1-video-batch-long-queue` |
| Job Definition | videoBatch.long.workerJobDefName | `academy-v1-video-batch-long-jobdef` |
| Compute Environment | videoBatch.long.computeEnvironmentName | `academy-v1-video-batch-long-ce` |
| **SQS** | | |
| Messaging Queue | messagingWorker.sqsQueueName | `academy-v1-messaging-queue` |
| AI Queue | aiWorker.sqsQueueName | `academy-v1-ai-queue` |
| **Redis** | | |
| Replication Group | redis.replicationGroupId | `academy-v1-redis` |
| **참고:** REDIS_HOST | (없음) | ElastiCache Primary Endpoint는 런타임 조회값. params에는 리소스 ID만 있음. |
| **SSM** | | |
| API env | ssm.apiEnv | `/academy/api/env` |
| Workers env | ssm.workersEnv | `/academy/workers/env` |
| **ECR** | | |
| API | ecr.apiRepo | `academy-api` |
| Video Worker | ecr.videoWorkerRepo | `academy-video-worker` |
| Messaging Worker | ecr.messagingWorkerRepo | `academy-messaging-worker` |
| AI Worker | ecr.aiWorkerRepo | `academy-ai-worker-cpu` |

---

## 2. 코드에서 사용하는 실제 참조 (Actual references used by code)

### 2.1 API (Django `apps/api/config/settings/base.py`)

| 환경변수 | 용도 | 코드 기본값 (env 없을 때) |
|----------|------|---------------------------|
| VIDEO_BATCH_JOB_QUEUE | Batch 표준 큐 제출 | `academy-v1-video-batch-queue` |
| VIDEO_BATCH_JOB_DEFINITION | Batch 표준 JobDef | `academy-v1-video-batch-jobdef` |
| VIDEO_BATCH_JOB_QUEUE_LONG | Batch Long 큐 | `academy-v1-video-batch-long-queue` |
| VIDEO_BATCH_JOB_DEFINITION_LONG | Batch Long JobDef | `academy-v1-video-batch-long-jobdef` |
| VIDEO_BATCH_COMPUTE_ENV_NAME | CE 참고용 | `academy-v1-video-batch-ce` |
| MESSAGING_SQS_QUEUE_NAME | 메시징 SQS enqueue | `academy-v1-messaging-queue` |
| AI_SQS_QUEUE_NAME_LITE/BASIC/PREMIUM | AI SQS enqueue | `academy-v1-ai-queue` |
| REDIS_HOST / REDIS_PORT | Redis 클라이언트 (`libs/redis/client.py`) | base.py에는 미정의. env 없으면 Redis 미사용(None). |

- **batch_submit.py:** `getattr(settings, "VIDEO_BATCH_JOB_QUEUE", ...)` 등 동일 기본값 사용.  
- **Redis:** `libs/redis/client.py`는 `os.getenv("REDIS_HOST")`만 사용. 설정 모듈에는 REDIS_* 미정의.

### 2.2 Messaging Worker (`apps/worker/messaging_worker/config.py`)

| 환경변수 | 용도 | 코드 기본값 |
|----------|------|-------------|
| MESSAGING_SQS_QUEUE_NAME | SQS 수신 큐 이름 | `academy-v1-messaging-queue` |

### 2.3 AI Worker

- **run.py:** 작업 수신은 **API 내부 엔드포인트** (`/api/v1/internal/ai/job/next/`) 폴링. SQS 큐 이름은 API가 enqueue 시 사용.
- **worker.py (Django settings):** `AI_SQS_QUEUE_NAME_*` = `academy-v1-ai-queue` (env 기본값). API가 SQS에 넣을 때 사용.

### 2.4 Video Batch Worker

- **batch_entrypoint.py:** 부팅 시 SSM `/academy/workers/env` (또는 `BATCH_SSM_ENV`)에서 JSON/base64 JSON 로드 → `os.environ` 설정.
- **필수 키:** `REDIS_HOST`, `REDIS_PORT`, `DB_*`, `R2_*`, `API_BASE_URL`, `INTERNAL_WORKER_TOKEN` 등. **큐/JobDef 이름은 불필요** (Batch가 `parameters.job_id`로 전달).
- Job 수신: AWS Batch API가 큐/JobDef 기준으로 스케줄 → 컨테이너에 `VIDEO_JOB_ID` 등 전달.

---

## 3. 환경변수 소스 (Environment variable sources)

| 대상 | 런타임 소스 | 갱신/설정 방법 |
|------|-------------|----------------|
| **API** | SSM `/academy/api/env` (JSON) | EC2 UserData / refresh-api-env.sh 가 SSM → `/opt/api.env` → `docker run --env-file /opt/api.env`. **.env / .env.deploy 는 프로덕션 API 소스가 아님.** |
| **Messaging / AI Worker** | SSM `/academy/workers/env` (base64 JSON) | Bootstrap: `.env` 읽어서 생성·갱신. SQS 큐 이름은 SSOT에서 채움 (없을 때만). |
| **Video Batch Job** | SSM `/academy/workers/env` | Batch 태스크 시작 시 `batch_entrypoint.py`가 SSM 조회 → `os.environ` 설정 후 워커 실행. |

**API env에 대한 주입:**

- **update-api-env-sqs.ps1:** 기존 SSM 값을 **읽어서 merge** 후 저장.
  - 주입 키: `MESSAGING_SQS_QUEUE_NAME`, `AI_SQS_QUEUE_NAME_*`, `VIDEO_BATCH_JOB_QUEUE`, `VIDEO_BATCH_JOB_DEFINITION`, `VIDEO_BATCH_COMPUTE_ENV_NAME`, Long 큐/JobDef.
  - **REDIS_HOST / REDIS_PORT 는 이 스크립트에서 설정하지 않음** (params에 호스트 없음). 기존 SSM에 있으면 merge 시 유지됨.

---

## 4. 배포 시 환경 주입 분석 (Deployment injection analysis)

| 스크립트 | 역할 | API env | Workers env | REDIS_HOST |
|----------|------|---------|-------------|------------|
| **deploy.ps1** | Ensure 인프라 + Bootstrap | 호출 안 함 | Bootstrap으로 생성/갱신 | Workers만 (.env 기반) |
| **update-api-env-sqs.ps1** | SSM `/academy/api/env` merge | SQS + VIDEO_BATCH_* 주입 | - | 주입 안 함 (기존 값 유지) |
| **bootstrap.ps1** | Invoke-BootstrapWorkersEnv, SQS, RDS, ECR 등 | API env 생성/수정 안 함 | .env → requiredKeys + SQS 이름(SSOT) | requiredKeys에 포함 (.env에서 복사) |
| **api.ps1** (UserData) | EC2 부팅 시 | SSM → `/opt/api.env` → docker | - | SSM에 있으면 적용 |
| **worker_userdata.ps1** | Worker EC2 부팅 시 | - | SSM → `/opt/workers.env` → docker | SSM에 있으면 적용 |

**정리:**

- **VIDEO_BATCH_***: API에는 `update-api-env-sqs.ps1`로 SSOT에서 주입됨. 배포 스크립트가 이 스크립트를 **자동 호출하지는 않음** (수동 또는 CI에서 실행 필요).
- **REDIS_HOST (API):** params/ssot에 호스트 없음. ElastiCache Primary Endpoint는 수동으로 SSM에 넣거나, 별도 스크립트로 replication group 조회 후 SSM 갱신해야 함. `update-api-env-sqs.ps1`는 merge만 하므로 기존 REDIS_HOST가 있으면 유지됨.
- **REDIS (Workers):** Bootstrap이 workers env를 `.env` 기반으로 만들 때 `REDIS_HOST`, `REDIS_PORT`를 requiredKeys로 요구. 따라서 `.env`에 올바른 ElastiCache 주소가 있어야 함.

---

## 5. 연결 맵 (Connection map)

```
API (Django, ECS/EC2)
  → Batch: VIDEO_BATCH_JOB_QUEUE / _JOB_DEFINITION (및 Long) — submit_batch_job
  → SQS: MESSAGING_SQS_QUEUE_NAME, AI_SQS_QUEUE_NAME_* — enqueue
  → Redis: REDIS_HOST, REDIS_PORT — libs/redis/client (캐시/진행률, 없으면 None)

Messaging Worker (ECS worker)
  → SQS: MESSAGING_SQS_QUEUE_NAME (receive/send/delete)
  → Redis: REDIS_HOST, REDIS_PORT — idempotency, 진행률

AI Worker (ECS worker)
  → API: /api/v1/internal/ai/job/next/ (폴링)
  → SQS: (API가 enqueue 시 사용; 워커는 API 경유)
  → Redis: REDIS_HOST, REDIS_PORT — 진행률 등

Video Batch (AWS Batch, 1 video = 1 job)
  → Compute Environment / Job Definition / Queue: Batch가 스케줄 (코드에서 큐 이름 참조 없음)
  → Redis: REDIS_HOST, REDIS_PORT — SSM /academy/workers/env
  → DB, R2, API_BASE_URL: SSM /academy/workers/env

Redis (ElastiCache)
  ← API, Messaging Worker, AI Worker, Video Batch (REDIS_HOST/REDIS_PORT)
  리소스 이름(SSOT): redis.replicationGroupId = academy-v1-redis
```

---

## 6. 감지된 불일치 (Detected mismatches)

| # | 구분 | 내용 | 심각도 |
|---|------|------|--------|
| 1 | **.env vs SSOT** | `.env`에 `VIDEO_BATCH_JOB_QUEUE=academy-video-batch-queue` (v1 없음). 로컬에서 API를 .env로 돌리면 잘못된 큐 참조. 프로덕션 API는 SSM만 사용하므로 프로덕션 직접 영향 없음. | 중 (로컬/일관성) |
| 2 | **REDIS_HOST (API)** | `update-api-env-sqs.ps1`가 REDIS_HOST/REDIS_PORT를 주입하지 않음. SSM에 기존 값이 있으면 merge로 유지되나, **최초 API env 생성** 또는 **전체 치환** 시 Redis 누락 가능. | 중 (초기 설정/재생성 시) |
| 3 | **deploy.ps1와 update-api-env-sqs.ps1** | `deploy.ps1`가 `update-api-env-sqs.ps1`를 호출하지 않음. 배포만 하고 SSM API env를 갱신하지 않으면, 예전에 수동으로 넣은 VIDEO_BATCH_* 등이 그대로여서 의도치 않은 큐/JobDef 참조 가능. | 중 (운영 절차) |
| 4 | **params vs 코드 기본값** | 현재는 일치. base.py, worker.py, batch_submit.py, messaging config 기본값이 모두 v1 이름(academy-v1-*)과 동일. | 없음 |

---

## 7. 끊긴 참조 (Broken references)

- **코드가 기대하는 이름 vs SSOT:**  
  코드 기본값과 params.yaml v1 이름은 **일치**함.  
  끊긴 참조는 “코드와 SSOT가 다름”이 아니라, **배포/설정 절차**로 인해 잘못된 값이 SSM이나 .env에 들어가는 경우에 해당함.

- **확인된 리스크:**
  1. **API SSM에 VIDEO_BATCH_* 가 이전 값으로 남아 있는 경우**  
     → `update-api-env-sqs.ps1` 미실행 시 잘못된 큐/JobDef 참조 가능.  
     (이미 문서화: `docs/00-SSOT/v1/reports/API-VIDEO-BATCH-REDIS-CONNECTION-REFERENCE.md`)
  2. **.env의 VIDEO_BATCH_JOB_QUEUE**  
     → 로컬/개발에서만 사용 시 `academy-video-batch-queue` 등 구 이름 사용 가능.  
  3. **API용 REDIS_HOST**  
     → SSM에 한 번도 넣지 않았거나, 전체 치환 시 빠지면 API에서 Redis 미연결(캐시/진행률만 영향).

---

## 8. 최소 수정 제안 (Minimal fixes required)

인프라 재설계 없이 **SSOT → 배포 스크립트 → 런타임 env → AWS 리소스**가 맞도록 하는 최소 변경만 제안.

| # | 대상 | 제안 | 비고 |
|---|------|------|------|
| 1 | **.env** | `VIDEO_BATCH_JOB_QUEUE=academy-video-batch-queue` → `VIDEO_BATCH_JOB_QUEUE=academy-v1-video-batch-queue` 등으로 v1 이름으로 통일. (.env.example은 이미 v1 기준) | 로컬/문서 일관성 |
| 2 | **REDIS_HOST (API)** | (선택) ElastiCache Primary Endpoint를 자동 반영하려면: `update-api-env-sqs.ps1` 또는 별도 스크립트에서 `redis.replicationGroupId`로 describe-replication-groups 조회 후 Primary Endpoint를 `/academy/api/env`에 REDIS_HOST로 merge. 없으면 기존처럼 수동 설정 유지. | 초기 설정/재생성 시 Redis 누락 방지 |
| 3 | **배포 절차** | `deploy.ps1` 실행 **후** 또는 배포 파이프라인에서 **한 번** `update-api-env-sqs.ps1` 실행하도록 Runbook/CI에 명시. (또는 선택적으로 deploy.ps1 끝에서 호출해 SSM API env를 SSOT 기준으로 갱신) | VIDEO_BATCH_* / SQS 이름이 항상 SSOT와 일치하도록 |
| 4 | **문서** | 이미 있음: `API-VIDEO-BATCH-REDIS-CONNECTION-REFERENCE.md`에 SSM 점검·verify 스크립트 안내. 재배포 후 연결 확인 시 동일 문서 참고. | 유지 |

---

## 9. 요약

- **SSOT (params.yaml)와 코드 기본값은 v1 이름으로 일치**하며, Batch/SQS/Redis 리소스 이름에 대한 코드↔SSOT 불일치는 없음.
- **연결 끊김 위험**은 다음에서 옴:
  - API env(SSM)에 VIDEO_BATCH_* / SQS가 예전 값으로 남는 경우 → `update-api-env-sqs.ps1`로 SSOT 기준 갱신 필요.
  - API SSM에 REDIS_HOST가 없거나 재생성 시 빠지는 경우 → 수동 또는 스크립트로 ElastiCache Primary Endpoint 주입.
  - 로컬 .env의 VIDEO_BATCH_* 가 구 이름인 경우 → v1 이름으로 맞추면 로컬에서도 올바른 참조.
- **최소 수정:** .env v1 통일, (선택) REDIS_HOST 자동 주입, 배포 후 `update-api-env-sqs.ps1` 실행 명시 또는 자동화.

이 문서는 저장소와 설정 로직만 분석한 것이며, AWS 리소스 실제 존재 여부는 `verify-video-batch-connection.ps1` 등으로 별도 확인해야 함.
