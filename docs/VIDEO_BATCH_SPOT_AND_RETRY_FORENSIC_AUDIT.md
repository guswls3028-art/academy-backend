# Video Batch — Spot Interruption & Retry Forensic Audit Report

**Scope:** Backend codebase and infrastructure scripts only. Evidence-based; no assumptions.

---

## Phase 1 — Spot Interruption Handling Audit

### Search Terms and Results

| Search term | Matches | Location | Relevant to Spot handling? |
|-------------|---------|----------|----------------------------|
| "spot" | Yes | See below | Comment/config only |
| "interruption" | Yes | docs, redis_status_cache | ASG/scale-in context only |
| "instance terminated" | **No** | — | — |
| "Host EC2 instance terminated" | **No** | — | — |
| "SIGTERM" | **No** | — | — |
| "SIGINT" | **No** | — | — |
| signal handling | **No** | — | — |
| boto3 describe_jobs | Yes | diagnose, validate, verify scripts | Used for status check; no failure-reason-driven retry |
| failure reason parsing | Yes | verify script (statusReason) | Only for display/fail-fast; not used to trigger retry |
| 169.254.169.254 | **No** | — | — |
| instance-action | **No** | — | — |
| interruption notice | **No** | — | — |

### Evidence for Each Match

**1) apps/support/video/redis_status_cache.py (lines 34–35)**

```python
def set_asg_interrupt(ttl_seconds: int = VIDEO_ASG_INTERRUPT_TTL_SECONDS) -> bool:
    """Spot/Scale-in drain 시 설정. Lambda가 BacklogCount 퍼블리시 스킵하여 scale-out runaway 방지."""
```

- **What it does:** Sets a Redis key so Lambda skips publishing BacklogCount (legacy ASG scaling).
- **Spot handling?** **No.** Comment mentions “Spot” only in the sense of “drain”; it does not handle Batch Spot instance termination or container SIGTERM.

**2) scripts/infra/batch_video_setup.ps1 (line 145)**

```powershell
# instanceTypes update requires allocationStrategy BEST_FIT_PROGRESSIVE or SPOT_CAPACITY_OPTIMIZED
```

- **What it does:** Comment for AWS API constraint when updating instance types.
- **Spot handling?** **No.** No Spot interruption or signal handling.

**3) scripts/infra/batch_video_verify_and_register.ps1 (lines 121–125)**

```powershell
$jobDesc = Invoke-AwsJson @("batch", "describe-jobs", "--jobs", $awsJobId, ...)
$job = $jobDesc.jobs[0]
$status = $job.status
$reason = $job.statusReason
```

- **What it does:** Polls Batch job status during verification; uses `statusReason` only for MISCONFIGURATION check (line 134).
- **Spot handling?** **No.** No parsing of Spot-related failure reasons; no retry or DB update based on `statusReason`.

**4) scripts/diagnose_batch_video_infra.ps1 (line 155)**

- **What it does:** `describe-jobs` to show status and reason for a smoke job.
- **Spot handling?** **No.** Diagnostic only.

**5) apps/support/video/management/commands/validate_batch_video_system.py (lines 30–32)**

- **What it does:** Subprocess `aws batch describe-jobs` to validate job state/exitCode/reason.
- **Spot handling?** **No.** Validation only; no retry or failure-reason logic.

### Phase 1 Conclusion

**No explicit Spot interruption handling is implemented.**

- No SIGTERM/SIGINT or other signal handlers in the worker.
- No use of instance metadata (169.254.169.254) or Spot interruption notice.
- No code that parses Batch `statusReason` or container failure reason to detect “Host EC2 instance terminated” (or similar) and trigger retry or DB update.
- The only “Spot” reference is a comment in Redis ASG-interrupt logic (Lambda metric skip), unrelated to Batch Spot recovery.

---

## Phase 2 — Retry Policy Audit

### 2.1 scan_stuck_video_jobs

**File:** `apps/support/video/management/commands/scan_stuck_video_jobs.py`

| Item | Value |
|------|--------|
| Trigger condition | `state=RUNNING` and `last_heartbeat_at < now - threshold_minutes` (default 3) |
| MAX_ATTEMPTS | 5 (constant) |
| If attempt_after >= MAX_ATTEMPTS | Set state=DEAD, error_code=STUCK_MAX_ATTEMPTS |
| Else | Set state=RETRY_WAIT, increment attempt_count, call `submit_batch_job(str(job.id))` |
| Side effects | DB update (state, attempt_count, locked_*, error_*); optional aws_batch_job_id update |

**Critical finding:** The Batch worker (`batch_main.py`) **never** calls `job_set_running()`. The comment in `batch_main.py` states: "NO job_set_running. NO RUNNING state block." Jobs are created as QUEUED and transition to SUCCEEDED (job_complete) or RETRY_WAIT (job_fail_retry) when the container exits. So in the Batch-only path, **no job is ever set to RUNNING in the DB**. Therefore **scan_stuck_video_jobs never selects any Batch-originated job** (it only selects RUNNING jobs with stale heartbeat).

**File:** `apps/support/video/views/internal_views.py` (VideoScanStuckView, POST /api/v1/internal/video/scan-stuck/)

- Same filter: RUNNING + last_heartbeat_at < cutoff.
- On match: either job_mark_dead (if attempt_after >= 5) or state=RETRY_WAIT, attempt_count increment. **It does not call submit_batch_job** — so jobs moved to RETRY_WAIT by this API are not resubmitted.

### 2.2 attempt_count and MAX_ATTEMPTS

| Location | Value | Purpose |
|----------|--------|---------|
| scan_stuck_video_jobs.py | MAX_ATTEMPTS = 5 | Cap for stuck jobs; beyond that → DEAD |
| internal_views.VideoScanStuckView | max_attempts = 5 | Same logic (no resubmit) |
| batch_main.py | VIDEO_JOB_MAX_ATTEMPTS (env, default 5) | After job_fail_retry, if attempt_count >= this → job_mark_dead |
| models.VideoTranscodeJob | attempt_count (default 1) | Persisted field |

### 2.3 FAILED state handling

**job_fail_retry** (repositories_video.py, ~729–744):

- **When:** Called from batch_main on exception (including CancelledError) or from application failure.
- **Effect:** state → RETRY_WAIT, attempt_count += 1, error_message set. No distinction by failure type.
- **After:** batch_main then checks attempt_count >= VIDEO_JOB_MAX_ATTEMPTS and optionally calls job_mark_dead.

**No automatic resubmission for FAILED/RETRY_WAIT:** Nothing in code (cron, Lambda, or API) was found that periodically resubmits jobs in RETRY_WAIT. Resubmission happens only: (1) when scan_stuck_video_jobs moves a job from RUNNING to RETRY_WAIT and then calls submit_batch_job (management command only), or (2) when the user triggers retry (create_job_and_submit_batch or retry API). So **FAILED (application failure) jobs are not auto-retried**; they remain RETRY_WAIT until user retry or manual intervention.

### 2.4 Retry logic and failure type

- **Distinction by failure type:** **None.** job_fail_retry(reason) does not branch on infrastructure vs application vs timeout. Same path for all exceptions.
- **Retry blocked for specific error codes?** **No.** No logic that prevents retry based on error_code.
- **Can a Spot interruption be retried automatically?** **No.** On Spot kill the container exits without calling job_fail_retry or job_complete, so the job stays QUEUED. scan_stuck only considers RUNNING jobs, so it never picks up such a job. There is no Batch-status sync that sets DB to RUNNING or FAILED from Batch’s statusReason.

### Retry flow (structured)

```
[Submit] create_job_and_submit_batch → Job created QUEUED → submit_batch_job
    ↓
[Batch runs container] batch_main.main() — does NOT set RUNNING in DB
    ↓
  Success → job_complete() → state=SUCCEEDED, video READY
  Exception → job_fail_retry() → state=RETRY_WAIT, attempt_count++
             → if attempt_count >= MAX → job_mark_dead()
  (Container killed e.g. Spot) → no callback → DB remains QUEUED → no automatic recovery

[Stuck scanner] scan_stuck_video_jobs (management command)
  Query: state=RUNNING AND last_heartbeat_at < cutoff
  Batch path: no job is RUNNING in DB → query returns 0 rows → no action
  If RUNNING (e.g. legacy): → RETRY_WAIT + submit_batch_job, or DEAD if attempt_count >= 5

[User retry] Retry API → (backend may clear/stale job) → create_job_and_submit_batch / submit_batch_job
```

---

## Phase 3 — Batch Job Definition & Infrastructure Settings Audit

### 3.1 Source and values

| Item | Source | Configured value |
|------|--------|------------------|
| retryStrategy.attempts | scripts/infra/batch/video_job_definition.json | 1 |
| timeout | Same file | attemptDurationSeconds: 14400 (4 hours) |
| Compute Environment type | scripts/infra/batch/video_compute_env.json | type: EC2, no "bidPercentage" or spot configuration |
| allocationStrategy | video_compute_env.json, batch_video_setup.ps1 | BEST_FIT_PROGRESSIVE |
| maxvCpus | video_compute_env.json | 32 |
| instance types | video_compute_env.json, batch_video_setup.ps1 | c6g.large, c6g.xlarge, c6g.2xlarge |
| AMI / architecture | CE is EC2; instance types are c6g (Graviton) | ARM64 |

**video_job_definition.json (excerpt):**

```json
"retryStrategy":{"attempts":1},"timeout":{"attemptDurationSeconds":14400}
```

**video_compute_env.json:**

```json
{"computeEnvironmentName":"academy-video-batch-ce","type":"MANAGED","state":"ENABLED",...,"computeResources":{"type":"EC2","allocationStrategy":"BEST_FIT_PROGRESSIVE","minvCpus":0,"maxvCpus":32,"desiredvCpus":0,"instanceTypes":["c6g.large","c6g.xlarge","c6g.2xlarge"],...}}
```

- No `bidPercentage` or Spot-specific fields → **ON_DEMAND** (not SPOT) in current JSON. (If CE is later changed to SPOT in the console or another script, that would be outside this repo.)

### 3.2 Verification scripts

- **batch_video_verify_and_register.ps1:** Enforces retryStrategy.attempts == 1 in source and deployed job definition. Documents that retry is handled by Django (scan_stuck_video_jobs), not Batch.
- **Batch retry:** Intentionally disabled (attempts=1). Django-level retry is the only retry mechanism; for Batch-originated jobs, the only path that resubmits is the management command’s stuck path, which does not apply because RUNNING is never set in DB for Batch.

---

## Phase 4 — Idempotency & Safety Audit

### 4.1 job_complete

- **repositories_video.py:** If job.state == SUCCEEDED and video already READY with hls_path, returns True ("idempotent"). Allows QUEUED, RETRY_WAIT, RUNNING to transition to SUCCEEDED. Single transaction for job + video update.
- **Conclusion:** Duplicate success (e.g. same job run twice) is safe; second completion is no-op.

### 4.2 S3/R2 output

- **processor.py / r2_uploader:** Output path is deterministic (e.g. video_hls_prefix, video_hls_master_path by tenant_id, video_id). Re-run overwrites the same keys.
- **Conclusion:** Same logical output path; overwrite is intentional. No separate “partial output” state in DB for HLS path; final state is READY + hls_path.

### 4.3 Duplicate submission

- **create_job_and_submit_batch:** Creates a new VideoTranscodeJob each time; video.current_job_id is updated. Retry API may clear stale current_job_id and create a new job. No duplicate-submit guard per video (e.g. “one job per video” lock) beyond backend retry validation (e.g. “Already in backlog” when current_job_id is set and recent).
- **submit_batch_job:** Called with job.id; each call produces a new AWS Batch job. aws_batch_job_id on the same DB row can be overwritten when scan_stuck resubmits (same job.id, new Batch job).

### 4.4 Temp files

- **processor.py:** Uses temp_workdir context manager; cleanup is by design when the context exits. If the process is killed (e.g. Spot), the OS may leave temp files; no application-level cleanup of orphaned temp dirs was found (acceptable for ephemeral Batch nodes).

### 4.5 Risk classification

| Risk | Level | Explanation |
|------|--------|--------------|
| Same job runs twice (success) | **LOW** | job_complete is idempotent; duplicate success does not corrupt state. |
| Partial output / corrupt final state | **LOW** | Output is overwritten atomically; DB is updated only on full success (job_complete). No half-READY state. |
| Temp files | **LOW** | Context-managed; orphaned temp on kill is node-local and not persisted. |
| Duplicate Batch submissions for same logical job | **MEDIUM** | Resubmit (retry or stuck) can create multiple Batch jobs for one DB job; aws_batch_job_id is overwritten. No reconciliation of “old” Batch job IDs. |

---

## Phase 5 — Heartbeat & Stuck Logic Audit

### 5.1 last_heartbeat_at

- **DB:** VideoTranscodeJob.last_heartbeat_at (nullable). Set in job_set_running (to now) and job_heartbeat (update).
- **Batch worker:** Does **not** call job_set_running or job_heartbeat. So for Batch, last_heartbeat_at is **never** updated after job creation (job is created QUEUED; job_set_running is never invoked).
- **Redis:** set_video_heartbeat is called from RedisProgressAdapter.record_progress when job_id is "video:{video_id}" and tenant_id is set. So **Redis** heartbeat is updated during encoding (on each progress record). **DB** last_heartbeat_at is not updated in the Batch path.

### 5.2 Stuck condition

- **scan_stuck_video_jobs:** Stuck = state=RUNNING and last_heartbeat_at < now - threshold (default 3 minutes).
- Because Batch never sets RUNNING or last_heartbeat_at, **no Batch job satisfies this condition**. Stuck logic does not apply to current Batch flow.

### 5.3 Spot termination and false stuck

- **Can Spot termination create false stuck?** **No.** Stuck is defined only for RUNNING + stale heartbeat. Batch jobs never become RUNNING in DB, so they are never considered stuck.
- **Is heartbeat robust enough?** For Batch: DB heartbeat is not used (never set). Redis heartbeat is set on progress records. If the container is killed (Spot), progress stops and Redis key will expire (TTL). No code was found that uses Redis heartbeat expiry to mark the job as failed or to resubmit.

---

## Phase 6 — Gap Analysis

| Area | Implemented? | Evidence | Risk | Recommended action |
|------|---------------|----------|------|---------------------|
| Spot explicit handling | **No** | No SIGTERM/signal handling, no 169.254.169.254, no instance-action, no parsing of Batch statusReason for Spot/termination | **HIGH** | Add explicit handling: e.g. SIGTERM handler that calls job_fail_retry and exits, and/or a periodic sync that uses describe_jobs to set FAILED/RETRY_WAIT and resubmit for Spot/termination reasons. |
| Automatic retry (Batch job killed) | **No** | Container exit without callback leaves DB QUEUED; scan_stuck only considers RUNNING; no Batch→DB status sync | **HIGH** | Same as above: either in-container cleanup on signal or out-of-band sync + resubmit. |
| Retry safety (idempotency) | **Yes** | job_complete idempotent; single output path; overwrite semantics | **LOW** | — |
| Idempotency (double run) | **Yes** | job_complete returns early when already SUCCEEDED+READY | **LOW** | — |
| Signal handling (SIGTERM) | **No** | No signal.signal or similar in batch_main or processor | **HIGH** | Add SIGTERM handler that calls job_fail_retry (or equivalent) and exits so Batch marks job failed and Django can retry via existing retry path once FAILED/RETRY_WAIT is set. |
| Batch infra (retryStrategy, timeout) | **Yes** | attempts=1, timeout=14400 in JSON; verified by script | **LOW** | — |
| Stuck scanner applies to Batch | **No** | RUNNING never set in Batch path; scanner selects 0 jobs | **HIGH** | Either set RUNNING (and heartbeat) when Batch job starts (e.g. from worker or from a sync), or introduce a separate “QUEUED + no Batch activity” / “Batch FAILED” recovery path. |
| Internal scan-stuck API | **Partial** | Same RUNNING filter; does not call submit_batch_job after RETRY_WAIT | **MEDIUM** | Align with management command: resubmit after moving to RETRY_WAIT, or document that only the command performs resubmit. |

---

## Phase 7 — Executive Summary

### 1) Is Spot interruption safely handled?

**No.** There is no Spot-specific or instance-termination handling:

- No SIGTERM/SIGINT handling in the worker.
- No use of Spot interruption metadata or Batch failure reason to detect host termination.
- If the Batch node is terminated (e.g. Spot), the container exits without calling job_fail_retry or job_complete. The job remains QUEUED in the DB and is never resubmitted by scan_stuck (which only considers RUNNING jobs).

### 2) Is retry logic sufficient for production?

**Partially.**

- **Application failure:** Handled: batch_main calls job_fail_retry; attempt_count and DEAD cap are applied. User or API can retry.
- **Stuck (RUNNING + no heartbeat):** Logic exists in scan_stuck_video_jobs but does not apply to Batch, because RUNNING is never set in the Batch path.
- **Infrastructure failure (Spot/termination):** Not handled: no automatic retry or DB update.
- **RETRY_WAIT:** Only the management command resubmits (and only when coming from RUNNING). The internal scan-stuck API does not resubmit.

### 3) Is any infrastructure change required?

- **Compute environment:** Current code and JSON describe ON_DEMAND (no Spot in repo). If the account uses SPOT for Batch, behavior is unchanged without application or sync changes: Spot kills are still unrecovered.
- **Application/sync changes:** Either (1) handle SIGTERM in the worker and call job_fail_retry so the job moves to RETRY_WAIT and can be retried, and/or (2) add a periodic process that uses describe_jobs, detects FAILED/termination reasons, updates DB (e.g. to RETRY_WAIT or FAILED), and resubmits where appropriate.

### 4) Overall maturity level

**BASIC.**

- Retry and idempotency for normal success/failure are in place.
- Batch configuration (retryStrategy, timeout) is explicit and verified.
- Spot/interruption handling is absent; DB state (RUNNING/heartbeat) is not aligned with Batch lifecycle, so existing stuck logic does not apply to Batch jobs. Recovery from infrastructure failure (e.g. Spot) is not implemented.

---

*End of audit. All conclusions are based only on the code and scripts inspected.*
