# Academy Backend — Codex Instructions

This file is self-contained for Codex sessions started at the backend Git root.

## Sources of truth

- Documentation entry: `docs/README.md`
- Product/config policy: `docs/ssot/`
- Architecture: `docs/architecture/`
- Domain behavior: `docs/domain/`
- Operations and deployment: `docs/operations/`, `docs/infrastructure/`
- Current release: `docs/releases/README.md` `CURRENT`
- Executable deployment truth: `.github/workflows/` and `scripts/v1/`

Executable artifacts and current code/tests outrank prose. Plans, reports,
release history, and agent guidance are not current runtime evidence.

## Durable feature records

Every added, changed, removed, or replaced behavior must update its owning
current-state document in the same task. Use `docs/domain/` for product rules
and flows, `docs/architecture/` for system boundaries, `docs/infrastructure/`
for runtime topology, and `docs/operations/` for operator procedures. If no
owner exists, create one and link it from `docs/README.md`.

The record must recover purpose, actors and entry points, end-to-end flow,
invariants and permissions, data/API/event ownership, failure and retry
behavior, cross-repository dependencies, and focused verification. For a
removal or replacement, also record why, migration/compatibility behavior,
and the fate of existing data. Keep planned work in `docs/refactor/`; promote
implemented behavior into the current-state owner.

## Backend boundaries

- Resolve tenant at the request/job boundary and scope every business query.
  Missing or ambiguous tenant context hard-fails; no default tenant, hostname
  override, or cross-tenant fallback.
- Preserve manual, user-authored, approved content and protected references.
  Automated analysis enters proposal/review flow where one exists.
- Before delete, reset, recut, bulk rewrite, or storage cleanup, enumerate
  exact tenant/object targets and counts, exclude user-created data, obtain
  explicit approval, and verify post-state.
- Keep business decisions inside the owning domain/application boundary.
- Video, Messaging, AI, and Tools workers have separate queues and mutable
  state. Video encoding is AWS Batch only.
- Preserve correlation IDs and structured production logging on changed paths.

Messaging work must follow `docs/ssot/messaging-policy.md`; exact approved
owner templates are required and SMS/LMS fallback is forbidden. Matchup work
must follow `docs/domain/matchup.md` and preserve manual cuts and approvals.
Account/student work follows `docs/domain/parent-account.md`,
`docs/domain/student-core.md`, and `docs/domain/student-lifecycle.md`.

## Verification

Run focused tests first, then as applicable:

```powershell
python manage.py check --settings apps.api.config.settings.test
python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test
python -m ruff check apps/ academy/
python scripts/lint/check_submission_lifecycle_boundary.py
python scripts/lint/refactor_boundary_snapshot.py --strict-touched
python -m pytest tests/test_smoke.py -v --tb=short -x
```

Finish with `git diff --check` and `git status --short`. Stage explicit files
only. Preserve pre-existing changes.

## Mandatory preproduction and zero-downtime delivery

Current executable entry points are
`.github/workflows/v1-build-and-push-latest.yml`, `scripts/v1/deploy.ps1`,
`scripts/v1/deploy-api-development.ps1`,
`scripts/v1/run-api-preprod-canary.ps1`, and `scripts/v1/verify.ps1`.

For every backend production release:

1. Build an immutable digest-pinned candidate; do not move `latest` yet.
2. Verify it on the persistent production-shaped development runtime with
   dedicated IAM, DB, queues, R2, Redis, and inbound-free SSM networking.
3. Require migration, production-resource denial, `/healthz`, `/health`,
   image identity, and synthetic Excel/PPT/R2 real-use smoke.
4. Publish a release-bound, versioned preprod env using the dedicated preprod
   DB role and credential, then run the isolated preprod EC2 gate.
5. Require migration, prod settings, exact DB name and role, denial of
   production DB CONNECT, exact env version/release ID, health, image identity,
   and signed CDN playback. Confirm termination.
6. Only then mutate production DB/runtime. Use backward-compatible
   expand/contract migrations while old and new instances overlap.
7. Replace through ASG/ALB health-gated rolling refresh with old instances
   retained until replacements are healthy.
8. Verify runtime digests, ALB/ASG, workers/queues, Batch, and affected user
   flows. Only after all verification may compatibility `latest` aliases and
   the successful release manifest be promoted.

Ordinary automation uses the repository GitHub OIDC role. Explicitly
authorized manual work may use an already configured AWS account-root or
Cloudflare master credential, but must never print/copy its value or weaken
the development, preproduction, migration, rolling-health, or post-deploy
gates.
