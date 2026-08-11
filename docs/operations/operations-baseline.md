# Operations Baseline

**Executable truth:** `.github/workflows/v1-build-and-push-latest.yml`,
`scripts/v1/`, `docs/ssot/params.yaml`, and current runtime readback.

## Release path

An application release is complete only when the same immutable candidate
passes every gate below:

1. GitHub Actions checks out `main` and uses the repository OIDC role.
   Explicitly authorized manual work may use an already configured account-root
   credential, but its value is never printed and no continuity gate changes.
2. Lint, migration safety, smoke tests, and the complete backend pytest suite
   pass before image build. The quality gate installs `requirements/test.txt`,
   seeds coverage with the focused fail-fast smoke run, then runs every remaining
   default-collected test in one sequential pytest process. This preserves the
   shared-database ordering boundary while avoiding a second smoke run and two
   extra Django/test-database startups. The gate combines both coverage files
   and publishes the missing-line summary in the job log. Product-code line
   coverage excludes tests and migrations and may not fall below the measured
   60.5% baseline. In parallel, a production-shape contract job creates an
   ephemeral pgvector/PostgreSQL 16 service and runs the critical transaction,
   concurrency, JSONB, tenant-isolation, and exam-policy regressions sequentially
   under `settings.test_pg`. Its host, database, and credentials are fixed to
   the job-local service; the quality gate never connects to a persistent or
   production database.
3. The base image is built or resolved first. Changed API, Video, Messaging,
   AI, and Tools ARM64 images then build on isolated matrix runners in
   parallel with a run-unique `sha-...-run-...` tag. A single fan-in job
   resolves all six immutable `sha256` digests and passes the existing ECR
   Critical/High scan gate before deployment can start.
4. `verify-api-development` deploys the API/Tools digests to the persistent,
   isolated development runtime. Dedicated DB, queues, R2, Redis, production
   resource denial, migrations, `/healthz`, database `/health`, image identity,
   and synthetic XLSX/PPT/R2 real-use smoke must pass.
5. `verify-api-preprod` publishes a release-bound exact SSM version and runs
   the API digest on a temporary isolated EC2 with the dedicated preproduction
   instance role and `academy_api_preprod_app` DB role. Migration, settings,
   DB name/role, production DB CONNECT denial, release identity, health, image
   identity and CDN playback must pass, and the instance must terminate.
6. Only then may production migration run on the digest-pinned candidate.
7. API deployment pins a new Launch Template version to the digest and performs
   an ALB-health-gated launch-before-terminate ASG refresh with
   `MinHealthyPercentage=100`, `MaxHealthyPercentage=200`, and
   `SkipMatching=false`. It preserves `min`/`desired`; only when current
   `desired == max` does it temporarily raise `max` by one replacement slot and
   restore/read back the original ceiling after success or compensation. The
   known-good instance remains until its replacement is healthy and warm.
8. `verify-deployment` compares expected digests with Launch Templates, actual
   InService container `RepoDigests`, worker/queue state and Video Batch
   definitions, then runs public health and affected real-use smoke.

The workflow uses a shared production mutation lock and does not cancel an
in-progress refresh. A successful build alone is not a production release.

## Change ownership

| Change | Owning path |
|--------|-------------|
| Application code or migration | Merge to `main`; GitHub Actions OIDC release path |
| Launch Template, UserData, ASG, ALB, IAM, SSM shape | `scripts/v1/deploy.ps1`, with an authorized identity and all continuity gates |
| Frontend | Frontend `quality-gate.yml`: checks → isolated preview → baseline/ownership check → direct Cloudflare Pages deploy → production E2E |
| Runtime env correction | Owning runbook/script; preserve rollback candidate and verify `/healthz` plus DB-backed `/health` |

`scripts/v1/deploy.ps1` converges infrastructure around an already verified/promoted
digest. It is not a shortcut for introducing a new application image. A new
candidate must still pass persistent development and isolated preproduction
through the GitHub Actions workflow before any production mutation.

Local AWS execution must first pass `scripts/v1/check-credentials.ps1`.
Account-root is allowed only when the user explicitly authorizes that manual
task; the mutation guard emits a warning and all exact-target, lock, preprod,
health and readback gates remain mandatory. Missing authorization or identity
still stops the operation. Do not copy credentials to repository files,
reports, or command output.

## Database and migrations

- Production connects directly to `academy-db`; the retired RDS Proxy is not
  in the request path.
- API `DB_CONN_MAX_AGE=0` is required for the current gevent/direct-RDS
  concurrency model. `/healthz` is liveness only; database availability is
  proven by `/health` and the RDS connection alarm.
- Migrations must be backward-compatible while old and new API instances
  overlap. Nullable additions and independent data backfills are expand-safe.
  removals, renames, blocking indexes and incompatible field changes require a
  separately reviewed contract release after the expand release is fully
  deployed.
- Migration execution uses the newly built digest, not the old API container,
  and reads the production env through the SSM-backed `/opt/api.env` path.

## Requirements

1. Add a package to the owning file under `requirements/`.
2. Pin compatibility-sensitive packages in `requirements/constraints.txt`.
3. Build the affected image and verify the installed version inside the
   candidate/runtime; do not treat a local `pip show` as production truth.
4. Backend test-only packages belong in `requirements/test.txt`; this manifest
   includes the API import surface plus PDF fixture dependencies so default
   `pytest` collection and the CI coverage run use the same reproducible
   environment. `pytest.ini` is the single pytest configuration owner; do not
   add a second `[tool.pytest.ini_options]` table that pytest will ignore.

## Security and data boundaries

- Tenant resolution fails closed. No default tenant, cross-tenant fallback or
  tenant-less query is allowed.
- User-authored, manually approved and canonical data is preserved.
- Messaging uses only an exact approved Alimtalk template; SMS/LMS is disabled.
- Video runs only on AWS Batch. Messaging, AI and Tools remain separate worker
  boundaries.
- Secret values are never printed. Validation records only parameter presence,
  version, digest/hash or reference.

## Observability baseline

| Signal | Expected evidence |
|--------|-------------------|
| API liveness/readiness | `/healthz` 200 and `/health` 200 with database connected |
| API replacement | ASG desired/InService and ALB healthy targets match; old instance drains only after replacement health |
| Runtime identity | release manifest digest = Launch Template = actual container `RepoDigests` |
| Workers | Messaging warm baseline; AI/Tools scale to queue demand; Video Batch queue/CE/job definition healthy |
| Database | RDS available, connection alarm `OK`, no API connection accumulation |
| Frontend | live `version.json` equals deployed Git SHA; required lazy assets and production E2E pass |
| User-impact alerts | five-minute alert cron succeeds without exposing payload or recipient secrets |

Current values and the latest incident/readback belong in
`docs/ssot/runtime-current.md` and `docs/reports/`, not in this invariant
baseline.
