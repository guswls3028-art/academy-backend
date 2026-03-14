# V1 배포 장애 원인 분석 및 수정 최종 보고서

**일시:** 2026-03-10  
**범위:** V1 배포 인프라, API/Workers SSM env, 비디오 파이프라인 연결 참조.

**실행 시 AWS 프로필:** V1 배포·검증·API 재배포 스크립트는 **반드시 프로필 `default` 사용.** 프로필을 묻지 않는다. (Cursor 룰 `07_aws_profile_default.mdc`, Runbook `RUNBOOK-DEPLOY-AND-ENV.md`.)

---

## 1. Actual V1 architecture discovered

| 컴포넌트 | 런타임 | env 소스 | 참조하는 리소스 |
|----------|--------|-----------|------------------|
| **API** | EC2 ASG + Docker (academy-api) | SSM `/academy/api/env` → UserData로 `/opt/api.env` 생성, 부팅 시 1회만 적용 | Batch Queue/JobDef(VIDEO_BATCH_*), SQS(MESSAGING/AI_*), Redis(REDIS_HOST/PORT) |
| **Messaging Worker** | EC2 ASG + Docker | SSM `/academy/workers/env` (base64 JSON), 부팅 시 1회만 적용 | SQS(MESSAGING_SQS_QUEUE_NAME), Redis |
| **AI Worker** | EC2 ASG + Docker | 동일 `/academy/workers/env` | API 내부 엔드포인트 폴링, SQS(API가 enqueue), Redis |
| **Video Batch** | AWS Batch (1 video = 1 job) | SSM `/academy/workers/env` (batch_entrypoint.py가 태스크 시작 시 조회) | Batch Queue/JobDef(스케줄러가 사용), Redis, DB, R2, API_BASE_URL |
| **Redis** | ElastiCache | params `redis.replicationGroupId` = academy-v1-redis. 호스트명은 SSOT에 없고 런타임 조회 필요 | API/Workers/Batch가 REDIS_HOST/REDIS_PORT로 접속 |

**연결 맵:**

- **SSOT (params.yaml)** → **deploy scripts (ssot.ps1, sync_env.ps1)** → **SSM (/academy/api/env, /academy/workers/env)** → **런타임 컨테이너** (UserData 또는 batch_entrypoint가 SSM 조회) → **AWS 리소스** (Batch, SQS, ElastiCache).
- **API** → Batch Queue/JobDef(submit_batch_job), SQS(enqueue), Redis(캐시/진행률).
- **Messaging Worker** → SQS(consume), Redis.
- **AI Worker** → API(/job/next), Redis.
- **Video Batch** → Batch Queue/JobDef(스케줄), Workers env, Redis, DB, R2.

---

## 2. Confirmed root cause(s)

1. **SSM 갱신만 하고 기동 중인 API 컨테이너에 반영하지 않음**  
   - deploy.ps1(및 기존 update-api-env-sqs.ps1)가 SSM `/academy/api/env`만 갱신함.  
   - API 인스턴스는 **부팅 시** SSM을 읽어 `/opt/api.env`를 만들고, 그 후로는 갱신하지 않음.  
   - 따라서 재배포 후 SSM에 VIDEO_BATCH_* / REDIS_HOST 가 올바르게 들어가도, **이미 떠 있는 API 컨테이너는 예전 env를 계속 사용**하여 잘못된 큐/JobDef 참조 또는 Redis 미연결 가능.

2. **VIDEO_BATCH_COMPUTE_ENV_NAME 누락**  
   - 검증 스크립트 실행 시 SSM에 `VIDEO_BATCH_COMPUTE_ENV_NAME`이 비어 있음이 확인됨.  
   - Sync 로직에서 해당 키를 SSOT로 채우도록 되어 있었으나, 배포 후 기동 중 API에 SSM이 반영되지 않아 동일 현상이 재현될 수 있음.

---

## 3. Evidence for each root cause

- **Evidence 1 (SSM vs 런타임 불일치):**  
  - `scripts/v1/resources/api.ps1`의 UserData: 부팅 시 `aws ssm get-parameter --name "$SsmApiEnvParam"` → `/opt/api.env` 작성 후 `docker run --env-file /opt/api.env`.  
  - 이후 SSM을 바꿔도 기동 중인 인스턴스는 `/opt/api.env`를 다시 읽지 않음.  
  - `scripts/v1/refresh-api-env.ps1` 주석: "기존 인스턴스는 부팅 시에만 SSM을 읽음. SSM 갱신 후 컨테이너만 재시작하려면 이 스크립트 사용."

- **Evidence 2 (VIDEO_BATCH_COMPUTE_ENV_NAME 빈 값):**  
  - `pwsh -File scripts/v1/verify-video-batch-connection.ps1` 실행 시  
    `VIDEO_BATCH_COMPUTE_ENV_NAME = (empty) -> MISMATCH (expected: academy-v1-video-batch-ce)` 출력.  
  - Sync 후 동일 스크립트 재실행 시 `VIDEO_BATCH_COMPUTE_ENV_NAME = academy-v1-video-batch-ce`, "SSM VIDEO_BATCH_* 일치" 확인.

- **Evidence 3 (배포 로그):**  
  - deploy.ps1 실행 시 "[Sync runtime env with SSOT]" 후 "Refreshing API env on 1 instance(s) from SSM /academy/api/env..." → "i-029673735471c504f : env refreshed, container restarted" 로, 한 대 API에서 SSM 재적용 및 재시작이 수행됨.

---

## 4. Files changed

| 파일 | 변경 내용 |
|------|------------|
| `scripts/v1/resources/api.ps1` | `Invoke-RefreshApiEnvOnInstances` 추가. SSM 갱신된 `/academy/api/env`를 기동 중 API 인스턴스에 SSM send-command로 적용 후 컨테이너 재시작. |
| `scripts/v1/deploy.ps1` | `Invoke-SyncEnvFromSSOT` 직후 `Invoke-RefreshApiEnvOnInstances` 호출 추가. 주석에 idempotent·env 동기화 설명 보강. |
| `docs/00-SSOT/v1/reports/API-VIDEO-BATCH-REDIS-CONNECTION-REFERENCE.md` | §3 API Prod Env 소스: deploy.ps1의 Sync+Refresh 자동 실행 명시, REDIS 자동 발견 설명, 재배포 후 끊김 원인 및 해결 정리. §7 수정 사항 요약을 위 내용으로 갱신. |
| `docs/00-SSOT/v1/RUNBOOK-DEPLOY-AND-ENV.md` | **신규.** V1 배포 절차, SSM만 수동 변경 시 refresh 절차, 재배포 후 끊김 방지, 검증 방법 정리. |

---

## 5. AWS / deployment resources changed

- **SSM:** `/academy/api/env` — Sync 시 VIDEO_BATCH_* (COMPUTE_ENV_NAME 포함), SQS, REDIS_HOST/REDIS_PORT(Redis discovery) 반영. 기존 키는 merge 유지.  
- **SSM:** `/academy/workers/env` — Sync 시 SQS, REDIS_HOST/REDIS_PORT 반영.  
- **API 인스턴스:** deploy.ps1 실행 시 `Invoke-RefreshApiEnvOnInstances`로 1대(이번 실행 기준 i-029673735471c504f)에 SSM 재조회 → `/opt/api.env` 갱신 → academy-api 컨테이너 재시작.  
- **인프라 리소스:** 기존 Batch CE/Queue/JobDef, SQS, Redis, ASG 등은 변경 없음. deploy.ps1에서 API Launch Template 버전 29로 갱신 및 instance-refresh 시작(기존 drift 해소).

---

## 6. What was aligned to SSOT

- **API env:** SSOT(SQS, Video Batch queue/jobdef/CE, Redis discovery)가 SSM `/academy/api/env`에 merge되고, **기동 중 API 인스턴스에 즉시 반영**되도록 배포 스크립트에 Refresh 단계 추가.  
- **Workers env:** SSOT(SQS, Redis discovery)가 SSM `/academy/workers/env`에 merge. (Workers는 부팅 시 SSM만 사용하므로, 새 인스턴스 또는 instance-refresh 시 반영.)  
- **Redis:** params `redis.replicationGroupId` 기준으로 Primary Endpoint 자동 조회 후 API/Workers SSM에 REDIS_HOST/REDIS_PORT 주입.  
- **배포 절차:** 단일 진입점은 `scripts/v1/deploy.ps1`. SSOT → 인프라 → SSM → 런타임 env 가 한 번에 맞도록 Sync + Refresh 순서 고정.

---

## 7. Redeploy steps executed

1. `pwsh -File scripts/v1/deploy.ps1 -SkipNetprobe` 실행 (backend 디렉터리에서).  
2. Bootstrap, Ensure RDS/Redis/SSM/ECR/DynamoDB/ASG/Batch/ALB, Ensure API(Launch Template 버전 29, instance-refresh 시작) 순으로 수행.  
3. **Invoke-SyncEnvFromSSOT** 실행: API env synced, Workers env synced (로그 확인).  
4. **Invoke-RefreshApiEnvOnInstances** 실행: 1대 API 인스턴스에서 "env refreshed, container restarted" 로그 확인.  
5. `pwsh -File scripts/v1/verify-video-batch-connection.ps1` 재실행: SSM VIDEO_BATCH_* 일치(VIDEO_BATCH_COMPUTE_ENV_NAME 포함), Batch 큐/JobDef/CE 존재 확인.

---

## 8. Verification results

### API

- **확인됨:** Ensure API 단계에서 health 200, SSM online. Sync 후 Refresh로 1대 API에서 SSM 재적용 및 컨테이너 재시작 완료.  
- **확인됨:** verify-video-batch-connection.ps1에서 SSM `/academy/api/env`의 VIDEO_BATCH_* 5개 키가 v1 이름과 일치.

### Video

- **확인됨:** Batch 큐 academy-v1-video-batch-queue, JobDef academy-v1-video-batch-jobdef, CE academy-v1-video-batch-ce 존재 및 ENABLED/VALID.  
- **확인됨:** API가 사용하는 SSM에 올바른 큐/JobDef/CE 이름이 반영되었고, 해당 API 인스턴스에 Refresh로 적용됨.  
- **미수행:** 실제 업로드 → upload_complete → submit_batch_job → job 목록 확인까지의 E2E 테스트는 이번 세션에서 실행하지 않음. 연결 참조와 배포 절차는 정리됨.

### Messaging

- **확인됨:** SQS academy-v1-messaging-queue 존재, Workers env에 SSOT 기준 SQS 이름 반영.  
- **미수행:** 메시지 enqueue → 워커 consume → 완료까지의 런타임 테스트는 미실행.

### AI

- **확인됨:** SQS academy-v1-ai-queue 존재, Workers env에 SSOT 반영.  
- **미수행:** AI job 생성 → 워커 fetch → 처리 완료까지의 런타임 테스트는 미실행.

### Redis

- **확인됨:** ElastiCache academy-v1-redis available, Primary Endpoint 확인됨. Sync 시 Get-RedisPrimaryEndpoint로 REDIS_HOST/REDIS_PORT가 API/Workers SSM에 주입됨.  
- **확인됨:** API Refresh 후 해당 인스턴스는 갱신된 SSM을 쓰므로 REDIS_HOST를 가짐.

---

## 9. Still unverified items

- **Video E2E:** 업로드 완료 → API에서 submit_batch_job 호출 → Batch 큐에 job 생성 → 워커 실행 → 인코딩 완료까지의 실제 요청/로그 기반 검증.  
- **Messaging E2E:** 메시지 1건 enqueue → Messaging Worker가 수신·처리하는지.  
- **AI E2E:** AI job 생성 → API 내부 엔드포인트 → AI Worker가 job 수신·처리하는지.  
- **Workers env 반영 시점:** Workers는 부팅 시에만 SSM을 읽음. Sync로 SSM이 갱신된 뒤 기동 중인 Messaging/AI 워커에는 **자동 Refresh 없음**. 새 인스턴스 또는 instance-refresh 시에만 새 env 적용. (필요 시 Workers용 refresh 스크립트 추가 가능.)

---

## 10. Remaining risks

- **Workers ASG:** SSM 갱신 후 기존 Messaging/AI 워커 인스턴스는 예전 env 유지. instance-refresh 또는 수동 재시작 전까지 SQS/Redis 변경이 반영되지 않음.  
- **수동 SSM 변경:** SSM만 수동으로 바꾼 뒤 `refresh-api-env.ps1`를 실행하지 않으면 API는 계속 구 env 사용. Runbook에 명시했음.  
- **Video E2E:** 연결 참조와 배포 절차는 맞췄으나, 실제 업로드·Batch job 생성·완료는 운영 환경에서 한 번 더 검증하는 것이 좋음.

---

## 11. Updated runbook / docs summary

- **docs/00-SSOT/v1/RUNBOOK-DEPLOY-AND-ENV.md**  
  - 정식 V1 배포는 `deploy.ps1` 한 번 실행(Sync + Refresh 포함).  
  - SSM만 수동 갱신한 경우 `refresh-api-env.ps1` 실행으로 API에 반영.  
  - 재배포 후 끊김 원인과 대응 표로 정리.  
  - 배포 후 검증은 `verify-video-batch-connection.ps1` 실행 권장.

- **docs/00-SSOT/v1/reports/API-VIDEO-BATCH-REDIS-CONNECTION-REFERENCE.md**  
  - §3: API Prod Env 소스에 deploy.ps1의 Sync+Refresh 자동 실행, REDIS 자동 발견, 재배포 후 끊김 원인 및 해결 추가.  
  - §7: 수정 사항을 deploy.ps1 + Sync + Refresh + Redis 자동 주입 기준으로 갱신.

이로써 “재배포 후 비디오 파이프라인이 진행되지 않는” 현상의 원인(SSM 갱신만 하고 기동 중 API에 미반영)을 수정하고, SSOT → 배포 스크립트 → 런타임 env가 일치하도록 정리했으며, 동일 이슈 재발 방지를 위한 Runbook과 문서를 보강함.
