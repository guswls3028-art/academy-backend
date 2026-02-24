# SSM Parameter /academy/workers/env — JSON Schema (Source of Truth)

## Format

- **Type:** SecureString
- **Value:** Single-line JSON object (no legacy KEY=VALUE lines).
- **Produced by:** `scripts/infra/ssm_bootstrap_video_worker.ps1` from `.env`. No manual editing.

## Required keys

All Batch jobs (worker, netprobe, reconcile, scan_stuck) require these keys to be present and non-empty:

| Key | Description |
|-----|-------------|
| `AWS_DEFAULT_REGION` | e.g. `ap-northeast-2` |
| `DB_HOST` | RDS endpoint hostname |
| `DB_NAME` | Database name |
| `DB_USER` | Database user |
| `DB_PASSWORD` | Database password |
| `DB_PORT` | e.g. `5432` |
| `R2_ACCESS_KEY` | R2 access key |
| `R2_SECRET_KEY` | R2 secret key |
| `R2_ENDPOINT` | R2 endpoint URL |
| `R2_VIDEO_BUCKET` | R2 bucket for video |
| `API_BASE_URL` | API base URL (no trailing slash) |
| `INTERNAL_WORKER_TOKEN` | Shared secret for internal API |
| `REDIS_HOST` | Redis hostname |
| `REDIS_PORT` | e.g. `6379` |
| `DJANGO_SETTINGS_MODULE` | Must be `apps.api.config.settings.worker` for Batch |

## Optional keys

- `REDIS_PASSWORD`, `R2_PUBLIC_BASE_URL`, `R2_PREFIX`, `VIDEO_BATCH_JOB_QUEUE`, `VIDEO_BATCH_JOB_DEFINITION`

## Runtime contract

- **batch_entrypoint.py** reads this parameter, parses JSON, sets `os.environ`, and validates required keys.
- If the value is not valid JSON or any required key is missing/empty, the entrypoint exits non-zero (no fallback).
- `DJANGO_SETTINGS_MODULE` must be exactly `apps.api.config.settings.worker` for Batch; dev/prod defaults are not allowed.
