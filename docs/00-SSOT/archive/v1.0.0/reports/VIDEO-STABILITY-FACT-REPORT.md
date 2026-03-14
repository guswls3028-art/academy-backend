# Video Upload/Processing — FACT REPORT

**Generated:** 2026-03-07  
**Scope:** Repo design truth + AWS runtime truth. No speculation.

---

## A. REPO DESIGN TRUTH

### 1. Routing rule (standard vs long queue)

| Source | Rule |
|--------|------|
| `batch_submit.py` L37-45 | `duration_seconds >= VIDEO_LONG_DURATION_THRESHOLD_SECONDS` (default 10800) → long queue/JobDef. Else standard. |
| `video_encoding.py` L115 | `submit_batch_job(str(job.id), duration_seconds=video.duration if video.duration else None)` |
| **Fact** | Duration comes from `video.duration`. If `None` or `0`, standard queue is used. |

### 2. Timeout, vCPU, memory, storage

| Resource | Standard | Long |
|----------|----------|------|
| **params.yaml** | jobTimeout 21600, vCPU 2, memory 4096 (JobDef), rootVolume 200GB (CE template) | jobTimeout 43200, vCPU 2, memory 4096, rootVolume 300GB |
| **video_job_definition.json** | timeout 21600, vcpus 2, memory 4096 | — |
| **video_job_definition_long.json** | — | timeout 43200, vcpus 2, memory 4096 |
| **video_compute_env.json** | maxvCpus, instanceType from SSOT | — |
| **video_compute_env_long.json** | — | Same pattern |

### 3. Retry button visibility (frontend)

| Source | Rule |
|--------|------|
| `videoProcessing.ts` L21-26 | `VIDEO_STATUS_RETRY_ALLOWED = ["PENDING", "FAILED", "PROCESSING", "UPLOADED"]` |
| `SessionVideosTab.tsx` L221 | `isRetryAllowedByStatus(video.status)` → show retry button |
| **Fact** | Retry shown for PENDING, FAILED, PROCESSING, UPLOADED. Not for READY. |

### 4. Retry acceptance (backend)

| Source | Rule |
|--------|------|
| `video_views.py` retry | PENDING + file_key → `_upload_complete_impl` (re-run upload-complete). PENDING without file_key → 400. |
| | UPLOADED, PROCESSING, FAILED, READY → job cleanup then `create_job_and_submit_batch`. |
| | RUNNING job (no cancel_requested) → 409. QUEUED/RETRY_WAIT recent (<1h) → 400 "Already in backlog". |
| | status not in (READY, FAILED, UPLOADED, PROCESSING) → 400 "Cannot retry: status must be READY or FAILED". |
| **Fact** | Backend accepts: PENDING+file_key, UPLOADED, PROCESSING, FAILED, READY (with job cleanup). |

### 5. Video states

| State | Meaning |
|-------|---------|
| PENDING | Created by upload/init; file not yet uploaded or upload-complete not called |
| UPLOADED | upload-complete succeeded; job created and submitted to Batch |
| PROCESSING | Worker running (DB may not reflect RUNNING; Batch worker does not call job_set_running) |
| READY | Encoding complete |
| FAILED | job_mark_dead after max attempts or submit failure |

### 6. Stuck states after upload succeeds but upload-complete fails

| Scenario | Result |
|----------|--------|
| File uploaded to R2, upload-complete never called (network/timeout) | Video stays PENDING. file_key set. Raw object in R2. |
| upload-complete called, head_object fails (R2 error) | Video stays PENDING. Returns 503. |
| upload-complete called, head_object returns not exists/empty | Video stays PENDING. error_reason=source_not_found_or_empty. Returns 409. |
| upload-complete called, create_job_and_submit_batch returns None (tenant limit, DDB lock, submit error) | Video is UPLOADED but no job. Stuck. |

### 7. Distinction of failure points

| Case | Code path | Distinguishable? |
|------|-----------|------------------|
| Upload not started | No upload/init | PENDING, no file_key |
| Upload incomplete | upload/init done, PUT not done | PENDING, file_key, R2 object may not exist |
| Upload complete callback failed | upload/complete 503/409/timeout | PENDING, file_key, R2 object may exist |
| Batch submit failed | create_job_and_submit_batch returns None | UPLOADED, current_job_id may be null or job has no aws_batch_job_id |
| Processing failed | Worker job_fail_retry → RETRY_WAIT, eventually job_mark_dead → FAILED | FAILED |

### 8. Minimal recovery paths

| Path | Exists? |
|------|---------|
| Retry PENDING+file_key | Yes. Retry calls _upload_complete_impl. |
| Retry FAILED/UPLOADED/PROCESSING | Yes. Job cleanup + create_job_and_submit_batch. |
| Reconcile (Batch→DB sync) | Yes. EventBridge 15min. reconcile_batch_video_jobs. |
| Scan-stuck (RETRY_WAIT resubmit) | Yes. EventBridge 5min. scan_stuck_video_jobs. |

### 9. EventBridge / Ops / reconcile

| Component | Schedule | Purpose |
|-----------|----------|---------|
| reconcile | rate(15 min) | Batch→DB sync; FAILED/not_found resubmit (optional) |
| scan-stuck | rate(5 min) | RETRY_WAIT jobs → resubmit |
| **Required for minimal?** | Reconcile: yes (orphan detection, Batch status sync). Scan-stuck: yes (retry stuck RETRY_WAIT). |

---

## B. RUNTIME TRUTH (AWS / Deployed)

### 1. Standard CE/Queue/JobDef

| Resource | State |
|----------|-------|
| academy-v1-video-batch-ce | ENABLED, VALID, maxvCpus=40, instanceTypes=[c6g.xlarge] |
| academy-v1-video-batch-queue | ENABLED, VALID |
| academy-v1-video-batch-jobdef | rev 20, vcpus=2, memory=4096, timeout=21600 |

### 2. Long CE/Queue/JobDef

| Resource | State |
|----------|-------|
| academy-v1-video-batch-long-ce | ENABLED, VALID, maxvCpus=80, instanceTypes=[c6g.xlarge] |
| academy-v1-video-batch-long-queue | ENABLED, VALID |
| academy-v1-video-batch-long-jobdef | rev 1, vcpus=2, memory=4096, timeout=43200 |

### 3. Job definition timeout and resources

| JobDef | timeout | vcpus | memory |
|--------|---------|-------|--------|
| standard | 21600 (6h) | 2 | 4096 |
| long | 43200 (12h) | 2 | 4096 |

### 4. SSM /academy/api/env (video-related)

| Key | Value |
|-----|-------|
| VIDEO_BATCH_JOB_QUEUE | academy-v1-video-batch-queue |
| VIDEO_BATCH_JOB_DEFINITION | academy-v1-video-batch-jobdef |
| VIDEO_BATCH_JOB_QUEUE_LONG | academy-v1-video-batch-long-queue |
| VIDEO_BATCH_JOB_DEFINITION_LONG | academy-v1-video-batch-long-jobdef |
| VIDEO_TENANT_MAX_CONCURRENT | 6 |
| VIDEO_LONG_DURATION_THRESHOLD_SECONDS | (not in SSM; defaults to 10800 in base.py) |

### 5. Recent long-queue jobs

| Status | Count |
|--------|-------|
| RUNNABLE | 0 |
| SUCCEEDED | 0 |
| FAILED | 0 |

*(No recent jobs in long queue. Cannot confirm long videos were routed there.)*

### 6–10. Problematic videos (4444, 6666)

**Cannot verify from repo/AWS alone.** Requires:
- DB: Video status, file_key, current_job_id
- R2: head_object(file_key) for raw object existence
- Batch: list-jobs by job name pattern for video-* jobs

**Inferred from prior context:** 4444, 6666 showed no retry button → status was PENDING. Frontend previously did not show retry for PENDING; code was changed to add PENDING to VIDEO_STATUS_RETRY_ALLOWED. Retry for PENDING+file_key calls _upload_complete_impl.

---

## C. IDENTIFIED GAPS (from facts)

1. **Duration unknown at submit:** If ffprobe fails or duration is 0/None, video routes to standard queue. For 3h+ videos, standard timeout (6h) may suffice, but 4h+ could risk timeout.
2. **PENDING without file_key:** Retry returns 400 "업로드가 완료되지 않았습니다." Frontend shows retry for all PENDING (including no file_key). User gets error on click.
3. **PENDING+file_key but R2 object missing:** Retry runs _upload_complete_impl → head_object fails → 503 or 409. No explicit "delete and re-upload" message.
4. **create_job_and_submit_batch returns None:** Video stays UPLOADED with no active job. Retry would create new job. If tenant limit exceeded, retry also returns None → ValidationError "비디오 작업 등록 실패."

---

## D. MINIMAL STABILITY TARGET

1. **Standard path:** Short/normal videos (duration < 10800s or unknown).
2. **Long path:** Videos with duration ≥ 10800s.
3. **Retry supports:**
   - PENDING + file_key + raw exists → re-run upload-complete.
   - PENDING + file_key + raw missing → clear "delete and re-upload" message.
   - PENDING + no file_key → clear "upload first" message; hide retry button.
   - FAILED/UPLOADED/PROCESSING → re-submit if safe.
   - Impossible cases → clear message, require delete/re-upload.
4. **Frontend:** Retry only when backend can act. PENDING without file_key → no retry button.
5. **No indefinite stuck:** Reconcile + scan-stuck remain. No new control plane.

---

## E. FILES CHANGED

| File | Change |
|------|--------|
| `academyfront/src/features/videos/constants/videoProcessing.ts` | Added `canShowRetryButton(video)` — retry only when backend can act; PENDING requires file_key |
| `academyfront/src/features/lectures/components/SessionVideosTab.tsx` | Use `canShowRetryButton` instead of `isRetryAllowedByStatus` |
| `academyfront/src/features/videos/pages/VideoExplorerPage.tsx` | Use `canShowRetryButton` instead of `isRetryAllowedByStatus` |
| `academyfront/src/features/videos/pages/VideoDetailPage.tsx` | Use `canShowRetryButton` instead of `isRetryAllowedByStatus` |
| `academy/apps/support/video/services/batch_submit.py` | Added `BATCH_SUBMIT_ROUTE` log (duration, threshold, use_long) for debuggability |

## F. EXACT LOGIC CHANGED

1. **canShowRetryButton:** PENDING → retry only if `file_key` exists and non-empty. Other statuses unchanged.
2. **batch_submit:** Log routing decision (duration_sec, threshold, use_long) before queue selection.

## G. INFRA / SSM / BATCH CHANGES

None. All verified present: Long CE/Queue/JobDef, SSM VIDEO_BATCH_*_LONG, VIDEO_TENANT_MAX_CONCURRENT=6.

## H. VERIFIED RETRY RULES

| State | file_key | Retry button | Backend action |
|-------|----------|--------------|----------------|
| PENDING | yes | ✅ | _upload_complete_impl |
| PENDING | no | ❌ | — |
| UPLOADED | — | ✅ | create_job_and_submit_batch |
| PROCESSING | — | ✅ | job cleanup + create_job_and_submit_batch |
| FAILED | — | ✅ | job cleanup + create_job_and_submit_batch |
| READY | — | ❌ (not in RETRY_ALLOWED) | — |

## I. VERIFIED LONG-VIDEO ROUTING RULE

- `duration_seconds >= 10800` → long queue + long JobDef.
- `duration_seconds` from `video.duration` at submit time.
- If `None` or `0` → standard queue.

## J. VERIFICATION STEPS (manual)

1. **Long routing:** Upload 3h video → check CloudWatch logs for `BATCH_SUBMIT_ROUTE | use_long=true`.
2. **Retry PENDING+file_key:** For stuck PENDING with file_key, click retry → should re-run upload-complete.
3. **Retry PENDING no file_key:** Retry button should not appear.
4. **Retry FAILED:** Click retry → new job submitted.
5. **Short video:** Still works (standard queue).

## K. FINAL STATUS

**MINIMAL_VIDEO_STABILITY_IMPROVED**

- Retry button aligned with backend eligibility.
- PENDING without file_key no longer shows retry.
- Routing log added for long-video debugging.
- No new infra. Philosophy preserved.
