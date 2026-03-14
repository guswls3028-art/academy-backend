# Video Infrastructure Architecture — V1.0.3

## Worker Modes

### Daemon Mode (Default, `VIDEO_WORKER_MODE=daemon`)

Long-running process that polls PostgreSQL for QUEUED/RETRY_WAIT jobs.

**Handles:** Videos with duration ≤ 30 minutes (or unknown duration).

```
daemon_main.py
├── poll_next_job() — select_for_update(skip_locked=True)
│   ├── state: QUEUED or RETRY_WAIT
│   ├── duration: NULL or ≤ 1800s
│   └── order_by: created_at (FIFO)
├── process_one_job()
│   ├── Idempotency checks (already SUCCEEDED? already READY?)
│   ├── job_set_running() — CAS state transition
│   ├── Heartbeat thread (60s interval, extends DDB lock)
│   ├── process_video() — 7-step pipeline (ffprobe, transcode, upload, verify, publish)
│   ├── job_complete() — SUCCEEDED + READY in one transaction
│   └── R2 raw file cleanup
├── Connection verification (DB + Redis, every 5 min)
├── Idle backoff (5s → 30s when no jobs)
└── Graceful shutdown (SIGTERM → finish current job)
```

### Batch Mode (`VIDEO_WORKER_MODE=batch`)

1-shot AWS Batch container per job. Used for long videos (>30min) even in daemon mode.

**Auto-routing in daemon mode:**
- `create_job_and_submit_batch()` checks `video.duration > DAEMON_MAX_DURATION_SECONDS`
- If true → submits to AWS Batch regardless of `VIDEO_WORKER_MODE`
- Batch job runs `batch_main.py` with job_id from SSM environment

## State Machine

### Video Status

```
PENDING ──upload_complete──→ UPLOADED ──create_job──→ (job QUEUED)
                                                           │
                                                    daemon/batch picks up
                                                           │
                                                           ▼
                                                    PROCESSING (via mark_processing)
                                                           │
                                                    ┌──────┴──────┐
                                                    ▼             ▼
                                                  READY        FAILED
                                              (hls_path set)  (error_reason set)
```

### VideoTranscodeJob State

```
QUEUED ──worker claims──→ RUNNING ──success──→ SUCCEEDED
  │                         │
  │                    failure (< max_attempts)
  │                         │
  │                         ▼
  └──────────────── RETRY_WAIT
                         │
                    failure (>= max_attempts)
                         │
                         ▼
                       DEAD ──→ Video.status = FAILED

Also: CANCELLED (user cancel via retry endpoint)
```

## Failure Recovery Layers

### Layer 1: Worker Internal Retry
- `daemon_main.py`: On exception → `job_fail_retry()` → RETRY_WAIT
- Max attempts: `VIDEO_JOB_MAX_ATTEMPTS` (default 5)
- After max: `job_mark_dead()` → DEAD + Video FAILED

### Layer 2: Stuck Job Scanner (`scan_stuck_video_jobs`)
- Runs every 2 minutes via cron
- Detects RUNNING jobs with no heartbeat for >20min (standard) or >45min (long videos)
- Actions: RETRY_WAIT (with Batch resubmit for long videos) or DEAD (max attempts)

### Layer 3: Upload Recovery (`enqueue_uploaded_videos`)
- Runs every 10 minutes via cron
- Picks up UPLOADED videos deferred by concurrency limits
- With `--include-failed`: re-enqueues FAILED videos with file_key

### Layer 4: PENDING Recovery (`recover_stuck_videos`)
- Runs every 30 minutes via cron
- PENDING + file_key + stale >1h → UPLOADED + enqueue
- PENDING + no file_key + stale >24h → FAILED (abandoned)

### Layer 5: Manual Retry (Admin UI)
- `POST /api/v1/media/videos/{id}/retry/`
- Works for PENDING/UPLOADED/FAILED/PROCESSING/READY
- Handles stale RUNNING detection (30min heartbeat threshold)
- Race condition safe (DDB lock + DB unique constraint)

## Concurrency Protection

| Mechanism | Scope | Purpose |
|-----------|-------|---------|
| DynamoDB lock | Per-video | 1 video → 1 active job guarantee |
| DB unique constraint | Per-video | `unique_video_active_job` (QUEUED/RUNNING/RETRY_WAIT) |
| `select_for_update(skip_locked=True)` | Daemon polling | Multi-daemon safety |
| `job_set_running()` CAS | Per-job | Atomic QUEUED→RUNNING transition |
| Tenant limit | Per-tenant | Max 2 concurrent jobs per tenant |
| Global limit | System-wide | Max 20 concurrent jobs total |

## R2 Upload Pipeline

```
1. upload_directory() → local files → R2 tmp prefix (parallel, 8MB multipart)
2. verify_hls_integrity_r2() → master.m3u8 + variant playlists + segment HEAD checks
3. publish_tmp_to_final() → R2 copy tmp → final (ThreadPoolExecutor, 16 workers)
4. delete_prefix() → clean tmp prefix
```

**Progress logging:** Every 500 files for both upload and publish operations.

## Key Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `VIDEO_WORKER_MODE` | `daemon` | Worker mode |
| `DAEMON_MAX_DURATION_SECONDS` | `1800` | Max duration for daemon (30min) |
| `VIDEO_JOB_MAX_ATTEMPTS` | `5` | Max retry attempts before DEAD |
| `VIDEO_JOB_HEARTBEAT_SECONDS` | `60` | Heartbeat interval |
| `VIDEO_JOB_LOCK_TTL_SECONDS` | `43200` | DDB lock TTL (12h) |
| `VIDEO_STUCK_HEARTBEAT_STANDARD_MINUTES` | `20` | Stuck threshold (standard) |
| `VIDEO_STUCK_HEARTBEAT_LONG_MINUTES` | `45` | Stuck threshold (long) |
| `VIDEO_RETRY_STALE_RUNNING_MINUTES` | `30` | Stale RUNNING for retry UI |
| `DAEMON_POLL_INTERVAL_SECONDS` | `5` | Base poll interval |
| `DAEMON_POLL_MAX_INTERVAL_SECONDS` | `30` | Max poll interval (idle backoff) |
| `DAEMON_HEALTH_CHECK_INTERVAL_SECONDS` | `300` | Health check interval (5min) |
