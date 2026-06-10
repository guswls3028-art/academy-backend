# API Deploy + Worker Verification Report

**Generated:** 2026-06-11 04:54:00
**SSOT:** docs/ssot/params.yaml
**Verdict:** PASS (PASS=47, WARN=0, FAIL=0)

---

| Stage | Item | Status | Detail |
|-------|------|--------|--------|
| 0-IMAGE | git/HEAD | **PASS** | d58dfae (d58dfae424e4bc800144225dad69b6253c1d79d8) |
| 0-IMAGE | ci/last-success | **PASS** | d58dfae (run #27302116715) |
| 0-IMAGE | ci/HEAD-sync | **PASS** | HEAD matches last successful build |
| 0-IMAGE | ci-report/sha | **PASS** | b7d93df (0 images) |
| 0-IMAGE | ecr/academy-api | **PASS** | sha256:3f07296081c1... (pushed 06/11/2026 04:03:38) |
| 0-IMAGE | ecr/academy-video-worker | **PASS** | sha256:4d7f82ecf7ea... (pushed 06/11/2026 04:10:54) |
| 0-IMAGE | ecr/academy-messaging-worker | **PASS** | sha256:163006b05a07... (pushed 06/11/2026 04:11:40) |
| 0-IMAGE | ecr/academy-ai-worker-cpu | **PASS** | sha256:a0e2adb36e6a... (pushed 06/11/2026 04:12:26) |
| 0-IMAGE | ecr/academy-base | **PASS** | sha256:af7c49f6c07f... (pushed 05/20/2026 14:52:40) |
| 0-IMAGE | freshness | **PASS** | Runtime images match CI build report; later HEAD changes do not affect images |
| 1-REFRESH | refresh-status | **PASS** | Successful (100%) |
| 2-HEALTH | /healthz | **PASS** | 200 OK (0s) |
| 2-HEALTH | /health | **PASS** | 200 OK (599ms) |
| 3-ASG | api-asg | **PASS** | InService=2 >= min=2 (Desired=2, Max=3) |
| 3-ASG |   i-080bdfb3b81cfa911 | **PASS** | Healthy/InService |
| 3-ASG |   i-0a18335632700a499 | **PASS** | Healthy/InService |
| 3.5-MIGRATE | showmigrations | **PASS** | no pending migrations |
| 4-WORKERS | messaging-asg | **PASS** | InService=1/Desired=1 (idle=0 정상) |
| 4-WORKERS | ai-asg | **PASS** | InService=0/Desired=0 (idle=0 정상) |
| 5-SQS | messaging-main | **PASS** | Visible=0, InFlight=0, VisTimeout=900s |
| 5-SQS | messaging-dlq | **PASS** | DLQ=0 |
| 5-SQS | ai-main | **PASS** | Visible=0, InFlight=0, VisTimeout=1800s |
| 5-SQS | ai-dlq | **PASS** | DLQ=0 |
| 6-BATCH | ssm/VIDEO_BATCH_JOB_DEFINITION | **PASS** | academy-v1-video-batch-jobdef |
| 6-BATCH | ssm/VIDEO_BATCH_JOB_QUEUE | **PASS** | academy-v1-video-batch-queue |
| 6-BATCH | ssm/REDIS_HOST | **PASS** | academy-v1-redis.prqwaq.ng.0001.apn2.cache.amazonaws.com |
| 6-BATCH | queue/standard | **PASS** | ENABLED/VALID |
| 6-BATCH | queue/ops | **PASS** | ENABLED/VALID |
| 6-BATCH | ce/standard | **PASS** | ENABLED/VALID |
| 6-BATCH | ce/ops | **PASS** | ENABLED/VALID |
| 7-WENV | workers/DB_HOST | **PASS** | academy-db.cbm4oqigwl80.ap-northeast-2.rds.amazonaws.com |
| 7-WENV | workers/DB_NAME | **PASS** | postgres |
| 7-WENV | workers/DB_USER | **PASS** | admin97 |
| 7-WENV | workers/DB_PASSWORD | **PASS** | *** |
| 7-WENV | workers/DB_PORT | **PASS** | 5432 |
| 7-WENV | workers/REDIS_HOST | **PASS** | academy-v1-redis.prqwaq.ng.0001.apn2.cache.amazonaws.com |
| 7-WENV | workers/REDIS_PORT | **PASS** | 6379 |
| 7-WENV | workers/R2_ACCESS_KEY | **PASS** | *** |
| 7-WENV | workers/R2_SECRET_KEY | **PASS** | *** |
| 7-WENV | workers/R2_ENDPOINT | **PASS** | https://af4f2937d73db240e99864b8518265c5.r2.cloudflarestorage.com |
| 7-WENV | workers/API_BASE_URL | **PASS** | http://academy-v1-api-alb-1244943981.ap-northeast-2.elb.amazonaws.com |
| 7-WENV | workers/INTERNAL_WORKER_TOKEN | **PASS** | *** |
| 7-WENV | workers/DJANGO_SETTINGS_MODULE | **PASS** | apps.api.config.settings.worker |
| 7-WENV | workers/MESSAGING_SQS_QUEUE_NAME | **PASS** | academy-v1-messaging-queue |
| 8-EVENTS | reconcile | **PASS** | ENABLED — rate(1 hour) |
| 8-EVENTS | scan-stuck | **PASS** | ENABLED — rate(1 hour) |
| 8-EVENTS | enqueue-uploaded | **PASS** | ENABLED — rate(10 minutes) |
---

**SSOT Reference:** `docs/ssot/params.yaml`; architecture context: `docs/infrastructure/deployment-architecture.md`
