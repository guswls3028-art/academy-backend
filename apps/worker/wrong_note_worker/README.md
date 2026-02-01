# PATH: apps/worker/wrong_note_worker/README.md
Wrong Note PDF Worker (HTTP polling)

Required env:
- API_BASE_URL=https://your-api
- WORKER_TOKEN=... (must match settings.INTERNAL_WORKER_TOKEN or settings.WORKER_TOKEN)
- WORKER_ID=wrong-note-worker-1

Optional:
- POLL_INTERVAL_SECONDS=2.0
- HTTP_TIMEOUT_SECONDS=30.0
- RETRY_MAX_ATTEMPTS=5
- BACKOFF_BASE_SECONDS=1.5
- PDF_MAX_ITEMS=200

Run:
  python -m apps.worker.wrong_note_worker.run
