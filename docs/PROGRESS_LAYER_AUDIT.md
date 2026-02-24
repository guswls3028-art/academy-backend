# Real-Time Progress Delivery — Fact-Based Audit

## PHASE A — Current mechanism

### 1. How frontend receives progress

- **Mechanism:** HTTP polling.
- **Evidence:** Single GET endpoint; no SSE/WebSocket/Channels usage in repo for video encoding progress.

### 2. Evidence

| Item | Location |
|------|----------|
| API endpoint | `GET /media/videos/{id}/progress/` (or nested under lectures/sessions). View: `VideoProgressView.get()` in `apps/support/video/views/progress_views.py`. Registered in `apps/support/video/urls.py` as `videos/<int:pk>/progress/`. |
| View class | `VideoProgressView` (APIView), `VideoProgressViewSet` (ModelViewSet for other progress CRUD — not encoding progress). |
| Channels | Not used for video encoding progress. |
| Redis pub/sub | Not used for progress. |
| Redis usage | **Status key:** `get_video_status_from_redis(tenant_id, video_id)` reads `tenant:{tenant_id}:video:{video_id}:status` (set by `cache_video_status`). **Progress key:** `get_video_encoding_progress` / `_get_progress_payload` read `tenant:{tenant_id}:video:{video_id}:progress` (set by `RedisProgressAdapter.record_progress` in worker). |
| JS client | Not searched; assumption: frontend polls the progress URL. |

### 3. Endpoint behavior and risk

| Question | Answer | Mark |
|----------|--------|------|
| Which endpoint serves progress? | `VideoProgressView.get()` — GET `videos/<pk>/progress/`. |
| Does it hit DB? | Yes. When `get_video_status_from_redis(tenant_id, video_id)` returns `None`, code runs `Video.objects.filter(pk=video_id, session__lecture__tenant_id=tenant.id).values("status", "hls_path", "duration", "error_reason").first()`. | **DB HIT RISK** |
| Does it hit Redis only? | Only when status key exists. First it reads Redis status; if miss, it falls back to DB (see above). Then if status is PROCESSING it reads Redis progress key. | **REDIS FALLBACK TO DB** |
| Fallback to DB on Redis miss? | Yes. Lines 66–89: if `cached_status is None`, query Video and return READY/FAILED from DB or default. | **REDIS FALLBACK TO DB** |

**Summary:** **DB HIT RISK** and **REDIS FALLBACK TO DB**. Progress endpoint must be refactored to never query Video/VideoTranscodeJob; on Redis miss return minimal JSON without DB.

---

## PHASE B — Redis safety (findings)

| Check | Finding |
|-------|--------|
| Key pattern for progress | `tenant:{tenant_id}:video:{video_id}:progress`. Also legacy `job:{job_id}:progress` (job_id = `video:{video_id}`). |
| TTL set? | Yes. `RedisProgressAdapter.record_progress` uses `client.setex(key, self._ttl, ...)`. Default `PROGRESS_TTL_SECONDS = 3600`; Batch uses `VIDEO_PROGRESS_TTL_SECONDS` (14400). |
| TTL refreshed? | `refresh_video_progress_ttl` exists in `redis_status_cache` but is not called from the worker/processor. Worker only sets TTL on each `record_progress`. |
| Progress key deleted on SUCCEEDED/FAILED/DEAD? | No. `job_complete` and `job_mark_dead` do not delete the progress key. Only status key is updated via `_cache_video_status_safe`. |
| Redis write failure breaks job? | No. `record_progress` wraps setex in try/except; logs warning and returns. |

Required: TTL default 24h (86400), delete progress key in `job_complete` and `job_mark_dead`, keep Redis writes non-fatal, no DB fallback on miss.

---

## PHASE C — Database protection

Progress endpoint must not query Video, VideoTranscodeJob, or any JOIN. Current code path when `cached_status is None`: queries `Video.objects.filter(...)`. Refactor: on Redis miss return `{"state": "UNKNOWN"}` (or existing default PENDING) without any DB access. Add comment: `# DO NOT ADD DB ACCESS HERE (PROGRESS ENDPOINT)`.

---

## PHASE D — Load safety

- Polling: enforce minimum interval via `Retry-After: 3` response header on progress endpoint.
- SSE/WebSocket: not in use; no change.
- READY/FAILED: frontend should stop polling when status is READY or FAILED (client responsibility); endpoint already returns status from Redis/DB; after refactor it will return UNKNOWN or status from Redis only.

---

## PHASE E — Observability

Emit: ProgressRequests (count), RedisMiss (count), ProgressEndpointDBHit (count; must remain 0 after hardening).

---

## PHASE F — Validation command

`python manage.py validate_progress_layer`: verify no DB query in progress endpoint code path, Redis progress keys have TTL (setex, 86400), `job_complete`/`job_mark_dead` delete progress key, no fallback-to-DB (return UNKNOWN on Redis miss). Exit non-zero if violation found.

**Implemented:** `apps/support/video/management/commands/validate_progress_layer.py`
