# Video Batch — Human Action Plan & Deployment Checklist

**제약 유지:** 1 video = 1 AWS Batch Job = 1 EC2, minvCpus=0, persistent worker pool 없음, tenant isolation via prefix `tenants/{tenant_id}/...`.

---

## SECTION A — 필수 환경 변수

| Name | Used By (file:line) | Example | Required/Optional | Notes |
|------|---------------------|---------|-------------------|-------|
| AWS_REGION | base.py:37 | ap-northeast-2 | Optional (AWS_DEFAULT_REGION 대체) | |
| AWS_DEFAULT_REGION | base.py:38, validate_video_system.py:27, reconcile_batch_video_jobs.py:34, batch_submit.py:42, ops_events.py:67, validate_video_network_requirements.py:14 | ap-northeast-2 | Required (Batch/검증) | |
| VIDEO_BATCH_JOB_QUEUE | base.py:351, batch_submit.py:34,43, validate_video_system.py:28, reconcile_batch_video_jobs.py:36 | academy-video-batch-queue | Optional (기본값 동일) | |
| VIDEO_BATCH_JOB_DEFINITION | base.py:352, batch_submit.py:36,44 | academy-video-batch-jobdef | Optional (기본값 동일) | |
| VIDEO_TENANT_MAX_CONCURRENT | base.py:353, video_encoding.py:53 | 2 | Optional | |
| VIDEO_GLOBAL_MAX_CONCURRENT | base.py:354, video_encoding.py:54 | 20 | Optional | |
| VIDEO_MAX_JOBS_PER_VIDEO | base.py:355, video_encoding.py:55 | 10 | Optional | |
| VIDEO_CLOUDWATCH_NAMESPACE | base.py:356, ops_events.py:68 | Academy/Video | Optional | |
| VIDEO_BATCH_COMPUTE_ENV_NAME | validate_video_network_requirements.py:15 | academy-video-batch-ce | Optional | CE 이름 검증용 |
| LAMBDA_INTERNAL_API_KEY | base.py:44, permissions.py:49,52 | (secret) | Required (internal API 사용 시) | X-Internal-Key 헤더; 미설정 시 internal API 전부 403 |
| INTERNAL_API_ALLOW_IPS | base.py:46, permissions.py:54 | 10.1.0.0/16,172.30.0.0/16 | Optional | 비어 있으면 IP 검사 생략 |
| INTERNAL_WORKER_TOKEN | base.py:367, prod.py:140, config.py:88 | (secret) | Required (Batch 워커) | config.load_config()에서 _require |
| R2_ACCESS_KEY | base.py:317, config.py:114 | — | Required (API·워커) | |
| R2_SECRET_KEY | base.py:318, config.py:115 | — | Required (API·워커) | |
| R2_ENDPOINT | base.py:319, config.py:113 | — | Required (API·워커) | |
| R2_PUBLIC_BASE_URL | base.py:320 | — | Optional | |
| R2_VIDEO_BUCKET | base.py:322, config.py:110 | academy-video | Optional (기본값) | |
| R2_STORAGE_BUCKET | base.py:323 | academy-storage | Optional | |
| REDIS_HOST | libs/redis/client.py:33 | — | Optional (미설정 시 Redis 비활성) | 워커: RedisProgressAdapter, cache_video_status 사용 |
| REDIS_PORT | libs/redis/client.py:46 | 6379 | Optional | |
| REDIS_PASSWORD | libs/redis/client.py:41 | — | Optional | |
| REDIS_DB | libs/redis/client.py:48 | 0 | Optional | |
| DB_NAME | base.py:191 | — | Required (API·워커) | |
| DB_USER | base.py:192 | — | Required | |
| DB_PASSWORD | base.py:193 | — | Required | |
| DB_HOST | base.py:194 | — | Required | |
| DB_PORT | base.py:195 | 5432 | Optional | |
| VIDEO_JOB_ID | batch_main.py:96, batch_submit.py:49 | (runtime) | Required (Batch 런타임) | submit_batch_job containerOverrides로 주입 |
| VIDEO_PROGRESS_TTL_SECONDS | batch_main.py:41 | 14400 | Optional | |
| VIDEO_JOB_MAX_ATTEMPTS | batch_main.py:42 | 5 | Optional | |
| VIDEO_JOB_HEARTBEAT_SECONDS | batch_main.py:43 | 60 | Optional | |
| VIDEO_MIN_DURATION_SECONDS | video_views.py:439 | 3 | Optional | getattr default 3 |
| VIDEO_SQS_QUEUE_DELETE_R2 | base.py:348 | academy-video-delete-r2 | Optional | |
| SECRET_KEY | base.py:30 | — | Required (API) | |

**Batch 워커 전용 (config.load_config):** API_BASE_URL (config.py:86), INTERNAL_WORKER_TOKEN (config.py:88), R2_BUCKET 또는 R2_VIDEO_BUCKET, R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY 등. Job Definition JSON에는 `environment`/`secrets` 없음. **MISSING FROM REPOSITORY:** Job Definition에 DB/R2/Redis/API_BASE_URL/INTERNAL_WORKER_TOKEN 등 설정 방법이 코드에 없음. 배치 작업은 containerOverrides로 VIDEO_JOB_ID만 주입(batch_submit.py:47–50). DB·R2·Redis·API_BASE_URL·INTERNAL_WORKER_TOKEN은 (1) Job Definition의 environment/secrets(콘솔 또는 IaC) 또는 (2) SSM Parameter `/academy/workers/env`(batch_entrypoint 사용 시)로 MANUAL 설정 필요.

---

## SECTION B — AWS 리소스 프로비저닝 (순서)

### 1) ECR 리포지토리 + 이미지 푸시

| 항목 | 내용 |
|------|------|
| **Resource Name** | `academy-video-worker` (이미지명: job definition에서 PLACEHOLDER_ECR_URI로 치환) |
| **생성 방법** | **MANUAL STEP** 또는 기존 배포 스크립트 활용. 리포지토리 생성: repo에 ECR create 스크립트 없음. 이미지 푸시: `scripts/full_redeploy.ps1`(빌드+ECR 푸시), `deploy.ps1`(로컬 빌드+푸시), `scripts/build_and_push_ecr_remote.ps1`(원격 빌드+푸시)에 academy-video-worker 푸시 포함. |
| **필요 입력** | AWS 인증, Region, ECR 레지스트리 URL. full_redeploy: AccountId, Region, (선택) GitRepoUrl, SkipBuild. |
| **산출물** | `{AccountId}.dkr.ecr.{Region}.amazonaws.com/academy-video-worker:latest` 형태 URI |
| **검증** | `aws ecr describe-images --repository-name academy-video-worker --region <region> --query "imageDetails[0].imageTags"` |

### 2) IAM 역할

| 역할명 | 생성 방법 | 필요 입력 | 산출물 | 검증 |
|--------|-----------|-----------|--------|------|
| Batch service role | **SCRIPTED STEP** `scripts/infra/batch_video_setup.ps1` (섹션 [2]). trust: `scripts/infra/iam/trust_batch_service.json`, inline: `policy_batch_service_role.json` (존재 시). | Region, VpcId, SubnetIds, SecurityGroupId, EcrRepoUri (필수 5개) | academy-batch-service-role ARN | `aws iam get-role --role-name academy-batch-service-role` |
| EC2 instance profile role | **SCRIPTED STEP** 동일 스크립트. trust: `trust_ec2.json`, AWS 관리형 AmazonEC2ContainerServiceforEC2Role. | 위와 동일 | academy-batch-ecs-instance-role, academy-batch-ecs-instance-profile | `aws iam get-instance-profile --instance-profile-name academy-batch-ecs-instance-profile` |
| ECS task execution role | **SCRIPTED STEP** 동일. trust: `trust_ecs_tasks.json`, AWS 관리형 AmazonECSTaskExecutionRolePolicy, inline: `policy_ecs_task_execution_role.json` (존재 시). | 위와 동일 | academy-batch-ecs-task-execution-role ARN | `aws iam get-role --role-name academy-batch-ecs-task-execution-role` |
| Job role | **SCRIPTED STEP** 동일. trust: `trust_ecs_tasks.json`, inline: `policy_video_job_role.json`. | 위와 동일 | academy-video-batch-job-role ARN | `aws iam get-role --role-name academy-video-batch-job-role` |

### 3) CloudWatch Log Group (Batch 워커)

| 항목 | 내용 |
|------|------|
| **Resource Name** | `/aws/batch/academy-video-worker` (video_job_definition.json logConfiguration) |
| **생성 방법** | **SCRIPTED STEP** `scripts/infra/batch_video_setup.ps1` [1] 단계. Log group 없으면 create. |
| **필요 입력** | Region (스크립트 인자). 기본 LogsGroup 파라미터: `/aws/batch/academy-video-worker`. |
| **산출물** | Log group 생성됨. |
| **검증** | `aws logs describe-log-groups --log-group-name-prefix "/aws/batch/academy-video-worker" --region <region>` |

### 4) AWS Batch: Compute Environment, Job Queue, Job Definition

| 항목 | 내용 |
|------|------|
| **Compute environment** | **SCRIPTED STEP** `scripts/infra/batch_video_setup.ps1` [3]. JSON: `scripts/infra/batch/video_compute_env.json`. minvCpus=0, maxvCpus=32, type EC2, allocationStrategy BEST_FIT_PROGRESSIVE. PLACEHOLDER 치환: SERVICE_ROLE_ARN, INSTANCE_PROFILE_ARN, SECURITY_GROUP_ID, 서브넷 목록, MaxVcpus. |
| **필요 입력** | Region, VpcId, SubnetIds, SecurityGroupId, EcrRepoUri (필수). 선택: ComputeEnvName(기본 academy-video-batch-ce), JobQueueName, JobDefName, LogsGroup, MaxVcpus. |
| **산출물** | Compute environment VALID 상태, job queue, job definition 등록. |
| **검증** | `aws batch describe-compute-environments --compute-environments academy-video-batch-ce --region <region>`, `aws batch describe-job-queues --job-queues academy-video-batch-queue --region <region>`, `aws batch describe-job-definitions --job-definition-name academy-video-batch-jobdef --status ACTIVE --region <region>`. |

**Job Queue JSON 불일치:** `scripts/infra/batch/video_job_queue.json`의 `computeEnvironmentOrder[0].computeEnvironment` 값은 `academy-video-batch-ce-v3`. `batch_video_setup.ps1` 기본 ComputeEnvName은 `academy-video-batch-ce`. 스크립트는 queue JSON 내 CE 이름을 치환하지 않음. 따라서 (1) queue JSON을 수동으로 `academy-video-batch-ce`로 수정하거나, (2) 스크립트에 `-ComputeEnvName academy-video-batch-ce-v3`를 넘겨 CE 이름을 맞춰야 함. 그렇지 않으면 create-job-queue 시 CE를 찾지 못함.

### 5) EventBridge 스케줄

| 리소스 | 생성 방법 | 필요 입력 | 산출물 | 검증 |
|--------|-----------|-----------|--------|------|
| Reconcile (rate 2 min) | **SCRIPTED STEP** `scripts/infra/eventbridge_deploy_video_scheduler.ps1`. EventBridge role + put-rule + put-targets (Batch SubmitJob). | Region, JobQueueName(기본 academy-video-batch-queue). | rule: academy-reconcile-video-jobs, target: Batch job queue, JobDefinition academy-video-ops-reconcile | `aws events describe-rule --name academy-reconcile-video-jobs --region <region>`, `aws events list-targets-by-rule --rule academy-reconcile-video-jobs --region <region>` |
| Scan-stuck (rate 2 min) | **SCRIPTED STEP** 동일 스크립트. Batch target JobDefinition academy-video-ops-scanstuck. | 위와 동일 | rule: academy-video-scan-stuck-rate | `aws events describe-rule --name academy-video-scan-stuck-rate --region <region>` |

### 6) 네트워킹 사전 조건

| 항목 | 내용 |
|------|------|
| **VPC, 서브넷, 라우트 테이블** | **MANUAL STEP**. batch_video_setup.ps1은 기존 VpcId, SubnetIds, SecurityGroupId를 인자로 받음. 리포지토리에 VPC/서브넷 생성 스크립트 없음. |
| **NAT/IGW 또는 VPC 엔드포인트** | **MANUAL STEP**. `validate_video_network_requirements`는 CE 서브넷의 라우트 테이블에서 IGW 라우트 존재 여부만 확인. 사설 서브넷이면 "DEPENDS ON MANUAL AWS CONSOLE CONFIG" 출력하며, 필요 시 NAT 또는 VPC 엔드포인트(ecr.api, ecr.dkr, logs, s3) 안내. 실제 NAT/엔드포인트 생성 스크립트 없음. |
| **검증** | `python manage.py validate_video_network_requirements` (Django 앱 루트, AWS 자격 증명·설정 필요). |

### 7) 시크릿 저장소 (SSM/Secrets Manager)

| 항목 | 내용 |
|------|------|
| **SSM Parameter** | **MANUAL STEP**. `apps/worker/video_worker/batch_entrypoint.py`는 SSM `/academy/workers/env`에서 env 로드. Job Definition의 command는 `batch_main` 직접 실행(Ref::job_id); Dockerfile ENTRYPOINT는 batch_entrypoint. 즉 컨테이너는 batch_entrypoint → SSM fetch → exec batch_main. Job role 정책 `scripts/infra/iam/policy_video_job_role.json`에 ssm:GetParameter (arn:aws:ssm:*:*:parameter/academy/*) 포함. SSM 파라미터 생성/값 설정 스크립트는 repo에 없음. |
| **Secrets Manager** | 리포지토리 코드에서 참조 없음. |

### 8) CloudWatch 알람 (선택)

| 항목 | 내용 |
|------|------|
| **정의** | **SCRIPTED STEP** `scripts/infra/cloudwatch_deploy_video_alarms.ps1`. JSON: `scripts/infra/cloudwatch/alarm_video_dead_jobs.json`, `alarm_video_upload_failures.json`, `alarm_video_failed_jobs.json` + AWS/Batch Failed, RUNNABLE. |
| **필요 입력** | Region, JobQueueName(기본 academy-video-batch-queue). 선택: SnsTopicArn. |
| **산출물** | 알람 5개 생성. |
| **검증** | `aws cloudwatch describe-alarms --alarm-names academy-video-DeadJobs academy-video-UploadFailures academy-video-FailedJobs academy-video-BatchJobFailures academy-video-QueueRunnable --region <region>` |

---

## SECTION C — DB 마이그레이션 및 백필

- **적용할 마이그레이션:** `apps/support/video/migrations/` — 0001_initial ~ 0007_videoopsevent. 0006_unique_video_active_job: UniqueConstraint 조건 `state__in=["QUEUED","RUNNING","RETRY_WAIT"]`, fields=("video",), name=unique_video_active_job.
- **적용 명령:** `python manage.py migrate` (Django 앱 루트, DB 설정 필요).
- **백필/정리:** 0006 적용 전에 동일 비디오에 대해 QUEUED/RUNNING/RETRY_WAIT 상태가 2건 이상 있으면 마이그레이션 시 제약 위반 가능. 리포지토리에 해당 위반 행을 정리하는 쿼리/커맨드는 없음. **NOT IMPLEMENTED.** 위험: 기존에 중복 active job이 있으면 migrate 실패; 수동으로 1 video당 1개만 남기고 나머지는 state 변경 또는 삭제 후 migrate 필요.

---

## SECTION D — 원타임 프로덕션 부트스트랩 시퀀스 (0 → 첫 VIDEO READY)

1. **환경 변수 확정**  
   - API 서버: AWS_DEFAULT_REGION, VIDEO_BATCH_*, LAMBDA_INTERNAL_API_KEY, DB_*, R2_*, REDIS_*, SECRET_KEY 등 (Section A 표 참고).
   - Batch 워커용: Job Definition environment 또는 SSM `/academy/workers/env`에 API_BASE_URL, INTERNAL_WORKER_TOKEN, DB_*, R2_*, (선택) REDIS_* 등 설정.  
   **검증:** API 기동 후 `python manage.py check` 등.

2. **DB 마이그레이션**  
   - `python manage.py migrate`  
   **검증:** `python manage.py showmigrations video` 에서 [X] 표시.

3. **네트워크 준비 (MANUAL)**  
   - VPC, 서브넷, 보안 그룹, 필요 시 NAT 또는 VPC 엔드포인트(ECR, logs, S3).  
   **검증:** (선택) `python manage.py validate_video_network_requirements`.

4. **ECR 이미지**  
   - ECR 리포지토리 생성(수동 또는 콘솔).  
   - 이미지 빌드 및 푸시: `deploy.ps1` 또는 `scripts/full_redeploy.ps1` 또는 `scripts/build_and_push_ecr_remote.ps1` (VideoWorker 포함).  
   **검증:** `aws ecr describe-images --repository-name academy-video-worker --region <region>`.

5. **Job Queue JSON 정렬**  
   - `scripts/infra/batch/video_job_queue.json`의 computeEnvironment 값을 사용할 CE 이름과 일치시킴(예: academy-video-batch-ce). 또는 batch_video_setup.ps1에 `-ComputeEnvName`으로 동일 이름 지정.

6. **Batch 인프라**  
   - **SCRIPTED STEP:**  
     `.\scripts\infra\batch_video_setup.ps1 -Region ap-northeast-2 -VpcId <vpc-id> -SubnetIds @("subnet-1","subnet-2") -SecurityGroupId <sg-id> -EcrRepoUri <account>.dkr.ecr.<region>.amazonaws.com/academy-video-worker:latest`  
   **검증:** 스크립트 끝단의 describe-compute-environments, describe-job-queues, describe-job-definitions 출력. (선택) `python manage.py validate_video_system`.

7. **워커 환경 주입 (MANUAL)**  
   - Job Definition의 environment 또는 SSM Parameter `/academy/workers/env`에 DB_*, R2_*, API_BASE_URL, INTERNAL_WORKER_TOKEN 등 설정. (Repo에 이 단계 자동화 없음.)

8. **EventBridge Reconcile**  
   - **SCRIPTED STEP:**  
     `.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region ap-northeast-2 -ApiBaseUrl "https://<api-host>" -InternalApiKey "<key>"`  
   `.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue`  
   **검증:** `aws events describe-rule --name academy-reconcile-video-jobs --region <region>`.

9. **(선택) CloudWatch 알람**  
   - **SCRIPTED STEP:**  
     `.\scripts\infra\cloudwatch_deploy_video_alarms.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue`  
   **검증:** CloudWatch 콘솔에서 알람 존재 확인.

10. **첫 비디오 업로드 → READY**  
    - API로 비디오 업로드 및 인코딩 트리거.  
    **검증:** DB에서 Video.status=READY, hls_path 채워짐; VideoTranscodeJob 상태 SUCCEEDED; R2에 tenants/{tenant_id}/... prefix로 HLS 객체 존재; Batch job SUCCEEDED; CloudWatch Logs `/aws/batch/academy-video-worker`에 해당 job 로그 스트림 존재.

---

## SECTION E — 스모크 테스트 플랜 (코드 변경 없음)

| # | 전제 조건 | 액션 | DB 기대 결과 | R2 prefix 기대 결과 | Batch/CloudWatch 기대 결과 |
|---|-----------|------|--------------|----------------------|----------------------------|
| 1 | API 기동, Batch·Queue·Job Def 준비됨, DB 마이그레이션 완료 | 1건 비디오 업로드 후 인코딩 요청 | Video.status=PROCESSING → READY, Video.hls_path 설정됨. VideoTranscodeJob 1건: QUEUED → RUNNING → SUCCEEDED. | tenants/{tenant_id}/media/hls/videos/... 아래 .m3u8, .ts 등 객체 생성 | Batch list_jobs/describe_jobs에 해당 job 1건 SUCCEEDED. /aws/batch/academy-video-worker 로그 그룹에 batch/default/... 스트림에 BATCH_JOB_COMPLETED 등 로그 |
| 2 | 동일 | validate_video_system 실행 | 출력: "validate_video_system: OK". RUNNING without heartbeat, PROCESSING without active job, READY without hls_path, Duplicate active jobs, Orphan AWS job 없음 | — | — |
| 3 | 동일 | validate_video_iam_expectations 실행 | stdout에 batch-service, ecs-execution, batch-job, api 역할별 required actions 목록 출력 (AWS 호출 없음) | — | — |
| 4 | 동일 | validate_video_network_requirements 실행 | Batch CE 서브넷 존재, 필요 시 DEPENDS ON MANUAL... 메시지로 NAT/엔드포인트 안내 | — | — |
| 5 | Internal API 키 설정됨 | POST /api/v1/internal/video/reconcile/ with X-Internal-Key, body {"dry_run":true} | 응답 200, body.ok=true. DB 변경 없음(dry_run) | — | — |

---

## SECTION F — 운영 체크리스트 (Day-2)

- **검증 명령**  
  - `python manage.py validate_video_system` — Batch 큐·Job Def·DB/Redis 일관성 (apps/support/video/management/commands/validate_video_system.py).  
  - `python manage.py validate_video_network_requirements` — CE 서브넷, 사설 시 NAT/VPC 엔드포인트 안내 (validate_video_network_requirements.py).  
  - `python manage.py validate_video_iam_expectations` — 역할별 필요 AWS 액션 목록 정적 출력 (validate_video_iam_expectations.py).  
  - `python manage.py validate_video_architecture_mode` — Lambda 미사용 검증 (validate_video_architecture_mode.py). Lambda 아티팩트 있으면 exit 1.

- **OpsEvents 조회**  
  - DB 모델: `apps.support.video.models.VideoOpsEvent`. 테이블명: `video_videoopsevent`. 필드: type, severity, tenant_id, video_id, job_id, aws_batch_job_id, payload, created_at. type: JOB_DEAD, BATCH_DESYNC, UPLOAD_INTEGRITY_FAIL, ORPHAN_CANCELLED, TENANT_LIMIT_EXCEEDED 등.

- **특정 Job의 CloudWatch 로그 스트림**  
  - Log group: `/aws/batch/academy-video-worker` (video_job_definition.json logConfiguration). 스트림 prefix: `batch`. Batch가 생성하는 스트림 이름 형식: `batch/default/<job-id>/<container-id>` 등.  
  - Job ID로 찾기: `aws batch describe-jobs --jobs <aws-job-id> --region <region>` 로 로그 스트림 정보 확인 후, `aws logs get-log-events --log-group-name /aws/batch/academy-video-worker --log-stream-name <stream-name> --region <region>`.

- **스케줄러 중단 시 수동 Reconcile**  
  - 방법 1: `python manage.py reconcile_batch_video_jobs [--dry-run] [--older-than-minutes 5] [--resubmit]` (reconcile_batch_video_jobs.py).  
  - 방법 2: API 호출. POST `/api/v1/internal/video/reconcile/`, Header `X-Internal-Key: <LAMBDA_INTERNAL_API_KEY>`, Body `{"dry_run": false, "older_than_minutes": 5, "resubmit": false}` (internal_views.py VideoReconcileView).

- **Runaway Batch Job 종료**  
  - DB에서 해당 VideoTranscodeJob의 aws_batch_job_id 확인 후:  
    `aws batch terminate-job --job-id <aws_batch_job_id> --reason "operator-terminate" --region <region>`  
  - 또는 앱에서: `from apps.support.video.services.batch_submit import terminate_batch_job`; `terminate_batch_job(video_job_id_str, reason="operator-terminate")` (batch_submit.py terminate_batch_job). DB의 job 상태 정리(DEAD 등)는 별도 처리 필요.

---

## SECTION G — 수동 의존성 / 블로커

- **Job Queue CE 이름 불일치:** `scripts/infra/batch/video_job_queue.json`의 computeEnvironment는 `academy-video-batch-ce-v3`, batch_video_setup.ps1 기본 CE 이름은 `academy-video-batch-ce`. 스크립트가 queue JSON을 치환하지 않아, 그대로 사용 시 create-job-queue 실패 또는 잘못된 CE 참조. 영향: Batch 큐 생성 실패 또는 다른 CE 사용.

- **VPC/서브넷/보안 그룹:** batch_video_setup.ps1은 기존 VpcId, SubnetIds, SecurityGroupId를 받음. 이 리소스를 만드는 스크립트는 repo에 없음. 영향: Batch CE 생성 전에 MANUAL로 네트워크 준비 필요.

- **ECR 리포지토리 생성:** ECR repo를 생성하는 스크립트 없음. full_redeploy/deploy 등은 기존 repo에 push만 함. 영향: 최초 1회 수동 또는 콘솔로 repository 생성 필요.

- **Batch 워커 환경 변수:** Job Definition JSON(scripts/infra/batch/video_job_definition.json)에는 `environment`/`secrets`가 없고, batch_submit은 containerOverrides로 VIDEO_JOB_ID만 설정. DB, R2, Redis, API_BASE_URL, INTERNAL_WORKER_TOKEN 등은 Job Definition의 env 또는 SSM `/academy/workers/env`로 MANUAL 설정 필요. 영향: 워커 기동 실패 또는 설정 오류.

- **INTERNAL_API_ALLOW_IPS:** IsLambdaInternal에서 사용. 비어 있으면 IP 검사 생략; 채우면 Lambda/API VPC CIDR 등 수동 설정. 리포지토리에 해당 값을 설정하는 배포 단계 없음. 영향: 내부 API 접근 제어 정책은 운영자가 수동 설정.

- **validate_video_system --fix:** reconcile_batch_video_jobs.py docstring 및 validate_video_system에 --fix 옵션 존재하나, validate_video_system의 handle()에서 fix 로직 미구현(POST_REFACTOR_PRODUCTION_READINESS_VERIFICATION_REPORT.md). 영향: stale RUNNING 등 자동 수정 없음, 수동 또는 reconcile로 처리.
