# Video Batch Production Runbook

**Status:** Active
**Last checked:** 2026-07-23

**Executable truth:**

- `.github/workflows/v1-build-and-push-latest.yml`
- `scripts/v1/resources/batch.ps1`
- `apps/worker/video_worker/`
- `apps/domains/video/serializers.py`
- `apps/core/management/commands/production_canary.py`
- `frontend/src/app_teacher/domains/videos/components/CompactVideoThumbnail.tsx`

## 1. Current Architecture

- Video encoding runs only through AWS Batch. Daemon/long-path worker mode is retired.
- Only uploaded `source_type=s3` videos enter the Batch/R2/HLS pipeline. `source_type=youtube` videos are metadata-only links: they are created as `READY`, use YouTube thumbnail/embed URLs, and must not be retried, reconciled, or scanned as Batch jobs.
- Main compute environment: `academy-v1-video-batch-ce-200gb` (`SPOT`, desired=0 max=40 vCPU)
- Main queue: `academy-v1-video-batch-queue`
- Main job definition: `academy-v1-video-batch-jobdef`
- Ops compute environment: `academy-v1-video-ops-ce` (`EC2`, desired=0 max=1 vCPU)
- Ops queue: `academy-v1-video-ops-queue`
- Worker image: ECR `academy-video-worker`
- Runtime shape: c6g.4xlarge, 8 vCPU, 16GB memory, 200GB EBS, job timeout 6h.
- Ops jobs such as reconcile/scan_stuck/netprobe use the same worker image and worker settings.
Current runtime snapshot: `docs/ssot/runtime-current.md`.

## 2. Deploy Path

Normal production deployment is the backend GitHub Actions workflow:

```text
git push origin main
  -> .github/workflows/v1-build-and-push-latest.yml
  -> build-and-push
  -> deploy-video when video worker inputs changed
  -> verify-deployment
```

Do not use removed `scripts/infra/*` or `.github/workflows/video_batch_deploy.yml`; those paths are legacy and no longer exist in the repo.

## 3. Configuration Source

- API env: SSM `/academy/api/env` -> `/opt/api.env`
- Worker env: SSM `/academy/workers/env` -> `/opt/workers.env` or Batch entrypoint env
- SSM JSON schema reference: `docs/operations/ssm-json-schema.md`
- Django settings for workers: `apps.api.config.settings.worker`

No manual console editing of SSM values during normal release work. If env drift is suspected, compare executable scripts and SSM snapshots before changing production values.

## 4. Resource Management

Current v1 resource scripts live under `scripts/v1/resources/`:

| Resource | File |
|----------|------|
| Batch CE/queue/job definition | `scripts/v1/resources/batch.ps1` |
| ECR repositories | `scripts/v1/resources/ecr.ps1` |
| EventBridge | `scripts/v1/resources/eventbridge.ps1` |
| CloudWatch | `scripts/v1/resources/cloudwatch.ps1` |
| IAM | `scripts/v1/resources/iam.ps1` |
| Worker userdata | `scripts/v1/resources/worker_userdata.ps1` |

Run the full `scripts/v1/deploy.ps1` path when infrastructure needs to be reconciled. Code-only releases should use GitHub Actions from `main`.

## 5. Verification

After a release touching video, verify:

- GitHub Actions `deploy-video` succeeded or was correctly skipped when video inputs did not change.
- `verify-deployment` succeeded.
- Batch CE and queue are enabled.
- A netprobe or small test job reaches `SUCCEEDED` when video runtime changed.
- `reconcile_batch_video_jobs` and `scan_stuck_video_jobs` remain runnable with `apps.api.config.settings.worker`.
- YouTube link upload still bypasses Batch and is immediately playable in the student app through the embedded YouTube player.

General post-deploy verification entrypoint:

```powershell
pwsh scripts/v1/run-deploy-verification.ps1 -AwsProfile default
```

### 5.1 Thumbnail Not Visible Triage

Do not infer a worker failure from a play-icon placeholder. Verify the chain in
order and stop at the first broken boundary:

1. **Database/worker invariant** — the uploaded `READY` video has a non-empty,
   same-tenant `thumbnail_r2_key`. The production canary warning covers only
   this layer.
2. **API contract** — the tenant-scoped media response includes a non-empty
   `thumbnail_url`. YouTube items use the normalized YouTube URL path; uploaded
   items use the configured signed CDN path.
3. **Object delivery** — the R2 object has a non-zero size and a sanitized CDN
   probe returns `2xx` with an image content type. Do not copy signed query
   parameters into reports or logs.
4. **Browser rendering** — the affected product path contains an `<img>` whose
   `complete` state is true and `naturalWidth`/`naturalHeight` are non-zero.
   Check both the reported viewport and the sibling teacher video list.

Interpretation:

- Missing DB key: Batch publication/reconciliation incident.
- DB key present but API URL missing or delivery failing: serializer, signing,
  CDN, or object-path incident.
- API URL and CDN object healthy but no decoded DOM image: frontend projection
  incident.
- Only one teacher screen fails: split rendering ownership or missing regression
  coverage.

Current teacher compact-list ownership:

- Session video tab:
  `frontend/src/app_teacher/domains/lectures/pages/SessionDetailPage.tsx`
- Teacher-wide video list:
  `frontend/src/app_teacher/domains/videos/pages/VideoListPage.tsx`
- Shared compact renderer:
  `frontend/src/app_teacher/domains/videos/components/CompactVideoThumbnail.tsx`
- Focused regression:
  `frontend/e2e/teacher/video-thumbnail-render.mock.spec.ts`

The compact teacher renderer is intentionally separate from the application-wide
`frontend/src/shared/media/video/VideoThumbnail.tsx`, which owns other video
layouts and contracts.

## 6. Rollback

- Use `pwsh scripts/v1/rollback-video.ps1 -AwsProfile default`. With no `-Sha`, it derives the current digest from all eight required ACTIVE job definitions and selects the immediately prior immutable image.
- The script registers all eight revisions with the exact `repo@sha256:...` URI and fails unless every readback matches and both compute environments are `VALID/ENABLED`.
- Never re-tag `latest` for rollback; digest-pinned Batch runtimes do not observe that alias.
- If a Django migration is involved, do not reverse it as a generic rollback step; use a corrective migration/roll-forward unless a migration-specific quiesce, snapshot, and tested reverse runbook exists.

## 7. Obsolete References

The following names are legacy-only and must not appear in new runbooks:

- `scripts/infra/*`
- `docs/deploy/SSM_JSON_SCHEMA.md`
- `docs/deploy/actual_state/*`
- `.github/workflows/video_batch_deploy.yml`
- daemon video worker ASG commands
