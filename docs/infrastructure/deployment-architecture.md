# V1.1.0 Deployment Architecture

**Version:** V1.1.0
**Date:** 2026-03-14 (checked 2026-05-25)
**SSOT Status:** Active

## 1. Service Decomposition

| Service | ECR Repository | ASG | Container Name | Purpose |
|---------|---------------|-----|----------------|---------|
| API | academy-api | academy-v1-api-asg | academy-api | Django REST API (Gunicorn) |
| Messaging Worker | academy-messaging-worker | academy-v1-messaging-worker-asg | academy-messaging-worker | SQS message processing |
| AI Worker | academy-ai-worker-cpu | academy-v1-ai-worker-asg | academy-ai-worker-cpu | AI task processing |
| Video Worker | academy-video-worker | AWS Batch CE (`academy-v1-video-batch-ce-200gb`, c6g.4xlarge primary) | — | 영상 인코딩. 1 video = 1 Batch job. VCPU=8 / MEM=16GB / timeout=6h |
| Base | academy-base | — | — | Shared base image for all services |

**Note (2026-05-10, checked 2026-05-22):** Daemon mode 폐기. 모든 영상은 AWS Batch로 1-shot 처리. long path도 폐기되어 단일 queue/jobdef만 사용한다. 현재 jobdef timeout은 6h이며, 실패/중단 케이스는 reconcile/scan_stuck이 재시도한다. ffmpeg는 `c6g.4xlarge` VCPU=8 + R2 병렬 업로드(16 worker)로 처리.

## 2. CI/CD Pipeline Architecture

```
git push main
    |
    v
[detect-changes] ─── analyze git diff ──> outputs: build_api, build_video,
    |                                               build_messaging, build_ai, force_full
    v
[run-lint] ─── ruff deploy gate
    |
    v
[run-tests] ─── smoke tests deploy gate
    |
    v
[build-and-push] ─── build changed images ──> ECR (:latest + :sha-XXXXXXXX)
    |
    |── (if API changed) ──> [run-migrations] ─── pull new SHA image ──> docker run manage.py migrate
    |                              |
    |                              v
    |── (if API changed) ──> [deploy-api] ─── ASG instance refresh (MinHealthy=100%, Warmup=120s)
    |
    |── (if messaging changed) ──> [deploy-messaging] ─── ASG instance refresh (MinHealthy=0%, Warmup=120s)
    |
    |── (if AI changed) ──> [deploy-ai] ─── ASG instance refresh (MinHealthy=0%, Warmup=120s)
    |
    |── (if video changed) ──> [deploy-video] ─── Batch job definition revisions with SHA image
    |
    v
[verify-deployment] ─── healthz 200 + health 200 + ASG healthy instances ──> PASS/FAIL
    |
    v
[notify-on-failure] ─── failure-only notification
```

## 3. Selective Build Logic

### Change Detection Rules

| Trigger Files | Builds |
|--------------|--------|
| `docker/Dockerfile.base`, `requirements/common.txt`, `requirements/requirements.txt`, `libs/`, `academy/` | ALL images (force_full) |
| `apps/`, `docker/api/`, `requirements/api.txt` | API |
| `apps/worker/video_worker/`, `apps/support/video/`, `apps/domains/video/`, `apps/api/config/settings/worker.py`, `docker/video-worker/`, `requirements/worker-video.txt` | Video Worker |
| `apps/worker/messaging_worker/`, `apps/support/messaging/`, `apps/domains/messaging/`, `apps/api/config/settings/worker.py`, `docker/messaging-worker/`, `requirements/worker-messaging.txt` | Messaging Worker |
| `apps/worker/ai_worker/`, `apps/worker/omr/`, `apps/domains/`, `apps/support/ai/`, `apps/api/config/settings/(worker|base).py`, `academy/`, `libs/queue/`, `docker/ai-worker*`, `requirements/worker-ai*` | AI Worker |

Base image build is CONDITIONAL — triggered only when `docker/Dockerfile.base`, `requirements/common.txt`, or `libs/` files change (or on workflow_dispatch). Conditional builds also apply to service-specific images.

### Build Output

Each image is tagged with:
- `:latest` — mutable, always points to most recent build
- `:sha-XXXXXXXX` — immutable, first 8 chars of git commit SHA

## 4. Selective Deploy Logic

Deploy jobs only run if the corresponding service was built:

```
deploy-api:       if build_api == 'true' || force_full == 'true'
deploy-messaging: if build_messaging == 'true' || force_full == 'true'
deploy-ai:        if build_ai == 'true' || force_full == 'true'
deploy-video:     if build_video == 'true' || force_full == 'true'
```

Dependencies:
- `deploy-api` waits for `run-migrations` to succeed (or be skipped)
- `deploy-messaging` and `deploy-ai` run in parallel, independently
- `verify-deployment` waits for all deploy jobs
- `deploy-video` is included in the same workflow and runs when the video worker image changes

## 5. Zero-Downtime API Strategy

### ASG Instance Refresh

- **MinHealthyPercentage: 100%** (API) — 새 인스턴스가 healthy가 될 때까지 기존 인스턴스 유지. 502 gap 0건 보장.
- **MinHealthyPercentage: 0%** (workers) — workers tolerate brief downtime during replacement (no HTTP traffic)
- **SkipMatching: false** (API) — launch template 변경 없어도 실제 인스턴스 교체 수행
- **InstanceWarmup: 300s** (API), **120s** (workers) — API는 ECR pull/컨테이너 기동 편차를 흡수
- **HealthCheckType: ELB** (API) — 앱 크래시 시 ALB가 감지 → ASG 자동 교체. **EC2** (workers) — ALB 없음.
- **HealthCheckGracePeriod: 300s** (API) / **60s** (workers) — 새 인스턴스 부팅 중 조기 종료 방지
- **ALB deregistration delay: 30s** — in-flight 연결 drain 후 즉시 정리
- Scale-up 후 **ALB target health 실측 확인** (고정 대기 아닌 실제 healthy 2개 확인, max 5min)
- Old instances are drained and terminated only after new ones pass ALB health checks

### Deployment Sequence

1. New EC2 instance launches with latest launch template
2. UserData script runs: install Docker, ECR login, pull `:latest`, fetch SSM env, run container
3. ALB health check passes on new instance
4. Old instance is drained (connection draining period)
5. Old instance is terminated

## 6. Worker Deployment Strategy

Workers use the same ASG instance refresh mechanism as API but with:
- Shorter warmup (120s vs 300s) — workers don't serve HTTP traffic
- No ALB health check — workers are background processors
- **MinHealthyPercentage=0%** — workers tolerate brief downtime during replacement. Message loss is prevented by SQS visibility timeout (messages return to queue if not acknowledged)

Runtime scaling is split by worker:

- **AI** uses AWS/SQS CloudWatch alarms (`ai-worker-queue-high`, `ai-worker-queue-low`, `ai-worker-queue-age-high`) and EC2 ASG StepScaling. SSOT min/desired is 0/0.
- **Messaging** runs with ASG min/desired=1 baseline and AWS/SQS CloudWatch alarms for StepScaling up to SSOT max capacity. Deploys replace instances through ASG refresh.
- **Video** is not an ASG worker. It is AWS Batch only.

### Worker UserData Flow

The worker launch templates contain UserData that executes on each new instance boot:

```bash
#!/bin/bash
# 1. Wait for network/IMDS
# 2. Install Docker (dnf/yum)
# 3. ECR login + pull image (5 retries, 15s apart)
# 4. Fetch SSM /academy/workers/env (base64 JSON -> KEY=VALUE env file)
# 5. docker run -d --restart unless-stopped --name <worker> -e DJANGO_SETTINGS_MODULE=... --env-file /opt/workers.env <image>
```

This UserData is already correctly implemented in `scripts/v1/resources/worker_userdata.ps1` and is embedded in the launch templates by `asg_ai.ps1` and `asg_messaging.ps1`.

## 7. Migration Strategy

### Execution

- Migrations run in GitHub Actions before API instance refresh.
- The workflow pulls the newly built immutable SHA image and runs `python manage.py migrate --no-input` in a one-shot Docker container using production env.
- Only runs when API or shared code changed
- Must succeed before API ASG refresh starts

### Backward Compatibility Requirement

Since migrations can succeed before every old API instance has drained:
- **Allowed:** Add nullable/default columns, add tables, add indexes
- **Not allowed in single release:** Drop columns, rename columns, remove tables, change column types
- For breaking schema changes, use a two-release process:
  1. Release N: Add new column (both old and new code work)
  2. Release N+1: Drop old column (old code no longer in production)

### Failure Handling

If migration fails:
- The SSM command returns non-zero exit code
- `run-migrations` job fails
- `deploy-api` is skipped (depends on migration success)
- Worker deploys are NOT affected (independent dependency chain)
- Fix the migration and push again

## 8. Rollback Strategy

### Image-Based Rollback

Every build produces immutable SHA-tagged images. To rollback:

1. **Identify the last good SHA tag:**
   ```bash
   aws ecr describe-images --repository-name academy-api \
     --query 'sort_by(imageDetails,&imagePushedAt)[*].{tags:imageTags,pushed:imagePushedAt}' \
     --output table
   ```

2. **Re-tag the good image as :latest:**
   ```bash
   MANIFEST=$(aws ecr batch-get-image --repository-name academy-api \
     --image-ids imageTag=sha-XXXXXXXX \
     --query 'images[0].imageManifest' --output text)
   aws ecr put-image --repository-name academy-api \
     --image-tag latest --image-manifest "$MANIFEST"
   ```

3. **Trigger ASG instance refresh:**
   ```bash
   aws autoscaling start-instance-refresh \
     --auto-scaling-group-name academy-v1-api-asg \
     --preferences '{"MinHealthyPercentage":100,"InstanceWarmup":300}'
   ```

### Migration Rollback

Django migrations are reversible by default. If a migration needs reverting:
```bash
# Via SSM on an API instance
docker exec academy-api python manage.py migrate <app_name> <previous_migration_number>
```

**Important:** Always verify that the reverse migration is safe before running.

## 9. ECR Lifecycle Policy

ECR repositories should have lifecycle policies to prevent unbounded image accumulation:

```json
{
  "rules": [
    {
      "rulePriority": 1,
      "description": "Remove untagged images after 1 day",
      "selection": {
        "tagStatus": "untagged",
        "countType": "sinceImagePushed",
        "countUnit": "days",
        "countNumber": 1
      },
      "action": { "type": "expire" }
    },
    {
      "rulePriority": 2,
      "description": "Keep last 10 sha-tagged images",
      "selection": {
        "tagStatus": "tagged",
        "tagPrefixList": ["sha-"],
        "countType": "imageCountMoreThan",
        "countNumber": 10
      },
      "action": { "type": "expire" }
    },
    {
      "rulePriority": 3,
      "description": "Keep last 5 release/deploy tags",
      "selection": {
        "tagStatus": "tagged",
        "tagPrefixList": ["latest", "v", "prod", "main", "deploy"],
        "countType": "imageCountMoreThan",
        "countNumber": 5
      },
      "action": { "type": "expire" }
    }
  ]
}
```

This keeps 10 rollback points and aggressively cleans untagged manifests. See `INFRASTRUCTURE-OPTIMIZATION.md` Section 4 for full ECR operational safety design including manifest-aware cleanup strategy.

## 10. Health Check Design

| Endpoint | Purpose | Checks | Used By |
|----------|---------|--------|---------|
| `/healthz` | Liveness probe | App is running, can respond | ALB health check, deploy verification |
| `/health` | Readiness probe | App + database connection | Deploy verification, smoke tests |
| `/readyz` | Readiness check (same as /health) | App + database connection | Registered at `urls.py` lines 16-18 |

- ALB uses `/healthz` for routing decisions (lightweight, no DB)
- Deploy verification checks BOTH endpoints
- `/health` failure with `/healthz` success indicates DB connectivity issue (not an app crash)

## 11. Workflow File Location

`backend/.github/workflows/v1-build-and-push-latest.yml`

## 12. Related Files

| File | Purpose |
|------|---------|
| `.github/workflows/v1-build-and-push-latest.yml` | CI build, migration, API/messaging/AI/video deploy, verification |
| `scripts/v1/resources/worker_userdata.ps1` | Worker UserData generation (Docker + ECR + SSM) |
| `scripts/v1/resources/asg_ai.ps1` | AI ASG + launch template management |
| `scripts/v1/resources/asg_messaging.ps1` | Messaging ASG + launch template management |
| `scripts/v1/resources/batch.ps1` | Video Batch CE/queue/job definition management |
| `scripts/v1/resources/api.ps1` | API ASG + launch template management |
| `scripts/v1/deploy.ps1` | Manual/bootstrap deployment (not used in CI/CD) |
