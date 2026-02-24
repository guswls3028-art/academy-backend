# Post-Refactor Production Readiness Verification Report

Fact-based verification of the refactor claims. Evidence: file path + line range or migration/function name only.

---

## 1) CHANGE VERIFICATION TABLE

| Refactor claim | Status | Evidence | Notes |
|----------------|--------|----------|--------|
| **Phase 1: UniqueConstraint — only one active job per video (QUEUED/RUNNING/RETRY_WAIT)** | VERIFIED | `apps/support/video/models.py` L207–214: `UniqueConstraint(fields=["video"], condition=Q(state__in=["QUEUED","RUNNING","RETRY_WAIT"]), name="unique_video_active_job")`. `apps/support/video/migrations/0006_unique_video_active_job.py` L14–20: `AddConstraint` with same condition and name. | — |
| **Phase 1: create_job_and_submit_batch uses transaction.atomic** | VERIFIED | `apps/support/video/services/video_encoding.py` L86–102: `with transaction.atomic():` wraps job create, video.current_job_id save, submit_batch_job, and on success job.aws_batch_job_id save. | — |
| **Phase 1: Rollback on submit failure** | VERIFIED | `apps/support/video/services/video_encoding.py` L100–102: if `aws_job_id` is falsy, `raise RuntimeError(...)` inside the atomic block; L103–104 catches `RuntimeError` and returns None (transaction rolled back). | — |
| **Phase 1: Idempotency — return existing active job** | VERIFIED | `apps/support/video/services/video_encoding.py` L43–49: `VideoTranscodeJob.objects.filter(video=video, state__in=[QUEUED,RUNNING,RETRY_WAIT]).first()`; if existing, return it. | — |
| **Phase 1: scan_stuck uses job_mark_dead so Video.status becomes FAILED** | VERIFIED | `apps/support/video/management/commands/scan_stuck_video_jobs.py` L40, L60–64: imports `job_mark_dead`, calls `job_mark_dead(str(job.id), error_code="STUCK_MAX_ATTEMPTS", error_message=...)` when attempt_after >= MAX_ATTEMPTS. `academy/adapters/db/django/repositories_video.py` L799–802: `job_mark_dead` updates `Video.objects.filter(current_job_id=job_id).update(status=Video.Status.FAILED, ...)`. | — |
| **Phase 1: retry view handles IntegrityError** | VERIFIED | `apps/support/video/views/video_views.py` L9: `from django.db.utils import IntegrityError`. L572–584: `except IntegrityError:` re-queries active job for video, returns 200 with existing job_id or ValidationError. | — |
| **Phase 1: retry view uses job_mark_dead + terminate_batch_job** | VERIFIED | `apps/support/video/views/video_views.py` L493–495: imports `job_mark_dead`, `terminate_batch_job`. L521–525 (stale no batch_id): `job_mark_dead(...)`. L534–535 (stale QUEUED/RETRY_WAIT): `terminate_batch_job(str(cur.id), reason="superseded")` then `job_mark_dead(...)`. L544–545 (RUNNING): `terminate_batch_job(str(cur.id), reason="superseded")`, `job_set_cancel_requested(cur.id)`. | — |
| **Phase 2: terminate_batch_job implemented** | VERIFIED | `apps/support/video/services/batch_submit.py` L84–113: `terminate_batch_job(video_job_id, reason="superseded")` loads job, gets aws_batch_job_id, calls `client.terminate_job(jobId=aws_batch_job_id, reason=reason[:256])`. | — |
| **Phase 2: Reconcile — multiple active jobs per video: keep latest, DEAD the rest, terminate AWS** | VERIFIED | `apps/support/video/management/commands/reconcile_batch_video_jobs.py` L90–128: dupes by video_id with `Count("id")` filter n__gt=1; per video jobs ordered by `-created_at`; keep=jobs[0], older=jobs[1:]; for each older: `terminate_batch_job(str(job.id), reason="reconcile_duplicate")` then `job_mark_dead(...)`. | — |
| **Phase 2: Reconcile terminates orphan Batch jobs** | VERIFIED | `apps/support/video/management/commands/reconcile_batch_video_jobs.py` L211–246: `list_jobs` paginator for jobQueue=VIDEO_BATCH_JOB_QUEUE, status RUNNING and RUNNABLE; db_aws_ids from active jobs; for each aws_id not in db_aws_ids, `batch_client.terminate_job(jobId=aws_id, reason="reconcile_orphan")`. | — |
| **Phase 3: tmp prefix upload _tmp/{job_id}** | VERIFIED | `apps/core/r2_paths.py` L28–30: `video_hls_tmp_prefix(tenant_id, video_id, job_id)` returns `tenants/{tenant_id}/video/hls/{video_id}/_tmp/{job_id}`. `src/infrastructure/video/processor.py` L116–119, L393–405: `hls_tmp_prefix = video_hls_tmp_prefix(...)`; `upload_directory(..., prefix=hls_tmp_prefix, ...)`. | — |
| **Phase 3: publish tmp → final, then verify HLS integrity on R2, delete tmp** | VERIFIED | `apps/worker/video_worker/video/r2_uploader.py` L137–163: `publish_tmp_to_final` lists keys under tmp_prefix, copies each to final_prefix via `copy_object`, then `delete_prefix(..., prefix=tmp_prefix)`. L164–203: `verify_hls_integrity_r2` gets master.m3u8, parses lines for .m3u8, gets variant playlists, counts .ts segments, head_object each segment; raises UploadIntegrityError if master missing, variant missing, segment missing, or segment_count < min_segments. `src/infrastructure/video/processor.py` L413–430: after upload_directory to tmp, calls publish_tmp_to_final then verify_hls_integrity_r2. | — |
| **Phase 3: On failure delete final + raise UploadIntegrityError** | VERIFIED | `src/infrastructure/video/processor.py` L431–449: `except UploadIntegrityError as e:` emits ops event, then `delete_prefix(bucket=..., prefix=hls_prefix, ...)`, then `raise`. | — |
| **Phase 4: EventBridge schedule JSON exists for reconcile (rate 2 min)** | VERIFIED | `scripts/infra/eventbridge/reconcile_video_jobs_schedule.json`: `"ScheduleExpression": "rate(2 minutes)"`, `"Name": "academy-reconcile-video-jobs"`, `"State": "ENABLED"`. | Target (Lambda/ECS) for the rule is not in repo. |
| **Phase 4: Reconcile supports orphan terminate (list_jobs + terminate)** | VERIFIED | See Phase 2 orphan row. `reconcile_batch_video_jobs.py` L224–236: `list_jobs` then `terminate_job`. | — |
| **Phase 5: VIDEO_TENANT_MAX_CONCURRENT, VIDEO_GLOBAL_MAX_CONCURRENT, VIDEO_MAX_JOBS_PER_VIDEO exist** | VERIFIED | `apps/api/config/settings/base.py` L353–355: `VIDEO_TENANT_MAX_CONCURRENT = int(os.getenv(..., "2"))`, `VIDEO_GLOBAL_MAX_CONCURRENT = int(..., "20")`, `VIDEO_MAX_JOBS_PER_VIDEO = int(..., "10")`. | — |
| **Phase 5: video_encoding checks limits and emits OpsEvent on tenant limit exceeded** | VERIFIED | `apps/support/video/services/video_encoding.py` L51–54: reads settings; L56–59: tenant_active count, L60–70: if tenant_active >= tenant_limit, `emit_ops_event("TENANT_LIMIT_EXCEEDED", ...)` then return None; L72–77: global_active check; L79–82: video_job_count check. | — |
| **Phase 6: VideoOpsEvent model + migration** | VERIFIED | `apps/support/video/models.py` L218–256: `VideoOpsEvent` with type, severity, tenant_id, video_id, job_id, aws_batch_job_id, payload, created_at; EventType choices include JOB_DEAD, BATCH_DESYNC, UPLOAD_INTEGRITY_FAIL, ORPHAN_CANCELLED, TENANT_LIMIT_EXCEEDED. `apps/support/video/migrations/0007_videoopsevent.py`: CreateModel VideoOpsEvent, AddIndex on (type, created_at). | — |
| **Phase 6: Ops event emitter publishes CloudWatch metrics** | VERIFIED | `apps/support/video/services/ops_events.py` L44–84: `emit_ops_event` calls `_publish_metric(event_type, ...)`. `_publish_metric` maps event_type to MetricName (DeadJobs, FailedJobs, UploadFailures, ActiveJobs), calls `cw.put_metric_data(Namespace=VIDEO_CLOUDWATCH_NAMESPACE, MetricData=[{MetricName, Value: 1, Unit: "Count", Dimensions: EventType}])`. | Namespace from settings VIDEO_CLOUDWATCH_NAMESPACE (base.py L356). |
| **Phase 6: batch_main logs always include job_id, tenant_id, video_id, aws_batch_job_id** | PARTIAL | `apps/worker/video_worker/batch_main.py` L77–81: `_log_json(event, job_id, tenant_id=..., video_id=..., aws_batch_job_id=..., **kwargs)` builds payload with all four. All calls after job_obj is loaded pass these (L121–166, 161–165, 169–185, 207–214, 236–241, 261–266, 303–309). | L58: `_log_json("BATCH_TERMINATED", job_id=jid, signal=signum)` in signal handler does not pass tenant_id, video_id, aws_batch_job_id (not available in handler). |
| **Phase 7: validate_video_system command exists** | VERIFIED | `apps/support/video/management/commands/validate_video_system.py`: Command `validate_video_system`. | — |
| **Phase 7: Checks: no heartbeat, READY missing hls_path, PROCESSING without active job, duplicate active jobs, orphan AWS jobs** | VERIFIED | Same file: L44–51 RUNNING without last_heartbeat_at; L54–63 PROCESSING without current_job or current_job not in QUEUED/RUNNING/RETRY_WAIT; L65–69 READY with hls_path=""; L72–84 duplicate active jobs per video_id; L86–106 orphan AWS jobs via list_jobs vs db_aws_ids. | --fix argument exists but no fix logic implemented (NOT IMPLEMENTED). |

---

## 2) STATE MODEL & INVARIANTS (FACTUAL)

**Job.state values (VideoTranscodeJob):**  
`apps/support/video/models.py` L166–173: QUEUED, RUNNING, SUCCEEDED, FAILED, RETRY_WAIT, DEAD, CANCELLED.

**Video.status values (Video):**  
`apps/support/video/models.py` L36–41: PENDING, UPLOADED, PROCESSING, READY, FAILED.

**Mapping rules enforced in code:**

- **Upload complete → UPLOADED:** Callers set `video.status = Video.Status.UPLOADED` before enqueue (e.g. `apps/support/video/views/video_views.py` L424, L444, L465, L529, L542, L551).
- **job_complete → READY:** `academy/adapters/db/django/repositories_video.py` L702–706: inside `job_complete`, `video.status = Video.Status.READY`, `video.hls_path = ...`, then save.
- **job_mark_dead → FAILED:** `academy/adapters/db/django/repositories_video.py` L799–802: `Video.objects.filter(current_job_id=job_id).update(status=Video.Status.FAILED, error_reason=...)`.

**Uniqueness (one active job per video):**  
Enforced by DB constraint. Model: `apps/support/video/models.py` L207–214, name `unique_video_active_job`, fields `["video"]`, condition `Q(state__in=["QUEUED","RUNNING","RETRY_WAIT"])`. Migration: `apps/support/video/migrations/0006_unique_video_active_job.py` L14–20 `AddConstraint`.

**Idempotency (exact query):**  
`apps/support/video/services/video_encoding.py` L43–46:  
`VideoTranscodeJob.objects.filter(video=video, state__in=[VideoTranscodeJob.State.QUEUED, VideoTranscodeJob.State.RUNNING, VideoTranscodeJob.State.RETRY_WAIT]).first()`  
If not None, function returns that instance (L47–49).

---

## 3) R2 ATOMICITY (FACTUAL)

**Tmp prefix shape:**  
`apps/core/r2_paths.py` L28–30:  
`tenants/{tenant_id}/video/hls/{video_id}/_tmp/{job_id}`  
No trailing slash in return value; callers append slash where needed (e.g. r2_uploader L149–150).

**Publish mechanism:**  
Copy, then delete tmp. `apps/worker/video_worker/video/r2_uploader.py` L137–163:  
- `list_prefix(bucket, tmp_prefix)`; for each key, `rel = key[len(tmp_prefix):]`, `dest_key = final_prefix + rel`; `client.copy_object(CopySource={Bucket, Key: key}, Bucket=bucket, Key=dest_key)`; then `delete_prefix(..., prefix=tmp_prefix)` (batch delete_objects in chunks of 1000).

**Cleanup on failure:**  
- After copy, tmp is always deleted (L163).  
- On `UploadIntegrityError`: `src/infrastructure/video/processor.py` L431–449: `delete_prefix(bucket=cfg.R2_BUCKET, prefix=hls_prefix, ...)` (final prefix), then re-raise. Tmp already deleted by publish_tmp_to_final before verify runs.

**Integrity checks:**  
`apps/worker/video_worker/video/r2_uploader.py` L164–203:  
- GET object at `prefix + "master.m3u8"`; decode body; lines = non-empty, non-comment.  
- For each line ending with `.m3u8`, variant_key = prefix + line; GET variant playlist; for each line in variant body that is non-comment and ends with `.ts`, segment_count += 1, seg_key = variant_key dir + "/" + vline, head_object(seg_key).  
- If segment_count < min_segments (default 3), raise UploadIntegrityError.  
- Raises UploadIntegrityError for: master missing, variant missing, segment missing, or segment count &lt; min_segments.

---

## 4) AWS BATCH CONTROL SURFACE (FACTUAL)

**submit_batch_job code path:**  
`apps/support/video/services/batch_submit.py` L18–80: checks VIDEO_BATCH_JOB_QUEUE and VIDEO_BATCH_JOB_DEFINITION; boto3 client("batch"); `client.submit_job(jobName=..., jobQueue=queue_name, jobDefinition=job_def_name, parameters={"job_id": ...}, containerOverrides={"environment": [{"name":"VIDEO_JOB_ID","value": video_job_id}]})`; returns (jobId, None) or (None, err_msg).

**terminate_batch_job:**  
`apps/support/video/services/batch_submit.py` L84–113: load VideoTranscodeJob by video_job_id; aws_batch_job_id = job.aws_batch_job_id; if empty return True; else `client.terminate_job(jobId=aws_batch_job_id, reason=reason[:256])`.

**Reconcile logic:**  
- **describe_jobs:** `apps/support/video/management/commands/reconcile_batch_video_jobs.py` L39–52 `_describe_jobs_boto3(aws_job_ids)`; L148–151 aws_ids from jobs, batch_jobs = _describe_jobs_boto3(aws_ids), by_aws_id map.  
- **Selection:** L130–144: jobs with state in QUEUED/RUNNING/RETRY_WAIT, non-empty aws_batch_job_id, updated_at &lt; cutoff, order_by updated_at, [:50].  
- **Per-job actions:** SUCCEEDED → job_complete if video READY and hls_path else job_fail_retry; FAILED → job_fail_retry (and resubmit if --resubmit); RUNNING and job.state QUEUED → job_set_running; bj is None → job_fail_retry (and resubmit if --resubmit).  
- **list_jobs for orphans:** L224–236: paginator list_jobs jobQueue=VIDEO_BATCH_JOB_QUEUE, jobStatus RUNNING and RUNNABLE; db_aws_ids from active jobs’ aws_batch_job_id; terminate any aws_id not in db_aws_ids.

**aws_batch_job_id update and old jobs:**  
- Set on successful submit: `apps/support/video/services/video_encoding.py` L96–98.  
- Reconcile resubmit: `reconcile_batch_video_jobs.py` L184–186, L205–207: after submit_batch_job, job.aws_batch_job_id = aws_job_id, save.  
- When retry replaces job: previous job is either DEAD (job_mark_dead) or left RUNNING with cancel_requested; previous AWS job is terminated via terminate_batch_job before or when marking DEAD (video_views.py L534, L545). Old aws_batch_job_id remains on the DEAD job row; no code clears it.

---

## 5) SCHEDULING / IaC (FACTUAL)

**Batch CE, Job Definition, Job Queue:**

- **Compute environment:** `scripts/infra/batch/video_compute_env.json`. Placeholders: PLACEHOLDER_SERVICE_ROLE_ARN, PLACEHOLDER_SUBNET_1, PLACEHOLDER_SECURITY_GROUP_ID, PLACEHOLDER_INSTANCE_PROFILE_ARN. minvCpus=0, maxvCpus=32, desiredvCpus=0.  
- **Job definition:** `scripts/infra/batch/video_job_definition.json`. Placeholders: PLACEHOLDER_ECR_URI, PLACEHOLDER_JOB_ROLE_ARN, PLACEHOLDER_EXECUTION_ROLE_ARN, PLACEHOLDER_REGION.  
- **Job queue:** `scripts/infra/batch/video_job_queue.json` (jobQueueName, state, priority, computeEnvironmentOrder).  
- **Deployment:** `scripts/infra/batch_video_setup.ps1`, `scripts/infra/batch_video_setup_full.ps1`, `scripts/infra/batch_video_verify_and_register.ps1` use/register these JSON files. Actual ARNs/subnets/security groups must be supplied (e.g. by replacing placeholders or passing from env). **DEPENDS ON MANUAL AWS CONSOLE CONFIG** or external provisioning for placeholder values.

**EventBridge rule for reconcile:**  
`scripts/infra/eventbridge/reconcile_video_jobs_schedule.json`: Name, Description, ScheduleExpression "rate(2 minutes)", State. No Target property in repo. **DEPENDS ON MANUAL AWS CONSOLE CONFIG** (or separate IaC) to create rule and attach target (Lambda or ECS RunTask) that runs `python manage.py reconcile_batch_video_jobs`.

**Lambda (scan-stuck API caller):**  
`infra/worker_asg/video_scan_stuck_lambda/lambda_function.py` exists (scan-stuck invocation). `infra/worker_asg/video_dlq_poller_lambda/lambda_function.py` calls internal API for job_mark_dead (DLQ). Whether these are used for Batch reconcile or only legacy ASG is not defined in the EventBridge JSON; reconcile target is not in repo. **NOT FOUND IN REPOSITORY** as EventBridge target for reconcile.

---

## 6) SECURITY / IAM / NETWORKING (FACTUAL)

**IAM role ARN placeholders and where set:**

- **video_compute_env.json:** serviceRole = PLACEHOLDER_SERVICE_ROLE_ARN; instanceRole = PLACEHOLDER_INSTANCE_PROFILE_ARN.  
- **video_job_definition.json:** jobRoleArn = PLACEHOLDER_JOB_ROLE_ARN; executionRoleArn = PLACEHOLDER_EXECUTION_ROLE_ARN.  
- **video_job_definition.json** logConfiguration: awslogs-region = PLACEHOLDER_REGION.  
- Replacement: `scripts/infra/batch_video_setup.ps1` and related scripts read JSON and call AWS APIs; placeholders are typically replaced in script variables or env (e.g. batch_video_setup.ps1 L17–18 queue/jobdef names; actual ARN substitution not shown in the read fragment). **DEPENDS ON MANUAL AWS CONSOLE CONFIG** or script/env to set real ARNs.

**IAM policy JSON in repo:**

- **Batch job role (container):** `scripts/infra/iam/policy_video_job_role.json` (SSM GetParameter, ECR, CloudWatch Logs).  
- **Trust / Batch:** `scripts/infra/iam/trust_batch_service.json`, `scripts/infra/iam/trust_ec2.json`, `scripts/infra/iam/trust_ecs_tasks.json` (present in scripts/infra/iam).  
- Instance profile / service role policies for CE: referenced in batch scripts; full policy JSON for CE service role and instance profile not fully enumerated in this verification.

**Networking for Batch nodes:**  
`scripts/infra/batch/video_compute_env.json`: subnets and securityGroupIds only as PLACEHOLDER_SUBNET_1 and PLACEHOLDER_SECURITY_GROUP_ID. No NAT gateway, IGW, or VPC endpoint configuration found in the video Batch JSON or scripts under review. **NOT FOUND IN REPOSITORY** (explicit NAT/IGW/VPC endpoints for Batch nodes).

---

## 7) OPERATIONAL GAPS (FACTUAL, NO FIXES)

- **EventBridge reconcile target:** The file `scripts/infra/eventbridge/reconcile_video_jobs_schedule.json` defines only the schedule rule (rate 2 minutes). No target (Lambda ARN, ECS task definition, etc.) is in the repo. **DEPENDS ON MANUAL AWS CONSOLE CONFIG** (or separate IaC) to run reconcile every 2 minutes.

- **Batch/IAM placeholders:** `video_compute_env.json` and `video_job_definition.json` contain PLACEHOLDER_* for service role, instance profile, subnets, security groups, ECR URI, job role, execution role, region. Scripts use these files but do not fully define where production ARNs come from. **DEPENDS ON MANUAL AWS CONSOLE CONFIG** or external config to deploy.

- **validate_video_system --fix:** The command defines `--fix` (validate_video_system.py L35–36) but handle() does not use it; no fix logic (e.g. marking stale RUNNING or cleaning orphans) is implemented. **NOT IMPLEMENTED.**

- **BATCH_TERMINATED log missing tenant_id, video_id, aws_batch_job_id:** In `batch_main.py` L58, _log_json("BATCH_TERMINATED", job_id=jid, signal=signum) is called from the signal handler; tenant_id, video_id, and aws_batch_job_id are not passed (and are not available there). **PARTIAL** relative to “every batch_main log includes job_id, tenant_id, video_id, aws_batch_job_id.”

- **Reconcile cron/daemon:** Docstring and EventBridge JSON describe running reconcile every 120s / 2 min, but no in-process daemon or cron definition is in the repo; only the management command and the schedule JSON exist. **DEPENDS ON MANUAL AWS CONSOLE CONFIG** (or cron/Lambda/EventBridge target) to actually run the command.

---

## CONSTRAINTS VERIFICATION (FACTUAL)

- **1 tenant = full isolation via tenant prefix:** R2 paths use `tenants/{tenant_id}/...` (r2_paths.py). HLS and tmp prefixes include tenant_id. **VERIFIED** in code.

- **1 video upload = 1 AWS Batch job = 1 EC2 instance:** One job per video enforced by unique_video_active_job; one Batch job submitted per VideoTranscodeJob; Batch CE is EC2 with one job per run. **VERIFIED** in code and Batch design.

- **minvCpus = 0:** `scripts/infra/batch/video_compute_env.json`: `"minvCpus":0`. **VERIFIED** in repo.

- **No persistent worker pool:** CE desiredvCpus=0, minvCpus=0; no long-running worker process in code; batch_main runs per job and exits. **VERIFIED** in repo.
