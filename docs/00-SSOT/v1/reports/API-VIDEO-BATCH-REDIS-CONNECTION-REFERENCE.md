# API ↔ Video Batch / Redis 연결 참조 대조

**목적:** API 배포 후 "프로세스 재시작"이 아니라 **연결 참조 일치** 확인.  
upload_complete 이후 API가 올바른 Batch Queue / Job Definition / Compute Environment / Redis를 바라보는지 검증.

---

## 1. 설정 키 이름 (코드 기준)

Django `apps/api/config/settings/base.py` 및 `apps/support/video/services/batch_submit.py`에서 참조하는 환경변수:

| 키 | 용도 | base.py 기본값 (env 없을 때) |
|----|------|-----------------------------|
| `VIDEO_BATCH_JOB_QUEUE` | Standard 작업 제출 큐 | `academy-v1-video-batch-queue` |
| `VIDEO_BATCH_JOB_DEFINITION` | Standard Job Definition | `academy-v1-video-batch-jobdef` |
| `VIDEO_BATCH_JOB_QUEUE_LONG` | Long(3h+) 작업 제출 큐 | `academy-v1-video-batch-long-queue` |
| `VIDEO_BATCH_JOB_DEFINITION_LONG` | Long Job Definition | `academy-v1-video-batch-long-jobdef` |
| `VIDEO_BATCH_COMPUTE_ENV_NAME` | 참고용 CE 이름 | `academy-v1-video-batch-ce` |
| `REDIS_HOST` | 비디오 진행/락 등 (캐시) | (없음, 미설정 시 Redis 미사용) |
| `REDIS_PORT` | Redis 포트 | 6379 |
| `REDIS_PASSWORD` | Redis 비밀번호 | (선택) |
| `REDIS_DB` | Redis DB 인덱스 | 0 |

---

## 2. SSOT (scripts/v1)

**파일:** `docs/00-SSOT/v1/params.yaml`  
**로더:** `scripts/v1/core/ssot.ps1` → `Load-SSOT` 후 `$script:VideoQueueName` 등 사용

| SSOT 변수 | params.yaml 경로 | 기대값 (v1) |
|-----------|------------------|-------------|
| VideoQueueName | videoBatch.standard.videoQueueName | `academy-v1-video-batch-queue` |
| VideoJobDefName | videoBatch.standard.workerJobDefName | `academy-v1-video-batch-jobdef` |
| VideoCEName | videoBatch.standard.computeEnvironmentName | `academy-v1-video-batch-ce` |
| VideoLongQueueName | videoBatch.long.videoQueueName | `academy-v1-video-batch-long-queue` |
| VideoLongJobDefName | videoBatch.long.workerJobDefName | `academy-v1-video-batch-long-jobdef` |
| VideoLongCEName | videoBatch.long.computeEnvironmentName | `academy-v1-video-batch-long-ce` |
| RedisReplicationGroupId | redis.replicationGroupId | `academy-v1-redis` |

**주의:** Redis **호스트명**은 params에 없음. ElastiCache `academy-v1-redis`의 Primary Endpoint를 AWS에서 조회해 `REDIS_HOST`로 넣어야 함.

---

## 3. API Prod Env 소스 (실제 동작)

- **런타임 소스:** SSM Parameter Store `/academy/api/env` (JSON 또는 Base64 JSON).
- **적용 경로:** EC2 UserData / Rapid Deploy → `aws ssm get-parameter` → `/opt/api.env` → `docker run --env-file /opt/api.env`.
- **갱신 스크립트:**
  - `scripts/v1/update-api-env-sqs.ps1` — SQS 큐 이름만 SSOT에서 주입. **VIDEO_BATCH_* 는 기존에 주입하지 않음.**
  - VIDEO_BATCH_* / REDIS_HOST 는 수동으로 SSM에 넣었거나, 예전 .env 기반으로 한 번 넣은 상태일 수 있음.

**재배포 후 끊김 시나리오:**  
SSM `/academy/api/env`에 `VIDEO_BATCH_JOB_QUEUE=academy-video-batch-queue`(v1 없음) 또는 값 자체가 비어 있으면, API는 Django 기본값 `academy-v1-video-batch-queue`를 쓰거나(env 없을 때), **잘못된 큐 이름**을 바라보게 됨.  
AWS에 실제로 있는 큐는 `academy-v1-video-batch-queue`이므로, **이름이 불일치하면 Batch 제출 실패 또는 잘못된 큐로 제출됨.**

---

## 4. .env.example vs .env vs .env.deploy

| 파일 | VIDEO_BATCH_* | REDIS_HOST | 비고 |
|------|----------------|------------|------|
| `.env.example` | v1 이름 (SSOT와 동일) | placeholder | SSOT 반영용 템플릿 |
| `.env` (로컬/과거) | **v1 없이** `academy-video-batch-queue` 등 있을 수 있음 | 실제 ElastiCache 주소 | SSM과 무관; 로컬용 |
| `.env.deploy` | **없음** (prepare_deploy_env.py가 출력하지 않음) | **비어 있음** | SSM에서 가져와 쓴다는 전제; VIDEO_BATCH/REDIS는 SSM에 있어야 함 |

---

## 5. 실제 AWS 리소스 이름 (확인 방법)

다음과 일치해야 함 (params.yaml v1 기준).

**Batch:**

```bash
aws batch describe-job-queues --region ap-northeast-2 --query 'jobQueues[*].jobQueueName' --output text
# 기대: academy-v1-video-batch-queue academy-v1-video-batch-long-queue academy-v1-video-ops-queue ...
aws batch describe-job-definitions --status ACTIVE --region ap-northeast-2 --query 'jobDefinitions[*].jobDefinitionName' --output text
# 기대: academy-v1-video-batch-jobdef academy-v1-video-batch-long-jobdef ...
aws batch describe-compute-environments --region ap-northeast-2 --query 'computeEnvironments[*].computeEnvironmentName' --output text
# 기대: academy-v1-video-batch-ce academy-v1-video-batch-long-ce academy-v1-video-ops-ce ...
```

**Redis (ElastiCache):**

```bash
aws elasticache describe-replication-groups --replication-group-id academy-v1-redis --region ap-northeast-2 --query 'ReplicationGroups[0].NodeGroups[0].PrimaryEndpoint.Address' --output text
# 이 주소가 REDIS_HOST 로 SSM /academy/api/env 에 들어가 있어야 함.
```

---

## 6. 검증 체크리스트 (재배포 후)

1. **SSM `/academy/api/env` 내용**
   - `VIDEO_BATCH_JOB_QUEUE` = `academy-v1-video-batch-queue`
   - `VIDEO_BATCH_JOB_DEFINITION` = `academy-v1-video-batch-jobdef`
   - `VIDEO_BATCH_JOB_QUEUE_LONG` = `academy-v1-video-batch-long-queue`
   - `VIDEO_BATCH_JOB_DEFINITION_LONG` = `academy-v1-video-batch-long-jobdef`
   - `VIDEO_BATCH_COMPUTE_ENV_NAME` = `academy-v1-video-batch-ce` (선택)
   - `REDIS_HOST` = ElastiCache `academy-v1-redis` Primary Endpoint (필요 시)

2. **params.yaml**
   - `videoBatch.standard.videoQueueName` 등이 위와 동일한 v1 이름인지 확인.

3. **AWS 실제 리소스**
   - 위 5번 명령으로 큐/JobDef/CE 이름이 v1 접두사와 일치하는지 확인.
   - Redis replication group `academy-v1-redis` 존재 및 Primary Endpoint가 SSM REDIS_HOST와 일치하는지 확인.

4. **API 컨테이너 실제 env**
   - EC2에서: `docker exec academy-api env | grep -E 'VIDEO_BATCH|REDIS_HOST'`
   - SSM → `/opt/api.env` 반영 후 컨테이너 재시작했는지 확인.

---

## 7. 수정 사항 요약

- **스크립트:** `scripts/v1/update-api-env-sqs.ps1`를 확장해, SSOT에서 **VIDEO_BATCH_*** 를 읽어 `/academy/api/env`에 merge 하도록 추가함.  
  → 재배포 후 한 번 실행하면 API가 항상 params.yaml과 동일한 큐/JobDef를 참조함.
- **REDIS_HOST:** ElastiCache 주소는 params에 없으므로, 기존처럼 SSM에 수동 설정하거나, 별도 스크립트로 replication group에서 Primary Endpoint를 조회해 SSM에 넣는 방식 유지.

이 문서와 스크립트 반영으로 **연결 참조 끊김**을 방지하고, 재배포 후에도 API가 올바른 Batch/Redis 대상을 바라보는지 위 체크리스트로 검증할 수 있음.
