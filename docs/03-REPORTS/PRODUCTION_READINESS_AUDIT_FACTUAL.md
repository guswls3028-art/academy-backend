# FULL PRODUCTION READINESS AUDIT — ZERO ASSUMPTION MODE

**Rules:** No assumptions. No general advice. No best practices. No redesign. No intent speculation. Every statement references actual code or infra in this repository. If not implemented → NOT IMPLEMENTED. If not found → NOT FOUND IN REPOSITORY. If depends on external console → DEPENDS ON MANUAL AWS CONSOLE CONFIG. If undefined → UNDEFINED BEHAVIOR. File:line where applicable.

---

## SECTION 1 — Code-Level Architecture (FACTUAL)

### Job creation flow

- **Entry:** `apps/support/video/views/video_views.py` L430, L452, L473: upload-complete calls `create_job_and_submit_batch(video)`. L546: retry API calls `create_job_and_submit_batch(video)` after optional current_job cleanup.
- **Implementation:** `apps/support/video/services/video_encoding.py` L18–59: `create_job_and_submit_batch(video)` requires `video.status == Video.Status.UPLOADED` (L26); gets `tenant_id` from `video.session.lecture.tenant` (L32–36); `VideoTranscodeJob.objects.create(video=video, tenant_id=tenant_id, state=QUEUED)` (L39–42); `video.current_job_id = job.id` and save (L44–45); `submit_batch_job(str(job.id))` (L46); on success `job.aws_batch_job_id = aws_job_id` and save (L49–50).

### State transitions (all possible)

- **QUEUED → RUNNING:** `academy/adapters/db/django/repositories_video.py` L639–649: `job_set_running` filters `pk=job_id`, `state__in=[QUEUED, RETRY_WAIT]`, update to RUNNING, locked_by, locked_until, last_heartbeat_at. Called from `apps/worker/video_worker/batch_main.py` L122; from `apps/support/video/management/commands/reconcile_batch_video_jobs.py` L142–144 when Batch RUNNING and DB QUEUED.
- **RUNNING → SUCCEEDED (+ Video READY):** `repositories_video.py` L676–726: `job_complete` in `transaction.atomic()`, select_for_update, set video.hls_path, duration, status=READY, job.state=SUCCEEDED. Called from batch_main.py L158.
- **RUNNING/QUEUED/RETRY_WAIT → RETRY_WAIT:** `repositories_video.py` L728–745: `job_fail_retry` in `transaction.atomic()`, state=RETRY_WAIT, attempt_count F+1, error_message. Called from batch_main L187, L195, L56 (_handle_term); reconcile L124, L147, L150.
- **RUNNING → DEAD (batch_main):** batch_main.py L196–199: after job_fail_retry, if attempt_count >= VIDEO_JOB_MAX_ATTEMPTS (5), calls `job_mark_dead`. `repositories_video.py` L781–806: job DEAD, Video.objects.filter(current_job_id=job_id).update(status=FAILED).
- **RUNNING → DEAD (scan_stuck mgmt command):** `apps/support/video/management/commands/scan_stuck_video_jobs.py` L59–65: direct job.state=DEAD, save. **Does not call job_mark_dead;** Video not updated.
- **RUNNING → DEAD (internal scan-stuck API):** `apps/support/video/views/internal_views.py` L241–246: calls `job_mark_dead(...)`. Video is updated to FAILED.
- **RETRY_WAIT → RUNNING:** Same as QUEUED → RUNNING (job_set_running accepts RETRY_WAIT). New Batch job is submitted by scan_stuck (L79) or reconcile with --resubmit.

### Video.status update logic

- **READY:** Only in `repositories_video.py` `job_complete` L706: `video.status = Video.Status.READY` inside same transaction.
- **FAILED:** Only in `repositories_video.py` `job_mark_dead` L802–805: `Video.objects.filter(current_job_id=job_id).update(status=Video.Status.FAILED, ...)`. Invoked from batch_main L197 when attempt_count >= 5; from internal_views VideoScanStuckView L242 when marking DEAD. **Not** invoked when scan_stuck_video_jobs (mgmt command) sets job to DEAD.
- **UPLOADED:** `apps/support/video/views/video_views.py` L423, L443, L464 (upload complete), L543 (retry path). Batch worker does not set Video.status to PROCESSING; only Redis cache is set to "PROCESSING" (batch_main.py L131).

### Retry logic (all sources)

- **job_fail_retry (app):** Job → RETRY_WAIT, attempt_count+1. No auto-resubmit from batch_main; container exits 1.
- **scan_stuck_video_jobs (mgmt):** `apps/support/video/management/commands/scan_stuck_video_jobs.py` L44–92: RUNNING and last_heartbeat_at < cutoff → RETRY_WAIT + attempt_count+1 + `submit_batch_job(str(job.id))` and save aws_batch_job_id; or DEAD if attempt_after >= 5 (Video not updated).
- **internal scan-stuck API:** `internal_views.py` L229–254: same filter; DEAD path calls `job_mark_dead` (Video updated); RETRY_WAIT path does **not** call submit_batch_job.
- **reconcile_batch_video_jobs:** L129–137, L146–155: on Batch FAILED or not found, job_fail_retry; with --resubmit calls submit_batch_job and updates aws_batch_job_id.

### Heartbeat implementation

- **Loop:** `apps/worker/video_worker/batch_main.py` L64–74 `_heartbeat_loop(job_id)`: daemon thread, every `VIDEO_JOB_HEARTBEAT_SECONDS` (env default 60, L42) calls `job_heartbeat(job_id, lease_seconds=VIDEO_JOB_HEARTBEAT_SECONDS*2)`. Started L154; stopped in finally L203.
- **DB:** `repositories_video.py` L658–673 `job_heartbeat`: filter pk=job_id, state=RUNNING; update last_heartbeat_at, locked_until=now+timedelta(seconds=lease_seconds).

### Timeout behavior (Batch + ffmpeg)

- **Batch:** `scripts/infra/batch/video_job_definition.json`: `"timeout":{"attemptDurationSeconds":14400}`. Batch terminates job after 4 hours.
- **ffmpeg:** `apps/worker/video_worker/video/transcoder.py` L194–201: FFMPEG_TIMEOUT_MIN_SECONDS=3600, FFMPEG_TIMEOUT_MAX_SECONDS=21600, FFMPEG_TIMEOUT_DURATION_MULTIPLIER=2.0, FFMPEG_CHUNK_SECONDS=300, FFMPEG_EXTEND_SECONDS=300, FFMPEG_MAX_TOTAL_SECONDS=86400. L204–212 `_effective_ffmpeg_timeout`: from duration capped 1h–6h; else config. L307–328 (and L364–377): `p.wait(timeout=chunk)`; on TimeoutExpired, deadline += 300, capped at 24h; repeat until process exits or deadline exceeded.

### SIGTERM handling

- **Registration:** batch_main.py L99–102: `signal.signal(signal.SIGTERM, _handle_term)`, `signal.signal(signal.SIGINT, _handle_term)`.
- **Handler:** L51–61 `_handle_term`: _shutdown_event.set(), `job_fail_retry(_current_job_id[0], "TERMINATED")`, sys.exit(1).

### Idempotency logic

- **batch_main:** L112–115: if job.state == SUCCEEDED, return 0. L116–121: if video.status == READY and video.hls_path, call job_complete then return 0 (process_video skipped).
- **job_complete:** repositories_video.py L687–692: if job.state == SUCCEEDED and video READY and hls_path, return True,"idempotent".
- **No idempotency in create_job_and_submit_batch:** New job always created; video.current_job_id replaced. batch_main does **not** use Redis job lock (no acquire_lock/release_lock in batch_main.py).

### Duplicate submission handling

- **Retry API:** `video_views.py` L515–537: if current_job exists and state in (QUEUED, RETRY_WAIT), either reject "Already in backlog" (if recent and has aws_batch_job_id), or mark current job DEAD and clear current_job_id then allow create_job_and_submit_batch. If current_job RUNNING, only `job_set_cancel_requested(cur.id)` then create_job_and_submit_batch is called (L536–546): **new job created, current_job_id replaced; previous Batch job orphaned.**
- **No DB constraint** preventing multiple RUNNING jobs per video. No uniqueness on (video_id, state).

### DB schema (Video + Job)

- **Video:** `apps/support/video/models.py` L33–146: id, session_id, folder_id, title, file_key, duration, order, thumbnail, status (CharField, choices PENDING/UPLOADED/PROCESSING/READY/FAILED), error_reason, hls_path, processing_started_at, leased_until, leased_by, current_job_id (FK to VideoTranscodeJob, SET_NULL, null=True). TimestampModel → created_at, updated_at. Indexes: (status, updated_at), (leased_until, status). **No UniqueConstraint on Video.**
- **VideoTranscodeJob:** L160–211: id (UUID PK), video_id (FK CASCADE), tenant_id (PositiveIntegerField), state (CharField, QUEUED/RUNNING/SUCCEEDED/FAILED/RETRY_WAIT/DEAD/CANCELLED), attempt_count, cancel_requested, locked_by, locked_until, last_heartbeat_at, error_code, error_message, aws_batch_job_id (CharField 256 blank), created_at (auto_now_add), updated_at (auto_now). Meta indexes: (state, updated_at), (tenant_id, state). **No UniqueConstraint on VideoTranscodeJob.**

### DB constraints

- **Video, VideoTranscodeJob:** No UniqueConstraint or CheckConstraint linking them. Other models in same file (VideoAccess, VideoProgress, VideoPlaybackSession, VideoFolder) have UniqueConstraints on their own fields (models.py L272–273, 310–311, 367–368, 472–473).

### Transactions and atomic guarantees

- **job_complete:** `repositories_video.py` L684: `with transaction.atomic():`; job and video updated in same transaction.
- **job_fail_retry:** L725: `with transaction.atomic():`; job only.
- **job_mark_dead:** L792: `with transaction.atomic():`; job and Video.update in same transaction.
- **job_set_running:** No transaction.atomic(); single update.
- **create_job_and_submit_batch:** No single transaction wrapping job create, video save, submit, and aws_batch_job_id save (video_encoding.py L39–50).

### Locking mechanism

- **DB:** `repositories_video.py` L685, L735, L790: `VideoTranscodeJob.objects.select_for_update()` in job_complete, job_fail_retry, job_mark_dead. L19: `get_video_for_update` uses select_for_update on Video.
- **In-memory:** `apps/worker/video_worker/current_transcode.py` L14, L18, L26, L34: `threading.Lock()` for _process/_job_id/_cancel_event (ffmpeg process reference). Not used for job claim.
- **Redis job lock:** `src/infrastructure/cache/redis_idempotency_adapter.py` L37, L48–98: key `job:{job_id}:lock`, acquire/release. **Not used by batch_main** (batch_main does not call acquire_lock or release_lock).

### Concurrency guard

- **job_set_running:** Only one row updated (filter by pk + state in [QUEUED, RETRY_WAIT]); so only one process can transition a given job to RUNNING. No guard limiting how many jobs run per tenant or globally in application code.

### Uniqueness guarantee

- **NOT IMPLEMENTED.** No unique constraint on (video_id, state=RUNNING) or single active job per video. Multiple RUNNING jobs for same video can exist in DB if retry creates new job while old is still RUNNING.

---

## SECTION 2 — Infrastructure as Code Completeness

| Item | Status | Evidence |
|------|--------|----------|
| Batch Compute Environment | Defined in repo, placeholders | `scripts/infra/batch/video_compute_env.json`: computeEnvironmentName=academy-video-batch-ce, type=MANAGED, computeResources.type=EC2, minvCpus=0, maxvCpus=32, desiredvCpus=0, instanceTypes=["c6g.large","c6g.xlarge","c6g.2xlarge"], allocationStrategy=BEST_FIT_PROGRESSIVE. serviceRole, subnets, securityGroupIds, instanceRole = PLACEHOLDER_* |
| Job Definition | Defined in repo, placeholders | `scripts/infra/batch/video_job_definition.json`: image=PLACEHOLDER_ECR_URI, jobRoleArn=PLACEHOLDER_JOB_ROLE_ARN, executionRoleArn=PLACEHOLDER_EXECUTION_ROLE_ARN, logConfiguration awslogs-group=/aws/batch/academy-video-worker, awslogs-region=PLACEHOLDER_REGION. timeout attemptDurationSeconds=14400, retryStrategy attempts=1, vcpus=2, memory=4096 |
| Job Queue | Defined in repo | `scripts/infra/batch/video_job_queue.json`: jobQueueName=academy-video-batch-queue, computeEnvironmentOrder academy-video-batch-ce-v3 (v3 not in same JSON; queue references v3) |
| IAM roles | Placeholder in JSON, replaced by script | video_job_definition.json PLACEHOLDER_JOB_ROLE_ARN, PLACEHOLDER_EXECUTION_ROLE_ARN. `scripts/infra/batch_video_verify_and_register.ps1` L65–76, `batch_video_setup.ps1` L118–119, L195–196: get role ARNs and replace. Role names in scripts: academy-video-batch-job-role, academy-batch-ecs-task-execution-role. |
| Instance profile | Placeholder in CE JSON | video_compute_env.json instanceRole=PLACEHOLDER_INSTANCE_PROFILE_ARN. Replaced in batch_video_setup.ps1. |
| ECR | Placeholder | video_job_definition.json image=PLACEHOLDER_ECR_URI. Replaced at register time by scripts. |
| Logging | Defined in Job Definition | logDriver=awslogs, awslogs-group=/aws/batch/academy-video-worker, awslogs-region=PLACEHOLDER_REGION, awslogs-stream-prefix=batch |
| VPC / Subnet / Security Groups | Placeholder in CE JSON | video_compute_env.json subnets=PLACEHOLDER_SUBNET_1, securityGroupIds=PLACEHOLDER_SECURITY_GROUP_ID. Replaced by script. DEPENDS ON MANUAL AWS CONSOLE CONFIG if not provided by script. |
| S3 / R2 | Not in Batch IaC; app config | R2_* in `apps/api/config/settings/base.py` L317–328, worker.py L114–120: R2_VIDEO_BUCKET, R2_ENDPOINT, etc. from env. No Terraform/CDK for R2 in repo. |
| Cron / Scheduler | NOT FOUND IN REPOSITORY | No cron, EventBridge rule, or Lambda definition in repo that runs scan_stuck_video_jobs or reconcile_batch_video_jobs. Docstrings: "Run via cron (e.g. every 2 min)" (scan_stuck), "Run via cron (e.g. every 1~2 min)" (reconcile). `infra/worker_asg/video_scan_stuck_lambda/` exists: Lambda calls internal API POST .../scan-stuck/ (does not resubmit). EventBridge trigger for that Lambda not defined in same lambda_function.py; DEPENDS ON MANUAL AWS CONSOLE CONFIG or separate deploy script. |
| Environment variables | Settings from env | base.py L37–38 AWS_REGION/AWS_DEFAULT_REGION; L317–328 R2_*; L351–352 VIDEO_BATCH_JOB_QUEUE, VIDEO_BATCH_JOB_DEFINITION. worker.py L102–103, L114–120. config.py (worker) reads many env vars. No .env.example or definitive list in repo for Batch worker. |

---

## SECTION 3 — Operational Automation Coverage

| Item | Status | Evidence |
|------|--------|----------|
| scan_stuck job | NOT IMPLEMENTED (scheduled) | Management command exists (`scan_stuck_video_jobs.py`). No cron/EventBridge/scheduler in repo. Lambda `video_scan_stuck_lambda` calls internal API; EventBridge rate(2 min) mentioned in comment (lambda_function.py L4); no rule definition in repo. |
| reconcile job | NOT IMPLEMENTED | Command exists (`reconcile_batch_video_jobs.py`). No schedule in repository. |
| cleanup jobs | NOT IMPLEMENTED | No management command or job that cleans up old DEAD jobs or R2 artifacts. |
| dead-letter handling | NOT IMPLEMENTED for Batch | No DLQ or handler for Batch job failures in code. SQS DLQ exists for other queues (docs reference academy-video-jobs-dlq for legacy SQS). |
| cost monitoring | NOT IMPLEMENTED | No cost metric, budget alarm, or cost allocation tag in repository. |
| health monitoring | Partial | validate_batch_video_system.py: DB, describe-jobs, CloudWatch logs, IAM. No continuous health check or heartbeat endpoint for Batch. |
| CloudWatch alarms | NOT FOUND IN REPOSITORY | No alarm definition (JSON/Terraform) for failed jobs, stuck jobs, or queue depth in repo. |
| metrics publishing | Partial (non-Batch) | `infra/worker_asg/queue_depth_lambda/lambda_function.py` L88–116: put_metric_data for SQS queue depth (Academy/VideoProcessing etc.). Video encoding path is Batch; queue_depth Lambda is for legacy SQS. No application metric for Batch job count or duration in repo. |
| autoscaling policies | NOT APPLICABLE to Batch | Batch CE scales by Batch service (minvCpus=0, maxvCpus=32). No TargetTracking or StepScaling in repo for Batch. Legacy ASG scripts exist (deploy_worker_asg.ps1) for non-Batch workers. |

---

## SECTION 4 — Storage & Data Integrity Guarantees

| Item | Evidence |
|------|----------|
| Where HLS output is written | Local: `src/infrastructure/video/processor.py` L121 `temp_workdir(cfg.TEMP_DIR, prefix=f"video-{video_id}-")`, L122 `out_dir = wd / "hls"`. R2 path: `apps/core/r2_paths.py` L25–26 `video_hls_prefix(tenant_id, video_id)` → `tenants/{tenant_id}/video/hls/{video_id}`; L30 `video_hls_master_path` → `tenants/{tenant_id}/video/hls/{video_id}/master.m3u8`. |
| How upload occurs | `apps/worker/video_worker/video/r2_uploader.py` L41–47: boto3 S3 client with endpoint_url (R2). L57–78: os.walk local_dir, key = prefix/rel, s3.upload_file with ContentType and CacheControl. |
| Retry behavior | r2_uploader.py L69–84: per-file loop, attempt += 1, retry_max=5, backoff_sleep on failure; raises UploadError after 5 failures. |
| Partial upload cleanup | NOT IMPLEMENTED. No delete of already-uploaded keys on upload failure. |
| Overwrite semantics | Same key (prefix + rel path) is overwritten by s3.upload_file. Retry uses same hls_prefix (tenant_id, video_id), so retry overwrites. |
| Success marker existence | NOT IMPLEMENTED. No dedicated success file in R2. Success = DB job_complete + hls_path. |
| Post-upload verification | NOT IMPLEMENTED. No read-back or checksum after upload. |
| Checksum validation | NOT IMPLEMENTED. |
| Atomic publish behavior | NOT IMPLEMENTED. Upload is file-by-file; if process dies mid-upload, partial keys remain. job_complete runs only after process_video returns (after full upload). |
| READY status guarantees full R2 consistency | No guarantee in code. job_complete sets READY after process_video returns; process_video does not verify R2 after upload. If upload_directory raised after some files, job would not be complete. If upload succeeded but R2 eventually lost objects, READY would still be set. |
| Old artifacts removed on retry | NOT IMPLEMENTED. Retry writes same prefix; new run overwrites same keys. No explicit delete of previous run's keys. |
| Two retries interleave writes | Possible. Two Batch jobs for same video (e.g. retry while first RUNNING) write to same R2 prefix; interleaved uploads can leave inconsistent segment set. No locking or versioning in code. |

---

## SECTION 5 — State Consistency Guarantees

| Question | Answer | Evidence |
|----------|--------|----------|
| Can Job.state and Video.status diverge? | Yes | scan_stuck_video_jobs (mgmt) sets job to DEAD without updating Video (no job_mark_dead). job_complete and job_mark_dead keep them in sync when they are used. |
| Protected by DB constraint? | No | No constraint linking Job.state to Video.status. |
| Single source of truth? | No | Job and Video updated in different code paths; reconcile and scan_stuck can change Job without always updating Video. |
| Can two RUNNING jobs exist for one video? | Yes | create_job_and_submit_batch creates new job and sets current_job_id; no DB uniqueness. Retry while RUNNING creates second job (video_views.py L536–546). |
| Can orphaned Batch jobs exist? | Yes | When retry creates new job, previous aws_batch_job_id is replaced; old Batch job continues until timeout/failure. No cancel or terminate of old job in code. |
| Can orphaned DB jobs exist? | Yes | Job can be DEAD/SUCCEEDED while video.current_job_id points to it; or current_job_id can point to old job after new job created. No cleanup of stale current_job_id in repo. |
| Can job be DEAD while video is READY? | Yes (edge) | If job was marked DEAD by scan_stuck (mgmt) after video was already set READY by a different run, job is DEAD and video READY. Normally DEAD is set when job failed; then job_mark_dead sets video FAILED. |
| Can job be SUCCEEDED while upload incomplete? | Theoretically no from batch_main | job_complete is called only after process_video returns (after upload_directory). If reconcile calls job_complete when Batch SUCCEEDED and video READY and hls_path (reconcile L114–121), it assumes output exists; if Batch reported SUCCEEDED but upload had failed before commit, DB could be SUCCEEDED with missing R2. Reconcile does not verify R2. |
| Can retry create duplicate AWS jobs? | Yes | scan_stuck and reconcile with --resubmit call submit_batch_job for same job.id; each call creates a new Batch job. aws_batch_job_id is overwritten; previous Batch job is orphaned. |
| Reconciliation without cron? | No | reconcile_batch_video_jobs is a one-shot command. No daemon or in-process loop. DEPENDS ON MANUAL SCHEDULER (cron/Lambda/EventBridge) to run it periodically. |

---

## SECTION 6 — Failure Scenario Exhaustive Analysis

| Scenario | Behavior (code only) |
|----------|----------------------|
| Container crash before job_set_running | Job stays QUEUED. last_heartbeat_at never set. scan_stuck selects only RUNNING + old heartbeat → not selected. reconcile can select it (QUEUED, aws_batch_job_id set, updated_at old); describe_jobs returns FAILED/not found → job_fail_retry, optional resubmit. If reconcile not run: UNDEFINED BEHAVIOR (stuck QUEUED). |
| Crash after job_set_running but before heartbeat | Job RUNNING, last_heartbeat_at set once at job_set_running. Next heartbeat 60s later never runs. After 3 min cutoff, scan_stuck selects it → RETRY_WAIT + resubmit or DEAD (Video not updated if DEAD via mgmt command). |
| Crash during upload | Exception in process_video; batch_main except L193, job_fail_retry. Job → RETRY_WAIT. No partial R2 cleanup. |
| EC2 termination without SIGTERM | No _handle_term. Process killed. Job remains RUNNING. scan_stuck after 3 min → RETRY_WAIT + resubmit or DEAD. If DEAD via mgmt: Video not updated. |
| OOM kill | Same as EC2 termination; no graceful SIGTERM. |
| Batch timeout at 4h | Batch terminates job. Container exits without job_fail_retry. Job stays RUNNING in DB. scan_stuck after 3 min → RETRY_WAIT + resubmit or DEAD. |
| ffmpeg runs 10h | transcoder.py wait loop extends deadline every 300s up to FFMPEG_MAX_TOTAL_SECONDS (86400). So ffmpeg can run up to 24h. Batch timeout 4h will kill container first; then behavior as "Batch timeout at 4h". |
| Network failure to R2 | upload_directory raises after retry_max (5) per-file attempts. job_fail_retry; RETRY_WAIT. |
| AWS throttling on submit_job | submit_batch_job returns (None, err_msg). video_encoding.py L52–58: job.state=FAILED, error_code/error_message saved, video.current_job_id=None. No retry of submit in code. |
| Duplicate API submission | Two upload-complete or retry calls: first creates job A, second creates job B, video.current_job_id=B. Two Batch jobs submitted. No idempotency key. |
| Retry spam via API | Retry API allows create_job_and_submit_batch if current_job is RUNNING (after set_cancel_requested). Each call creates new job and new submit. No rate limit or per-tenant cap. |
| Tenant uploads 500 videos simultaneously | 500 create_job_and_submit_batch → 500 jobs, 500 submit_job. All accepted. Batch CE maxvCpus=32 limits concurrency. No application-level backpressure. |
| scan_stuck not running for 24h | RUNNING jobs with no heartbeat stay RUNNING in DB. reconcile (if run) can update from Batch status. If neither run: jobs remain RUNNING indefinitely; Batch may have failed or succeeded. UNDEFINED BEHAVIOR for user-visible state. |
| reconcile not running for 24h | QUEUED/RUNNING jobs that Batch has failed or completed are not synced. Divergence persists until reconcile or manual fix. |
| AWS Batch reports SUCCEEDED but upload partially failed | Reconcile L114–121: if Batch SUCCEEDED and video READY and hls_path, calls job_complete. So reconcile assumes READY+hls_path means success. If Batch SUCCEEDED but container died before job_complete, video is not READY; reconcile L125–127 calls job_fail_retry "Reconcile: Batch SUCCEEDED, output missing". If Batch SUCCEEDED and container had called job_complete after partial upload (e.g. bug), READY would be set with incomplete R2. No code path verifies R2 after Batch SUCCEEDED. |
| Batch job not found in describe_jobs | reconcile L146–156: bj is None → job_fail_retry, optional resubmit. |

---

## SECTION 7 — Cost & Abuse Controls

| Item | Status | Evidence |
|------|--------|----------|
| Per-tenant concurrency limit | NOT IMPLEMENTED | No check by tenant_id before submit. |
| Per-tenant daily cap | NOT IMPLEMENTED | |
| Global job cap | Only Batch CE | maxvCpus=32 in video_compute_env.json. No app-level cap. |
| Queue depth check before submit | NOT IMPLEMENTED | |
| Rate limiting | NOT IMPLEMENTED | No rate limit on upload-complete or retry API in code. |
| Idempotency keys | Partial | batch_main idempotent for same job (SUCCEEDED/READY skip). No idempotency key on create_job_and_submit_batch. |
| Cancel protection | Partial | job_set_cancel_requested in retry path (video_views.py L537). No protection against runaway cancel. |
| Retry explosion guard | NOT IMPLEMENTED | attempt_count and job_mark_dead at 5 limit retries per job; new job can be created by retry API, so no per-video retry cap. |
| Instance size enforcement | In Job Definition | vcpus=2, memory=4096 in video_job_definition.json. |
| Memory limits defined | Yes | containerProperties.memory=4096 (MiB) in video_job_definition.json. |
| CPU limits defined | Yes | vcpus=2 in video_job_definition.json. |
| Storage limits defined | NOT IMPLEMENTED | No quota or limit on R2 prefix size or number of objects per tenant/video. |

---

## SECTION 8 — Security & Isolation

| Item | Status | Evidence |
|------|--------|----------|
| IAM least privilege enforcement | NOT FOUND IN REPOSITORY | IAM policy JSON for job role/execution role not in repo. Scripts reference role names; policies are DEPENDS ON MANUAL AWS CONSOLE CONFIG or external script. |
| R2 bucket isolation by tenant | By prefix only | r2_paths.py L25–26: prefix includes tenant_id. Single bucket (R2_VIDEO_BUCKET) for all tenants. No separate bucket per tenant in code. |
| Prefix validation | Implicit | Prefix built from tenant_id and video_id (int) from job dict (batch_main L139; processor L116–117). No path traversal in prefix; rel path from os.walk is relative to local_dir (r2_uploader L61–62). |
| Path traversal prevention | Partial | key = f"{prefix.rstrip('/')}/{rel.as_posix()}". rel is from Path.relative_to(local_dir). No ".." in rel from os.walk. If prefix or input were malicious, not validated. tenant_id and video_id come from DB (job_obj). |
| Environment variable secrets handling | From env | R2_ACCESS_KEY, R2_SECRET_KEY, INTERNAL_WORKER_TOKEN, etc. in settings. No secret manager reference in Batch worker config; SSM referenced in batch_entrypoint (docs/code). |
| Secret rotation mechanism | NOT FOUND IN REPOSITORY | |
| TLS enforcement | NOT FOUND IN REPOSITORY | R2 endpoint typically HTTPS; no explicit TLS check in code. |
| Public bucket exposure check | NOT FOUND IN REPOSITORY | R2_VIDEO_BUCKET and R2_PUBLIC_BASE_URL in settings; no code that checks bucket public access. |
| Per-tenant access enforcement | Application-level | Video/list and progress filtered by session__lecture__tenant (e.g. progress_views.py). Job submission and Batch worker use job_id; job_get_by_id has no tenant filter (repositories_video.py L626–629). Internal API uses X-Internal-Key; no tenant in scan-stuck payload. |

---

## SECTION 9 — Observability & Monitoring

| Item | Status | Evidence |
|------|--------|----------|
| Metrics emission | Partial (non-Batch) | queue_depth_lambda put_metric_data (SQS depth). No Batch job count/duration metric in application code. |
| Custom metrics | NOT IMPLEMENTED for Batch | job_compute_backlog_score in repositories_video.py is for API/CloudWatch replacement (comment L821); no put_metric_data for Batch in repo. |
| Structured logging | Partial | batch_main.py uses _log_json (L76, etc.) for event + kwargs. Logger.info(json.dumps(...)). No standard schema file. |
| Error aggregation | NOT IMPLEMENTED | No Sentry, error topic, or aggregation service in repo. |
| Alert definitions | NOT FOUND IN REPOSITORY | No CloudWatch alarm or alert definition. |
| Dead job alert | NOT IMPLEMENTED | |
| Cost alert | NOT IMPLEMENTED | |
| Failed job alert | NOT IMPLEMENTED | |
| Orphan detection alert | NOT IMPLEMENTED | |

---

## SECTION 10 — Production Readiness Matrix

| Capability | Status | Evidence | Missing Pieces |
|------------|--------|----------|----------------|
| Job creation + submit | Implemented | video_encoding.py:39-50, batch_submit.py:54-67 | — |
| aws_batch_job_id persistence | Implemented | video_encoding.py:49-50; scan_stuck 81-82; reconcile 136-137, 154-155 | — |
| job_set_running | Implemented | repositories_video.py:632-650; batch_main.py:122 | — |
| job_heartbeat | Implemented | repositories_video.py:658-673; batch_main.py:64-74, 154 | — |
| job_complete | Implemented | repositories_video.py:676-726; batch_main.py:158 | — |
| job_fail_retry | Implemented | repositories_video.py:728-745; batch_main 187,195,56; reconcile | — |
| job_mark_dead (Video updated) | Implemented | repositories_video.py:781-806; batch_main 197-199; internal_views 242 | Not called when scan_stuck (mgmt) sets DEAD |
| SIGTERM handler | Implemented | batch_main.py:51-61, 99-102 | — |
| Heartbeat loop | Implemented | batch_main.py:64-74, 154, 203 | — |
| Stuck detection | Implemented | scan_stuck_video_jobs.py:44-47; internal_views 229-232 | — |
| Stuck → DEAD (mgmt) | Implemented | scan_stuck_video_jobs.py:59-65 | Video.status not set to FAILED |
| Stuck → DEAD (API) | Implemented | internal_views.py:241-246 job_mark_dead | Video updated |
| Stuck → RETRY_WAIT + resubmit | Implemented (mgmt only) | scan_stuck_video_jobs.py:74-91 | Internal API does not resubmit |
| Batch timeout 4h | Implemented | video_job_definition.json timeout 14400 | — |
| ffmpeg timeout extension | Implemented | transcoder.py:194-201, 307-328, 364-377 | — |
| describe_jobs | Implemented | reconcile_batch_video_jobs.py:33-46, 101 | — |
| Reconcile command | Implemented | reconcile_batch_video_jobs.py:81-167 | Schedule not in repo |
| CloudWatch logging | Implemented | video_job_definition.json logConfiguration | — |
| ECR / IAM in IaC | Placeholder | video_job_definition.json; scripts replace | DEPENDS ON MANUAL AWS CONSOLE CONFIG if scripts not run |
| HLS validation (local) | Implemented | validate.py:8-38; processor.py:296 | — |
| R2 upload + retry | Implemented | r2_uploader.py:69-84 | — |
| Partial upload cleanup | NOT IMPLEMENTED | — | — |
| Success marker / post-upload verify | NOT IMPLEMENTED | — | — |
| Tenant concurrency limit | NOT IMPLEMENTED | — | — |
| Global job cap (app) | NOT IMPLEMENTED | — | Only CE maxvCpus |
| Scheduled scan_stuck | NOT IMPLEMENTED | Lambda exists, calls API; no rule in repo | DEPENDS ON MANUAL AWS CONSOLE CONFIG |
| Scheduled reconcile | NOT IMPLEMENTED | — | — |
| DB constraint Job↔Video | NOT IMPLEMENTED | — | — |
| Uniqueness one RUNNING per video | NOT IMPLEMENTED | — | — |
| Video.status on mgmt DEAD | NOT IMPLEMENTED | scan_stuck_video_jobs does not call job_mark_dead | — |
| Transaction create+submit+save | NOT IMPLEMENTED | video_encoding.py:39-50 | Two saves, no atomicity |
| Idempotency key on submit | NOT IMPLEMENTED | — | — |
| Orphan Batch job cancel | NOT IMPLEMENTED | — | — |
| CloudWatch alarms | NOT IMPLEMENTED | — | — |
| Cost/abuse controls | NOT IMPLEMENTED | — | — |

---

**END OF AUDIT.**
