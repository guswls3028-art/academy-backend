# Production Canary

Read-only production canary for release closure and preventive operations.

## Backend DB Invariants

Run inside the API container:

```powershell
python manage.py production_canary --tenant-id 1 --tenant-code hakwonplus --indent 2
```

The command fails on critical user-facing risks:

- enabled autosend configs without an implemented trigger or approved effective template
- READY videos missing HLS output
- stale active video jobs (`QUEUED`, `RUNNING`, or `RETRY_WAIT`)
- `Video.current_job` pointing at a different tenant or video row
- old UPLOADED or PROCESSING videos without a same-tenant active transcode job
- explicit E2E, AUDIT, or CHAOS residue in the production tenant
- auto billing enabled without Toss secret and webhook secret

Warnings are emitted for operational debt that should be reviewed but may be accepted temporarily:

- overdue or failed messaging jobs
- stale messaging worker heartbeat
- READY videos missing thumbnails
- READY videos still tied to an active transcode job
- recent DEAD video jobs
- fee management feature flag enabled without explicit allowlist
- billing date gaps, due AUTO_CARD invoices while auto billing is off, old pending transactions

Use `--fail-on-warning` for conservative release sealing.

## One-Command Wrapper

From the backend repository:

```powershell
pwsh -File scripts/v1/run-production-canary.ps1 -AwsProfile default
pwsh -File scripts/v1/run-production-canary.ps1 -Mode PostDeploy -AwsProfile default -WriteReport
```

The wrapper checks:

- public API and frontend HTTP edge
- tenant-scoped program API health (`2xx` only)
- API and worker ASGs
- ALB target health
- RDS and Redis state
- SQS queue and DLQ depth, failing closed if AWS CLI/SQS permissions/queue lookup fail
- CloudWatch service alarms
- remote Django checks on the live `academy-api` container
- video Batch queue/compute environment validity in `PostDeploy` and `Deep`

`PostDeploy` and `Deep` modes treat warnings as release-blocking and run remote Django checks on every healthy InService API instance.

Use `-Json` when automation needs machine-readable output. In JSON mode the wrapper suppresses progress logs and writes only one JSON payload to stdout.

This script is read-only. It does not mutate AWS resources or production data, aside from normal SSM command history.
