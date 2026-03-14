# API Deploy + Worker Verification Report

**Generated:** 2026-03-11 15:13:07
**SSOT Version:** V1.0.0
**Verdict:** FAIL (PASS=41, WARN=8, FAIL=1)

---

| Stage | Item | Status | Detail |
|-------|------|--------|--------|
| 0-IMAGE | git/HEAD | **PASS** | f8b9d8f (f8b9d8f5990d4c1d0d973be2dac329aac6a32ca2) |
| 0-IMAGE | ci/last-success | **PASS** | c033877 (run #22938312777) |
| 0-IMAGE | ci/HEAD-sync | **FAIL** | HEAD=f8b9d8f != lastBuild=c033877 (no build running!) |
| 0-IMAGE | ci-report/sha | **PASS** | 931b16f (5 images) |
| 0-IMAGE | ecr/academy-api | **WARN** | digest differs from CI report — newer image pushed after report (pushed 03/11/2026 14:33:36) |
| 0-IMAGE | ecr/academy-video-worker | **WARN** | digest differs from CI report — newer image pushed after report (pushed 03/11/2026 14:39:40) |
| 0-IMAGE | ecr/academy-messaging-worker | **WARN** | digest differs from CI report — newer image pushed after report (pushed 03/11/2026 14:40:38) |
| 0-IMAGE | ecr/academy-ai-worker-cpu | **WARN** | digest differs from CI report — newer image pushed after report (pushed 03/11/2026 14:57:53) |
| 0-IMAGE | ecr/academy-base | **WARN** | digest differs from CI report — newer image pushed after report (pushed 03/11/2026 14:30:51) |
| 0-IMAGE | freshness | **WARN** | ECR is 4 commit(s) behind HEAD |
| 1-REFRESH | refresh-status | **PASS** | InProgress (50%) |
| 2-HEALTH | /healthz | **PASS** | 200 OK (0s) |
| 2-HEALTH | /health | **PASS** | 200 OK (737ms) |
| 3-ASG | api-asg | **PASS** | InService=1 >= min=1 (Desired=1, Max=2) |
| 3-ASG |   i-04b13dbf561c21c4c | **PASS** | Healthy/InService |
| 3-ASG |   i-0845e0cdac30170fd | **WARN** | Healthy/Terminating |
| 4-WORKERS | messaging-asg | **PASS** | InService=1/Desired=1 (idle=0 정상) |
| 4-WORKERS | ai-asg | **PASS** | InService=1/Desired=1 (idle=0 정상) |
| 5-SQS | messaging-main | **PASS** | Visible=0, InFlight=0, VisTimeout=900s |
| 5-SQS | messaging-dlq | **PASS** | DLQ=0 |
| 5-SQS | ai-main | **PASS** | Visible=0, InFlight=0, VisTimeout=1800s |
| 5-SQS | ai-dlq | **PASS** | DLQ=0 |
| 6-BATCH | ssm/VIDEO_BATCH_JOB_DEFINITION_LONG | **PASS** | academy-v1-video-batch-long-jobdef |
| 6-BATCH | ssm/VIDEO_BATCH_JOB_DEFINITION | **PASS** | academy-v1-video-batch-jobdef |
| 6-BATCH | ssm/VIDEO_BATCH_JOB_QUEUE_LONG | **PASS** | academy-v1-video-batch-long-queue |
| 6-BATCH | ssm/VIDEO_BATCH_JOB_QUEUE | **PASS** | academy-v1-video-batch-queue |
| 6-BATCH | ssm/REDIS_HOST | **PASS** | academy-v1-redis.prqwaq.ng.0001.apn2.cache.amazonaws.com |
| 6-BATCH | queue/standard | **PASS** | ENABLED/VALID |
| 6-BATCH | queue/long | **PASS** | ENABLED/VALID |
| 6-BATCH | queue/ops | **PASS** | ENABLED/VALID |
| 6-BATCH | ce/standard | **PASS** | ENABLED/VALID |
| 6-BATCH | ce/long | **PASS** | ENABLED/VALID |
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
| 8-EVENTS | enqueue-uploaded | **WARN** | eventbridge-enqueue-uploaded. ExitCode=254. Output: An error occurred (ResourceNotFoundException) when calling the DescribeRule operation: Rule academy-v1-enqueue-uploaded-videos does not exist on EventBus default. |
---

**SSOT Reference:** `docs/00-SSOT/v1/DEPLOY-VERIFICATION-SSOT.md` (V1.0.0)
