# V1.1.0 Release Notes — Zero-Downtime Deployment Infrastructure

**Version:** V1.1.0
**Date:** 2026-03-14
**Type:** Infrastructure / CI-CD

## Summary

V1.1.0 upgrades the CI/CD pipeline from API-only auto-deploy to full zero-downtime deployment across all services (API, Messaging Worker, AI Worker). It introduces SHA-based image tagging for rollback capability, pre-deploy database migration automation, selective per-service ASG refresh, and post-deploy verification.

## Changes

### 1. SHA-Tagged Images for Rollback

Every ECR image now receives two tags on each build:
- `:latest` — used by ASG launch templates for new instances
- `:sha-<8char>` — immutable tag tied to the exact git commit

This enables instant rollback by pointing an ASG to a previous SHA tag without rebuilding.

### 2. Selective Worker Deployment

Previously only the API ASG was refreshed on push. Now all three ASGs are conditionally refreshed based on change detection:

| Service | ASG Name | Condition |
|---------|----------|-----------|
| API | academy-v1-api-asg | apps/, docker/api/, requirements/api.txt, or shared code changed |
| Messaging | academy-v1-messaging-worker-asg | messaging worker code changed |
| AI | academy-v1-ai-worker-asg | AI worker code changed |

Workers use `MinHealthyPercentage=100, InstanceWarmup=120s` (shorter than API's 300s).

### 3. Pre-Deploy Database Migration

Before API ASG refresh, migrations run automatically via SSM RunCommand on a current InService API instance:
- Only runs when API or shared code changed
- Executes `docker exec academy-api python manage.py migrate --no-input`
- Must succeed before API refresh begins
- Times out after 120 seconds with clear error reporting

### 4. Post-Deploy Verification

A verification job runs after all deploy jobs complete:
- `GET /healthz` (liveness) must return 200
- `GET /health` (readiness, DB-connected) must return 200
- All ASGs must have `>= MinSize` healthy InService instances
- Results reported in GitHub Actions step summary

### 5. Build Report Path Update

CI build reports now write to `docs/00-SSOT/v1.1.0/reports/ci-build.latest.md` (was `v1/reports/`).

## What Did NOT Change

- Video Batch worker deployment (uses separate `video_batch_deploy.yml` with its own SHA tagging)
- Health check endpoint logic (`/healthz`, `/health`)
- Docker images / Dockerfiles
- `deploy.ps1` core logic (manual/bootstrap use)
- Worker UserData scripts (already correct: Docker install, ECR login, pull :latest, SSM env, run)

## Rollback Procedure

To rollback any service to a previous commit:

```bash
# 1. Find the SHA tag of the good version
aws ecr describe-images --repository-name academy-api --query 'imageDetails[*].imageTags' --output json

# 2. Re-tag the good SHA as :latest
MANIFEST=$(aws ecr batch-get-image --repository-name academy-api --image-ids imageTag=sha-XXXXXXXX --query 'images[0].imageManifest' --output text)
aws ecr put-image --repository-name academy-api --image-tag latest --image-manifest "$MANIFEST"

# 3. Trigger ASG instance refresh
aws autoscaling start-instance-refresh --auto-scaling-group-name academy-v1-api-asg --preferences '{"MinHealthyPercentage":100,"InstanceWarmup":300}'
```

## Migration Safety

All migrations must be backward-compatible (additive only). The migration runs on the CURRENT code before the new code deploys. This means:
- New columns must have defaults or be nullable
- No column renames or drops in the same release
- No breaking schema changes

## Prerequisite: IAM Permissions

The OIDC role (`AWS_ROLE_ARN_FOR_ECR_BUILD`) must have:
- `autoscaling:StartInstanceRefresh` (all 3 ASGs)
- `autoscaling:DescribeInstanceRefreshes` (all 3 ASGs)
- `autoscaling:DescribeAutoScalingGroups`
- `ssm:SendCommand` (API instances)
- `ssm:GetCommandInvocation`
- Existing ECR push permissions (already present)
