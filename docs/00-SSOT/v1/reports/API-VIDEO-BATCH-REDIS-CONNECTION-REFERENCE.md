# API ↔ Video Batch / Redis 연결 참조 대조

**목적:** API 배포 후 "프로세스 재시작"이 아니라 **연결 참조 일치** 확인.  
upload_complete 이후 API가 올바른 Batch Queue / Job Definition / Compute Environment / Redis를 바라보는지 검증.

**인프라·이미지 정합:** 이미지 빌드(GitHub Actions)와 풀배포(deploy.ps1) 정합 및 최신 인프라 요약은 `docs/00-SSOT/v1/reports/INFRA-IMAGE-BUILD-DEPLOY-ALIGNMENT.md` 참고. SSOT는 `docs/00-SSOT/v1/params.yaml`.

**AWS 프로필:** V1 배포·검증 시 **반드시 프로필 `default` 사용.** 사용자에게 프로필을 묻지 않는다.

---

## 확정을 위한 2단계 확인 (이 두 개만 확인하면 끝)

**로컬에서 AWS 자격 증명이 있다면** 아래 스크립트로 한 번에 점검할 수 있음.  
**프로필은 반드시 `default`.** (스크립트 내부에서 `--profile default` 사용.)

```powershell
# PowerShell (backend 폴더에서)
pwsh -File scripts/v1/verify-video-batch-connection.ps1
```

```bash
# Bash (backend 폴더에서)
AWS_PROFILE=default bash scripts/v1/verify-video-batch-connection.sh
```

스크립트가 하는 일: (1) SSM `/academy/api/env`에서 VIDEO_BATCH_* 4개 값이 v1 이름과 일치하는지, (2) Batch 큐/JobDef/CE 존재 여부, (3) 해당 큐 최근 job 5건 표시.

---

| 단계 | 확인 내용 | 명령/위치 |
|------|-----------|-----------|
| **1) SSM env** | `/academy/api/env`에 VIDEO_BATCH_* 가 v1 이름인지 | `aws ssm get-parameter --name /academy/api/env --region ap-northeast-2 --with-decryption` → JSON에서 `VIDEO_BATCH_JOB_QUEUE`, `VIDEO_BATCH_JOB_DEFINITION`, `VIDEO_BATCH_JOB_QUEUE_LONG`, `VIDEO_BATCH_JOB_DEFINITION_LONG` 확인 |
| **2) Batch job 실제 생성** | 업로드 한 번 한 뒤 해당 큐에 job이 생성되는지 | `aws batch list-jobs --job-queue academy-v1-video-batch-queue --region ap-northeast-2` (SUBMITTED/RUNNABLE/RUNNING/SUCCEEDED 등) |

**SSM에서 반드시 아래 값이어야 함 (하나라도 틀리면 submitJob 실패 → worker 안 도는 것처럼 보임):**

- `VIDEO_BATCH_JOB_QUEUE` = `academy-v1-video-batch-queue`
- `VIDEO_BATCH_JOB_DEFINITION` = `academy-v1-video-batch-jobdef`
- `VIDEO_BATCH_JOB_QUEUE_LONG` = `academy-v1-video-batch-long-queue`
- `VIDEO_BATCH_JOB_DEFINITION_LONG` = `academy-v1-video-batch-long-jobdef`

---

## submitJob 로그 확인 (upload_complete → submitJob → Batch job 생성)

API 로그에서 **실제로 submitJob이 호출되었는지**, 실패했다면 **어디서 실패했는지** 확인하려면 아래 로그를 검색한다.

| 로그 메시지 (검색 키워드) | 의미 | 위치 |
|---------------------------|------|------|
| `BATCH_SUBMIT_ROUTE` | Standard/Long 라우팅 결정 직후 (queue/jobDef 이름 결정됨) | `apps/support/video/services/batch_submit.py` |
| `BATCH_SUBMIT` | `batch.submit_job()` 성공. `aws_job_id`, `queue` 포함 | 동일 |
| `BATCH_SUBMIT_FAILED` | `submit_job()` 호출 후 AWS 예외 (queue 없음, jobDef 없음, 권한 등) | 동일 |
| `BATCH_SUBMIT_ERROR` | 그 외 예외 | 동일 |
| `VIDEO_UPLOAD_ENQUEUE_FAILED` | `create_job_and_submit_batch` 가 False/None 반환 (제한 걸림 또는 submit 실패) | `video_views.py` |

**확인 순서:**

1. 업로드 완료 후 API 로그에 `BATCH_SUBMIT_ROUTE` 가 있는지 → 있으면 upload_complete → submit_batch_job 진입까지 도달한 것.
2. 그 다음 `BATCH_SUBMIT` 가 있는지 → 있으면 Batch job 생성 성공.
3. `BATCH_SUBMIT_FAILED` / `BATCH_SUBMIT_ERROR` 가 있으면 → 메시지에 queue/jobDef 이름 또는 AWS 에러가 있으므로, SSM 값 불일치 또는 AWS 리소스 없음으로 해석.

**예시 (CloudWatch Logs 또는 docker logs):**

```text
# 성공 시
BATCH_SUBMIT_ROUTE | job_id=123 | duration_sec=3600 | use_long=false
BATCH_SUBMIT | job_id=123 | aws_job_id=abc-xxx | queue=academy-v1-video-batch-queue | long=false

# 실패 시 (queue 이름 불일치 등)
BATCH_SUBMIT_FAILED | job_id=123 | error=...
```

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

**Redis:** 인코딩 자체는 Batch 기반이라, Redis가 없어도 job은 제출·실행됨. Redis는 **상태/진행 캐시**용. Redis 문제면 보통 **인코딩은 되고 progress 표시만 안 됨**.

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
- **적용 경로:** EC2 UserData 부팅 시 `aws ssm get-parameter` → `/opt/api.env` → `docker run --env-file /opt/api.env`.  
  **기동 중인 인스턴스**는 부팅 시점의 SSM만 갖고 있으므로, SSM을 나중에 갱신하면 **반드시** 아래 중 하나로 반영해야 함:
  - **권장:** `scripts/v1/deploy.ps1` 실행. 배포 마지막에 `Invoke-SyncEnvFromSSOT`(SSM 갱신) 후 `Invoke-RefreshApiEnvOnInstances`(기동 중인 API 인스턴스에 SSM 재적용 + 컨테이너 재시작)가 자동 실행됨.
  - **SSM만 수동 갱신한 경우:** `pwsh scripts/v1/refresh-api-env.ps1` 실행 후, API가 올바른 VIDEO_BATCH_* / REDIS_HOST 를 사용하는지 검증.
- **갱신 스크립트:**
  - `scripts/v1/deploy.ps1` — 인프라 Ensure 후 `core/sync_env.ps1`의 `Invoke-SyncEnvFromSSOT`로 `/academy/api/env`에 SQS, Video Batch, Redis(자동 발견) merge. 이어서 `Invoke-RefreshApiEnvOnInstances`로 기동 중 API에 적용.
  - `scripts/v1/update-api-env-sqs.ps1` — 배포 없이 SSM만 갱신할 때 사용. 실행 후 반드시 `refresh-api-env.ps1` 또는 instance-refresh로 API에 반영.
  - REDIS_HOST 는 `Get-RedisPrimaryEndpoint`(params `redis.replicationGroupId` 기준)로 자동 조회 후 Sync 시 SSM에 넣음.

**재배포 후 끊김 시나리오 (원인 및 해결):**  
SSM만 갱신하고 **기동 중인 API 컨테이너를 재시작하지 않으면** API는 예전 `/opt/api.env`를 계속 사용함.  
→ **해결:** `deploy.ps1` 한 번 실행( Sync + Refresh 포함 )하거나, SSM 수동 변경 후 `refresh-api-env.ps1` 실행.

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

- **deploy.ps1:** 인프라 Ensure 후 `Invoke-SyncEnvFromSSOT`(SSOT + Redis discovery → SSM) 실행, 이어서 `Invoke-RefreshApiEnvOnInstances`로 기동 중인 API 인스턴스에 갱신된 SSM 적용 및 컨테이너 재시작.  
  → 재배포 시 **반드시** `deploy.ps1` 한 번 실행하면 SSOT와 런타임 env가 일치하고, 비디오 파이프라인이 올바른 Batch 큐/JobDef를 참조함.
- **update-api-env-sqs.ps1:** 배포 없이 SSM만 갱신할 때 사용. 실행 후 `refresh-api-env.ps1`로 API에 반영 필요.
- **REDIS_HOST:** Sync 시 `Get-RedisPrimaryEndpoint`로 ElastiCache Primary Endpoint 자동 조회 후 SSM에 주입.

---

## 8. 점검 결과 (실제 인프라, `--profile default`)

**점검 일시:** 문서 최종 수정 시점 기준.  
**방법:** `aws ... --region ap-northeast-2 --profile default`

| 항목 | 결과 | 비고 |
|------|------|------|
| **SSM /academy/api/env** | ✅ 일치 | `VIDEO_BATCH_JOB_QUEUE`=academy-v1-video-batch-queue, `VIDEO_BATCH_JOB_DEFINITION`=academy-v1-video-batch-jobdef, Long 큐/JobDef 동일. `REDIS_HOST` 설정됨. |
| **Batch Job Queues** | ✅ ENABLED | academy-v1-video-batch-queue, academy-v1-video-batch-long-queue, academy-v1-video-ops-queue |
| **Batch Job Definitions (ACTIVE)** | ✅ 존재 | academy-v1-video-batch-jobdef, academy-v1-video-batch-long-jobdef, academy-v1-video-ops-* |
| **Batch Compute Environments** | ✅ ENABLED | academy-v1-video-batch-ce, academy-v1-video-batch-long-ce, academy-v1-video-ops-ce |
| **최근 Jobs (academy-v1-video-batch-queue)** | (없음) | list-jobs 최근 8건 빈 결과 — 최근 업로드/제출 이력 없음. 연결 참조는 정상. |

**결론:** API env(SSM) ↔ Batch 리소스 이름이 v1 기준으로 일치함. 연결 상태 정상.  
점검 스크립트는 `default` 프로필 사용(문서/운영 가이드와 동일).


---

## 8. Bash/Git Bash 주의 (SSM send-command · ALB 경로)

**SSM send-command `--parameters` JSON 이스케이프**

- `--parameters`에 넘기는 JSON 문자열에서 `\"batch\|upload\|video\"`처럼 **백슬래시+파이프(`\|`)** 를 쓰면 AWS CLI가 `Invalid \escape`로 거절함 (JSON에서 `\|`는 유효한 이스케이프가 아님).
- **해결:** grep 패턴은 **정규식 확장(`-E`) + 파이프만 사용**하도록 해서 JSON에 백슬래시가 들어가지 않게 한다.
  - ❌ `grep -i \"batch\|upload\|video\"` → JSON Invalid \escape
  - ✅ `grep -iE \"batch|upload|video\"` 또는 `grep -iE 'batch|upload|video'`

**예 (docker logs + grep, Bash에서 실행 시):**

```bash
MSYS_NO_PATHCONV=1 aws ssm send-command --instance-ids <INSTANCE_ID> \
  --document-name "AWS-RunShellScript" \
  --parameters '{"commands":["docker logs academy-api --since 24h 2>&1 | grep -iE \"batch|upload|video\" | tail -50"]}' \
  --region ap-northeast-2 --query 'Command.CommandId' --output text
```

**ALB `modify-target-group` `--health-check-path` (Git Bash)**

- Git Bash에서 `--health-check-path /health`처럼 슬래시로 시작하는 경로를 넘기면 `/health`가 **Windows 경로로 해석**되어 `C:/Program Files/Git/health` 같은 값이 전달될 수 있음.
- **해결:** 같은 셸에서 `MSYS_NO_PATHCONV=1`을 설정한 뒤 aws를 실행하거나, 경로를 따옴표로 감싼 절대 경로 형태로 넘긴다. (PowerShell에서 실행하는 `scripts/v1/resources/alb.ps1`는 해당 문제 없음.)

```bash
MSYS_NO_PATHCONV=1 aws elbv2 modify-target-group --target-group-arn <TG_ARN> --health-check-path /health --region ap-northeast-2
```

