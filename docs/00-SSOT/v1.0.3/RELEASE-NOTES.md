# V1.0.3 Release Notes — Video Infrastructure Hardening

**Date:** 2026-03-13
**Type:** System Hardening / Stability
**Status:** SEALED

---

## Overview

V1.0.3 is a comprehensive stability release targeting the video processing infrastructure.
All changes address real failure modes observed in production (tenant 2, video 240 incident).

---

## Changes

### 1. Daemon Worker — Concurrent-Safe Polling

**File:** `apps/worker/video_worker/daemon_main.py`

- `poll_next_job()` now uses `select_for_update(skip_locked=True)` inside a transaction
- Multiple daemon instances can safely run without duplicate job pickup
- Previously: first() without lock could cause two daemons to grab the same job (mitigated by job_set_running CAS, but wasteful)

### 2. Long Video Auto-Routing (Daemon → Batch Fallback)

**File:** `apps/support/video/services/video_encoding.py`

- In daemon mode, videos with `duration > DAEMON_MAX_DURATION_SECONDS` (default 1800s = 30min) are automatically routed to AWS Batch
- Prevents long videos from being stuck in QUEUED forever (daemon filters ≤30min)
- Videos with unknown duration (NULL) go to daemon (worker re-validates with ffprobe)
- New setting: `DAEMON_MAX_DURATION_SECONDS` in `base.py`

### 3. Stuck PENDING Video Recovery

**New file:** `apps/support/video/management/commands/recover_stuck_videos.py`

- **PENDING + file_key + stale >1h:** Transition to UPLOADED + enqueue job
- **PENDING + no file_key + stale >24h:** Mark FAILED (upload abandoned)
- Excludes videos with active jobs (QUEUED/RUNNING/RETRY_WAIT)
- Run via cron: `python manage.py recover_stuck_videos` (every 30min recommended)

### 4. FAILED Video Re-enqueue

**File:** `apps/support/video/management/commands/enqueue_uploaded_videos.py`

- New `--include-failed` flag to also re-enqueue FAILED videos with file_key
- FAILED videos are atomically reset to UPLOADED before job creation
- Use case: transient failures (R2 timeout, ffmpeg crash) that can succeed on retry

### 5. Daemon-Aware Stuck Scanner

**File:** `apps/support/video/management/commands/scan_stuck_video_jobs.py`

- In daemon mode, stuck short videos transition to RETRY_WAIT without Batch resubmit
- Daemon polls RETRY_WAIT jobs natively — Batch submit is unnecessary overhead
- Long videos (>30min) still get Batch resubmit even in daemon mode

---

## Root Cause: Video 240 Incident

**Symptom:** 142-minute video stuck in UPLOADED with RUNNING job for 170+ minutes.

**Root cause:** `publish_tmp_to_final` performed 4276 sequential `copy_object` calls with zero logging. Total copy time exceeded Batch job timeout.

**Fix (deployed in V1.0.2+):** ThreadPoolExecutor(16) parallel copy + progress logging every 500 files.

**V1.0.3 prevention layers:**
1. Long videos (>30min) auto-route to Batch with longer timeout
2. `scan_stuck_video_jobs` detects stalled RUNNING jobs (heartbeat-based)
3. `recover_stuck_videos` catches PENDING videos that never completed upload
4. `enqueue_uploaded_videos --include-failed` re-enqueues transient failures
5. Daemon `select_for_update(skip_locked=True)` prevents duplicate processing

---

## Management Commands (Cron Schedule)

| Command | Frequency | Purpose |
|---------|-----------|---------|
| `scan_stuck_video_jobs` | Every 2 min | Detect stuck RUNNING jobs, retry or mark DEAD |
| `enqueue_uploaded_videos` | Every 10 min | Pick up UPLOADED videos deferred by concurrency limits |
| `recover_stuck_videos` | Every 30 min | Recover PENDING/abandoned videos |
| `enqueue_uploaded_videos --include-failed` | Every 60 min | Re-enqueue transient failures |

---

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `VIDEO_WORKER_MODE` | `daemon` | Worker mode: "daemon" (DB polling) or "batch" (AWS Batch) |
| `DAEMON_MAX_DURATION_SECONDS` | `1800` | Max video duration for daemon processing (30min) |
| `VIDEO_TENANT_MAX_CONCURRENT` | `2` | Max concurrent jobs per tenant |
| `VIDEO_GLOBAL_MAX_CONCURRENT` | `20` | Max concurrent jobs globally |
| `VIDEO_STUCK_HEARTBEAT_STANDARD_MINUTES` | `20` | Stuck threshold for standard videos |
| `VIDEO_STUCK_HEARTBEAT_LONG_MINUTES` | `45` | Stuck threshold for long videos (3h+) |

---

## Video Processing Flow (V1.0.3)

```
Upload Init (PENDING)
    │
    ▼
Upload Complete (PENDING → UPLOADED)
    │
    ├─ duration ≤ 30min ──→ Daemon polls DB (QUEUED)
    │                          │
    │                          ▼
    │                       process_video()
    │                          │
    │                          ├─ Success → SUCCEEDED + READY
    │                          └─ Failure → RETRY_WAIT (up to 5x) → DEAD + FAILED
    │
    └─ duration > 30min ──→ AWS Batch submit
                               │
                               ▼
                            batch_main.py
                               │
                               ├─ Success → SUCCEEDED + READY
                               └─ Failure → scan_stuck detects → RETRY_WAIT → Batch resubmit
```

**Recovery layers:**
- `recover_stuck_videos`: PENDING stale → UPLOADED or FAILED
- `enqueue_uploaded_videos`: UPLOADED without job → enqueue
- `scan_stuck_video_jobs`: RUNNING stale → RETRY_WAIT or DEAD
- Admin retry button: Manual re-enqueue for any status

---

## Files Changed

| File | Change |
|------|--------|
| `apps/worker/video_worker/daemon_main.py` | `select_for_update(skip_locked=True)` in poll |
| `apps/support/video/services/video_encoding.py` | Daemon→Batch auto-fallback for long videos |
| `apps/api/config/settings/base.py` | `DAEMON_MAX_DURATION_SECONDS` setting |
| `apps/support/video/management/commands/scan_stuck_video_jobs.py` | Daemon-aware retry (skip Batch for short videos) |
| `apps/support/video/management/commands/enqueue_uploaded_videos.py` | `--include-failed` flag |
| `apps/support/video/management/commands/recover_stuck_videos.py` | NEW: stuck PENDING recovery |
| `docs/00-SSOT/v1.0.3/RELEASE-NOTES.md` | This file |
| `docs/00-SSOT/v1.0.3/VIDEO-INFRASTRUCTURE.md` | Architecture document |

---

## Verification Checklist

- [ ] Daemon starts and polls successfully
- [ ] Short video (<30min) processed by daemon
- [ ] Long video (>30min) auto-routed to Batch
- [ ] `recover_stuck_videos --dry-run` lists stuck videos
- [ ] `enqueue_uploaded_videos --include-failed --dry-run` lists failed videos
- [ ] `scan_stuck_video_jobs --dry-run` lists stale RUNNING jobs
- [ ] Retry button works for FAILED videos
- [ ] healthz 200, health 200 after deploy
