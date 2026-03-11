# API Deploy + Worker Verification Report

**Generated:** 2026-03-11 13:53:52
**SSOT Version:** V1.0.0
**Verdict:** FAIL (PASS=41, WARN=7, FAIL=1)

---

| Stage | Item | Status | Detail |
|-------|------|--------|--------|
| 0-IMAGE | git/HEAD | **PASS** | dc509f1 (dc509f16e5f6d025d93ff6d09847249a2f5d8e16) |
| 0-IMAGE | ci/last-success | **PASS** | 931b16f (run #22936533296) |
| 0-IMAGE | ci/HEAD-sync | **FAIL** | HEAD=dc509f1 != lastBuild=931b16f (no build running!) |
| 0-IMAGE | ci-report/sha | **PASS** | 8e8e765 (5 images) |
| 0-IMAGE | ecr/academy-api | **WARN** | digest differs from CI report — newer image pushed after report (pushed 03/11/2026 13:22:16) |
| 0-IMAGE | ecr/academy-video-worker | **WARN** | digest differs from CI report — newer image pushed after report (pushed 03/11/2026 13:28:17) |
| 0-IMAGE | ecr/academy-messaging-worker | **WARN** | digest differs from CI report — newer image pushed after report (pushed 03/11/2026 13:29:21) |
| 0-IMAGE | ecr/academy-ai-worker-cpu | **WARN** | digest differs from CI report — newer image pushed after report (pushed 03/11/2026 13:46:10) |
| 0-IMAGE | ecr/academy-base | **WARN** | digest differs from CI report — newer image pushed after report (pushed 03/11/2026 13:19:33) |
| 0-IMAGE | freshness | **WARN** | ECR is 3 commit(s) behind HEAD |
| 1-REFRESH | refresh-status | **PASS** | Successful (100%) |
| 2-HEALTH | /healthz | **PASS** | 200 OK (0s) |
| 2-HEALTH | /health | **PASS** | 200 OK (779ms) |
| 3-ASG | api-asg | **PASS** | InService=1 >= min=1 (Desired=1, Max=2) |
| 3-ASG |   i-0d55af8e5ce613e1c | **PASS** | Healthy/InService |
| 4-WORKERS | messaging-asg | **PASS** | InService=1/Desired=1 (idle=0 정상) |
| 4-WORKERS | ai-asg | **PASS** | InService=1/Desired=1 (idle=0 정상) |
| 5-SQS | messaging-main | **PASS** | Visible=0, InFlight=0, VisTimeout=900s |
| 5-SQS | messaging-dlq | **PASS** | DLQ=0 |
| 5-SQS | ai-main | **PASS** | Visible=0, InFlight=0, VisTimeout=1800s |
| 5-SQS | ai-dlq | **PASS** | DLQ=0 |
| 6-BATCH | ssm/VIDEO_BATCH_JOB_DEFINITION | **PASS** | academy-v1-video-batch-jobdef |
| 6-BATCH | ssm/VIDEO_BATCH_JOB_DEFINITION_LONG | **PASS** | academy-v1-video-batch-long-jobdef |
| 6-BATCH | ssm/VIDEO_BATCH_JOB_QUEUE | **PASS** | academy-v1-video-batch-queue |
| 6-BATCH | ssm/VIDEO_BATCH_JOB_QUEUE_LONG | **PASS** | academy-v1-video-batch-long-queue |
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
