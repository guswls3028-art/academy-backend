# Video Batch Production Runbook

**Status:** Active
**Last checked:** 2026-06-29
**Executable truth:** `.github/workflows/v1-build-and-push-latest.yml`, `scripts/v1/resources/batch.ps1`, `apps/worker/video_worker/`

## 1. Current Architecture

- Video encoding runs only through AWS Batch. Daemon/long-path worker mode is retired.
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

General post-deploy verification entrypoint:

```powershell
pwsh scripts/v1/run-deploy-verification.ps1 -AwsProfile default
```

## 6. Rollback

- Prefer immutable ECR SHA-tag rollback. Re-tag the last known-good image as `latest`, then run the relevant ASG/Batch refresh path.
- Do not roll back by editing Batch job definitions manually unless the GitHub Actions path is unavailable.
- If a Django migration is involved, verify reverse-migration safety separately before rollback.

## 7. Obsolete References

The following names are legacy-only and must not appear in new runbooks:

- `scripts/infra/*`
- `docs/deploy/SSM_JSON_SCHEMA.md`
- `docs/deploy/actual_state/*`
- `.github/workflows/video_batch_deploy.yml`
- daemon video worker ASG commands
