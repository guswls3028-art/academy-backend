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

## V1.1.0 Patch — Video Pipeline Overhaul (2026-03-16)

### 6. Video Encoding: 2-Tier ABR with Aspect Ratio Preservation

기존 360p+720p 고정비트레이트 인코딩을 **CRF 기반 2단계 ABR**로 교체.

| Variant | 해상도 | CRF | maxrate | Profile |
|---------|--------|-----|---------|---------|
| v2 (고화질) | 원본 유지 (≤1080p) | 20 | 8000k | High L4.1 |
| v1 (중화질) | 720p 비율 보존 | 23 | 3000k | Main L3.1 |

- 원본 비율 정확 보존 (기존: 16:9 강제 스케일링)
- 휴대폰 rotation 메타데이터 자동 처리 (90°/270° w↔h 스왑)
- 원본 ≤720p인 경우 단일 variant (업스케일 방지)
- **코드:** `apps/worker/video_worker/video/transcoder.py`

### 7. Video Worker Mode: batch 고정

`VIDEO_WORKER_MODE` 기본값을 `daemon` → `batch`로 변경. Daemon 프로세스 미운용 상태에서 짧은 영상이 QUEUED에서 무한 대기하던 문제 해결.

- **코드:** `apps/api/config/settings/base.py`

### 8. Heartbeat DB Connection Recovery

장시간 인코딩 중 DB 커넥션 만료로 heartbeat 갱신이 무음 실패하던 문제 수정.
- `close_old_connections()` 추가
- heartbeat 실패 로그 DEBUG → WARNING 승격
- **코드:** `apps/worker/video_worker/batch_main.py`, `daemon_main.py`

### 9. Operational Data Protection (CLAUDE.md §B)

Tenant 1(개발/테스트) 제외 모든 테넌트 데이터는 실제 운영 데이터. 삭제/수정/초기화 등 파괴적 조작 절대 금지.

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
