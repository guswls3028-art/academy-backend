# Video Batch — Production Readiness Forensic Audit

**Scope:** Backend (academy), worker, management commands, infrastructure scripts, deployment, env, docs, AWS CLI usage.  
**Principle:** Evidence only. No assumptions. No speculation.

---

## PHASE 1 — Batch Lifecycle Synchronization Audit

### 1.1 DB State vs AWS Batch Lifecycle Mapping

| AWS Batch status | DB state set where? | Evidence (file:line) |
|------------------|---------------------|------------------------|
| **SUBMITTED**    | Not stored explicitly. Job created as QUEUED; `aws_batch_job_id` saved after submit. | `video_encoding.py:39-50` — `VideoTranscodeJob.objects.create(..., state=QUEUED)`; then `submit_batch_job`; on success `job.aws_batch_job_id = aws_job_id` and save. |
| **PENDING**      | No DB transition. | — |
| **RUNNABLE**     | No DB transition. | — |
| **STARTING**     | **Never set in DB.** | — |
| **RUNNING**      | **Never set in DB for Batch path.** `job_set_running()` exists but is **not called** by Batch worker. | `repositories_video.py:632-648` defines `job_set_running()`; `batch_main.py` docstring (L4-5): "NO job_set_running. NO RUNNING state block." Grep: no import/call of `job_set_running` in `batch_main.py` or `batch_entrypoint.py`. |
| **SUCCEEDED**    | Set when container exits successfully via `job_complete()`. | `batch_main.py:107-109` — `job_complete(job_id, hls_path, duration)`; `repositories_video.py:434-436` sets `job.state = SUCCEEDED`. |
| **FAILED**       | Set only on **application** failure via `job_fail_retry()` → state becomes **RETRY_WAIT** (not FAILED). DB `State.FAILED` exists in model but is **not used** in Batch flow; submit failure sets `state=FAILED` in video_encoding. | `batch_main.py:143-148` — `job_fail_retry(job_id, ...)`; `repositories_video.py:453` sets `job.state = RETRY_WAIT`. `video_encoding.py:52-55` on submit error: `job.state = VideoTranscodeJob.State.FAILED`. |

### 1.2 Where DB state is set to RUNNING

- **Evidence:** `job_set_running()` in `academy/adapters/db/django/repositories_video.py` (L632-648) sets `state=RUNNING`, `last_heartbeat_at=now`, `locked_by="batch"`.
- **Called from:** **Nowhere** in the Batch worker path. Not imported in `apps/worker/video_worker/batch_main.py` or `batch_entrypoint.py`.
- **Conclusion:** In the Batch-only path, **no job is ever set to RUNNING in the DB.**

### 1.3 Where heartbeat is updated

- **DB `last_heartbeat_at`:** Set only inside `job_set_running()` and `job_heartbeat()` (`repositories_video.py:659-673`). Neither is called by the Batch worker. So **heartbeat is never updated in DB for Batch jobs.**
- **Redis:** `RedisProgressAdapter.record_progress` (and thus progress-based keys) are used during encoding; `cache_video_status(..., "PROCESSING", ttl=21600)` is set at start in `batch_main.py:85`. No code found that uses Redis heartbeat expiry to mark job failed or resubmit.

### 1.4 Reconciliation: `aws_batch_job_id` and describe-jobs

- **Where `aws_batch_job_id` is set:**  
  - `apps/support/video/services/video_encoding.py:49-50` after successful `submit_batch_job`.  
  - `apps/support/video/management/commands/scan_stuck_video_jobs.py:81-82` when resubmitting after RETRY_WAIT (management command only).
- **Where describe-jobs is used:**  
  - **Diagnostic/validation only** — no DB update from result:  
    - `scripts/diagnose_batch_video_infra.ps1` (describe-jobs for status/reason).  
    - `scripts/infra/batch_video_verify_and_register.ps1` (describe-jobs for verification).  
    - `apps/support/video/management/commands/validate_batch_video_system.py:28-45` — `run_aws_batch_describe(job_id)` returns status/exitCode/reason; used only to validate (STEP 2), **does not update DB**.
- **Conclusion:** **No reconciliation** of DB with Batch status via describe-jobs. If a Batch job fails externally (host termination, container kill), **DB is never updated** by any application or cron/Lambda.

### 1.5 Full lifecycle mapping table

| Stage              | AWS Batch              | DB state   | Set by / File(s) |
|--------------------|------------------------|------------|-------------------|
| Job created        | —                      | QUEUED     | `video_encoding.create_job_and_submit_batch` |
| Submit success     | SUBMITTED/PENDING/…    | QUEUED     | (unchanged) |
| Submit failure     | —                      | FAILED     | `video_encoding.py:52-55` |
| Container starts   | RUNNING                | QUEUED     | **(no transition)** |
| Container succeeds | SUCCEEDED              | SUCCEEDED  | `batch_main` → `job_complete` |
| Container fails (app) | FAILED              | RETRY_WAIT | `batch_main` → `job_fail_retry` |
| Container killed (Spot/etc.) | FAILED           | **QUEUED** | **(no update)** |
| Stuck scanner      | —                      | RUNNING→RETRY_WAIT or DEAD | `scan_stuck_video_jobs` (only if state=RUNNING and stale heartbeat; **never true for Batch**) |

### 1.6 Gaps (evidence-based)

1. **RUNNING never set** — Batch worker does not call `job_set_running()`, so DB never reflects “job is running.”
2. **Heartbeat never updated** — `job_heartbeat()` is never called; `last_heartbeat_at` stays null for Batch jobs.
3. **No Batch→DB sync** — No code path uses `describe_jobs` to set DB to FAILED/RETRY_WAIT or to resubmit when Batch reports FAILED (e.g. host termination).
4. **External failure leaves DB wrong** — If the Batch job fails externally (Spot, instance refresh, container kill), the container exits without calling `job_fail_retry` or `job_complete`; DB remains QUEUED and no automated recovery runs.

### 1.7 Exact file paths

| Concern                    | File path |
|---------------------------|-----------|
| Job creation + submit     | `apps/support/video/services/video_encoding.py` |
| Batch submit only         | `apps/support/video/services/batch_submit.py` |
| Worker entry              | `apps/worker/video_worker/batch_entrypoint.py` |
| Worker main (no RUNNING)  | `apps/worker/video_worker/batch_main.py` |
| job_set_running / heartbeat | `academy/adapters/db/django/repositories_video.py` |
| Stuck scanner (mgmt)      | `apps/support/video/management/commands/scan_stuck_video_jobs.py` |
| describe-jobs (no DB sync)| `apps/support/video/management/commands/validate_batch_video_system.py`, `scripts/infra/batch_video_verify_and_register.ps1`, `scripts/diagnose_batch_video_infra.ps1` |

---

## PHASE 2 — Spot / Infrastructure Failure Recovery Audit

### 2.1 Search results (evidence)

| Search term / concept        | Found? | Location / note |
|-----------------------------|--------|------------------|
| `signal.signal`             | No     | Not in `batch_main.py`, `batch_entrypoint.py`, or `src/infrastructure/video/processor.py`. Present in `apps/worker/messaging_worker/sqs_main.py`, `academy/framework/workers/ai_sqs_worker.py`, `libs/observability/shutdown.py` — not used by Batch video worker. |
| SIGTERM / SIGINT handler    | No     | No handler in Batch worker or processor. |
| Termination notice metadata | No     | No 169.254.169.254 or instance-action in repo (docs only). |
| describe_jobs polling       | Yes    | Only in scripts (validate_batch_video_system, batch_video_verify_and_register, diagnose_batch_video_infra) for validation/diagnosis; **no polling loop that updates DB**. |
| Failure reason parsing      | Yes    | validate_batch_video_system and verify script read statusReason; **no code path that parses failure reason to set DB state or trigger resubmit**. |

### 2.2 Worker SIGTERM behavior

- **Does the worker handle SIGTERM gracefully?** **No.**  
  Evidence: `batch_main.py` and `batch_entrypoint.py` contain no `signal.signal(signal.SIGTERM, ...)`. On SIGTERM (e.g. Spot), the process is killed without calling `job_fail_retry` or `job_complete`; DB stays QUEUED.

### 2.3 Automated recovery for host termination

- **Is there any automated recovery for host termination?** **No.**  
  - Stuck scanner selects only `state=RUNNING` and `last_heartbeat_at < cutoff`. Batch jobs never have RUNNING or updated heartbeat, so they are never selected.  
  - No periodic job that runs describe-jobs on jobs with `aws_batch_job_id` and updates DB or resubmits.

### 2.4 Periodic reconciliation job

- **Is there a periodic reconciliation job that syncs Batch status to DB?** **No.**  
  - `reconcile_video_processing` reclaims **Video** status PROCESSING (lease/heartbeat); it does not query Batch or VideoTranscodeJob.  
  - No other management command or Lambda uses describe-jobs to reconcile Batch→DB.

### 2.5 Explicit confirmation

**No automated infrastructure-failure recovery exists.**

- No SIGTERM (or other signal) handler in the Batch worker.
- No Spot/interruption metadata usage.
- No describe-jobs–based sync that updates DB or resubmits on Batch FAILED.
- Stuck logic does not apply to Batch-originated jobs (RUNNING never set).

---

## PHASE 3 — Automatic Retry Validation

### 3.1 Failure scenarios and outcomes

| Scenario                     | DB state outcome        | Retry trigger                    | Max attempts enforcement |
|-----------------------------|-------------------------|----------------------------------|----------------------------|
| Application exception       | RETRY_WAIT (job_fail_retry) | User/API retry or (theoretically) scan_stuck; scan_stuck does not select these jobs (RUNNING filter). | Yes: `batch_main.py:146-148` — if `attempt_count >= VIDEO_JOB_MAX_ATTEMPTS` (default 5), `job_mark_dead()`. |
| Timeout                     | Batch marks FAILED; DB **unchanged** (still QUEUED). | None. | N/A (no DB update). |
| Container killed            | DB **unchanged** (QUEUED). | None. | N/A. |
| Spot termination            | DB **unchanged** (QUEUED). | None. | N/A. |
| Instance refresh            | Same as container kill. | None. | N/A. |
| AWS Batch internal failure  | Batch FAILED; DB **unchanged**. | None. | N/A. |
| Submit failure              | FAILED (video_encoding). | User retry. | No attempt_count bump for submit failure. |
| Stuck (RUNNING + no heartbeat) | RETRY_WAIT or DEAD (scan_stuck). | Management command: submit_batch_job after RETRY_WAIT. Internal API: **no** submit_batch_job. | MAX_ATTEMPTS=5 in scan_stuck. **Not applicable** to Batch because RUNNING is never set. |

### 3.2 Retry decision matrix

| Current state | Event / condition              | Action in code | Resubmit? |
|---------------|---------------------------------|----------------|-----------|
| QUEUED        | Container exits success         | job_complete → SUCCEEDED | No (done). |
| QUEUED        | Container exits exception       | job_fail_retry → RETRY_WAIT | No (user/API retry). |
| QUEUED        | Container killed / Spot / timeout | **None** (DB stays QUEUED) | No. |
| RETRY_WAIT    | User/API retry                  | New job or reuse; create_job_and_submit_batch | Yes. |
| RETRY_WAIT    | scan_stuck (from RUNNING only)   | N/A for Batch. | Mgmt: yes (submit_batch_job). API: no. |
| RUNNING       | Stale heartbeat                 | scan_stuck → RETRY_WAIT or DEAD | Mgmt: yes. API: no submit. **(Batch never RUNNING.)** |
| QUEUED/RETRY_WAIT | attempt_count >= 5 (in worker) | job_mark_dead → DEAD | No further retry. |

### 3.3 MAX_ATTEMPTS

- **Worker:** `VIDEO_JOB_MAX_ATTEMPTS` env (default 5) in `batch_main.py:38, 146-148`; after `job_fail_retry`, if `attempt_count >= VIDEO_JOB_MAX_ATTEMPTS`, `job_mark_dead()`.
- **Stuck scanner:** `MAX_ATTEMPTS = 5` in `scan_stuck_video_jobs.py:21`; same for internal `VideoScanStuckView` (max_attempts=5). Enforced only when moving RUNNING→RETRY_WAIT/DEAD (not applicable to Batch).

---

## PHASE 4 — Multi-Tenant Isolation Audit

### 4.1 Tenant scoping by component

| Component | Filter by tenant? | Evidence |
|-----------|-------------------|----------|
| **Workbox / progress** | Yes | `progress_views.py:71` — `Video.objects.filter(pk=video_id, session__lecture__tenant_id=tenant.id)`. Tenant from request. |
| **VideoTranscodeJob queries** | | |
| — create_job_and_submit_batch | Implicit | Job created with `tenant_id=video.session.lecture.tenant.id` (`video_encoding.py:33-41`). |
| — job_get_by_id(job_id) | No | `repositories_video.py:626-629` — `VideoTranscodeJob.objects.filter(pk=job_id)`. No tenant_id. Caller must ensure job_id is trusted. |
| — scan_stuck_video_jobs | **No** | `scan_stuck_video_jobs.py:44-47` — `VideoTranscodeJob.objects.filter(state=RUNNING, last_heartbeat_at__lt=cutoff)`. **No tenant_id.** Global scan. |
| — internal VideoScanStuckView | **No** | `internal_views.py:229-232` — same filter, no tenant_id. |
| — retry API (video_views.retry) | Implicit | Video from `self.get_object()` (ViewSet); then `VideoTranscodeJob.objects.filter(pk=video.current_job_id)`. Job access is via video, which is tenant-scoped by permission/middleware. |
| — perform_destroy (delete) | Implicit | Same: video from get_object; job by video.current_job_id. |
| — validate_batch_video_system | **No** | `validate_batch_video_system.py:147` — `VideoTranscodeJob.objects.order_by("-created_at")[:3]`. **Global.** No tenant_id. |
| **job_count_backlog / job_compute_backlog_score** | **No** | `repositories_video.py:509-536` — filter by state only (QUEUED, RETRY_WAIT). **Global** count/score. |
| **DLQ mark dead (internal)** | No | Accepts `job_id` in body; `job_get_by_id(job_id)` — no tenant check. If internal API is compromised or job_id guessed, any job could be marked DEAD. |
| **reconcile_video_processing** | No | `get_video_queryset_with_relations().filter(status=Video.Status.PROCESSING)` — no tenant filter. Global reclaim (legacy lease path). |

### 4.2 Summary

- **All queries filter by tenant_id?** **No.**  
  - scan_stuck (mgmt + internal API), validate_batch_video_system, job_count_backlog, job_compute_backlog_score, and DLQ mark-dead path do **not** filter by tenant_id.  
- **Global job scanning without tenant constraint?** **Yes.**  
  - scan_stuck_video_jobs, internal scan-stuck API, validate_batch_video_system, backlog count/score all operate on all tenants.  
- **Background job processes across tenants?** **Yes.**  
  - Stuck scanner and internal scan-stuck process all RUNNING jobs (and in practice no Batch job is RUNNING); backlog metrics are global.  

### 4.3 Cross-tenant leakage risks

- **Data exposure:** Retry/destroy are scoped by video (get_object); progress API filters by `session__lecture__tenant_id=tenant.id`. So **workbox and staff retry/delete are tenant-safe** assuming middleware/permission correctly resolve tenant.
- **Internal API:**  
  - **scan-stuck:** No tenant filter; it only affects RUNNING + stale heartbeat (no Batch job today). So no practical cross-tenant data leak; it’s global by design.  
  - **dlq-mark-dead:** Accepts arbitrary `job_id`. If an actor can call internal API with another tenant’s job_id, that job can be marked DEAD. **Mitigation:** Internal API must be locked (IsLambdaInternal, IP allowlist, API key).  
- **Backlog score/count:** Global metrics; no per-tenant isolation. Acceptable if intended for single-queue autoscaling.

---

## PHASE 5 — Production Risk Assessment

| Risk | Level | Why (evidence) |
|------|--------|-----------------|
| **Data loss risk** | **MEDIUM** | Completed work is committed via `job_complete` in one transaction. Idempotent when already SUCCEEDED+READY. Risk: jobs that never run or are killed (Spot) stay QUEUED and are never retried automatically — “silent” permanent backlog, not classic data loss. |
| **Silent failure risk** | **HIGH** | Spot/container kill/timeout/Batch failure: DB never updated, job stays QUEUED. No RUNNING, so stuck scanner does not run. No describe-jobs sync. User sees “stuck” until manual retry or support. |
| **Cross-tenant leakage risk** | **LOW** | User-facing and workbox paths are tenant-scoped (video from get_object or tenant_id filter). Internal dlq-mark-dead accepts job_id without tenant check; depends on internal API protection. |
| **Infinite retry risk** | **LOW** | attempt_count and DEAD enforced in worker (VIDEO_JOB_MAX_ATTEMPTS=5). Stuck path also uses MAX_ATTEMPTS=5. RETRY_WAIT is not auto-resubmitted except by management command when coming from RUNNING (which Batch never is). |
| **Orphaned job risk** | **HIGH** | QUEUED jobs whose Batch job was killed (Spot, etc.) remain QUEUED forever with no automatic resubmit or DB update. Effect: orphaned jobs and “ghost” backlog. |
| **AWS cost explosion risk** | **LOW** | retryStrategy.attempts=1 in job definition; no runaway Batch retries. Stuck scanner does not select Batch jobs. Backlog count/score are global but do not by themselves submit jobs. |

---

## PHASE 6 — Production Readiness Score

### Score: **48 / 100**

**Reasons (evidence-based):**

- **Lifecycle sync:** RUNNING and heartbeat are never set; no Batch→DB reconciliation. **(−25)**  
- **Infrastructure failure:** No SIGTERM handling, no Spot/interruption handling, no describe-jobs–based recovery. **(−20)**  
- **Retry:** Application failure → RETRY_WAIT and max attempts work; external failure has no automatic retry. **(−7)**  
- **Idempotency / completion:** job_complete idempotent; single output path. **(+5)**  
- **Multi-tenant:** User/workbox paths tenant-scoped; internal/global jobs and metrics not tenant-filtered; dlq-mark-dead depends on API lockdown. **(small deduction already in risks)**  

### Maturity: **Pre-production**

- **Experimental:** Would imply no production use; code is structured for production.  
- **Pre-production:** Fits: core encode path works; completion and retry (app failure) are implemented; but Batch lifecycle is not synced, and infrastructure failure (Spot, kill, timeout) has no automated recovery.  
- **Production-capable:** Would require at least: RUNNING/heartbeat set or an equivalent sync, and one of SIGTERM handler or describe-jobs reconciliation for failed Batch jobs.  
- **Enterprise-grade:** Would require full lifecycle sync, tenant-scoped background jobs where appropriate, and documented runbooks.  

**Conclusion:** **Pre-production.** Not production-capable until the identified gaps (RUNNING/heartbeat or sync, and infrastructure-failure recovery) are addressed with code and/or operational procedures.

---

## Summary of critical gaps

1. **DB never set to RUNNING** — Batch worker does not call `job_set_running()`; heartbeat never updated.  
2. **No Batch→DB reconciliation** — describe-jobs is used only for validation/diagnostics; no process updates DB from Batch status or resubmits on FAILED.  
3. **No SIGTERM / Spot handling** — Container kill (Spot, timeout, instance refresh) leaves DB as QUEUED; no automatic recovery.  
4. **Stuck scanner inapplicable** — Selects RUNNING + stale heartbeat; no Batch job satisfies this.  
5. **Internal scan-stuck API** — Does not call `submit_batch_job` after moving to RETRY_WAIT (management command does).  
6. **Global job scans** — scan_stuck, validate_batch_video_system, backlog count/score have no tenant_id filter (by design for metrics/stuck; dlq-mark-dead needs strict internal API access control).

All findings above are tied to specific files and lines; no speculation.
