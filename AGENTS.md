# Academy Backend — Codex Instructions

Self-contained instructions for the backend Git root.

## Sources and context

- Entry: `docs/README.md`; policy: `docs/ssot/`.
  Current release: `docs/releases/README.md` `CURRENT` (not runtime evidence).
  Workflows/scripts/settings/migrations/runtime readback outrank code/tests,
  then SSOT, owning docs and plans/history/agent guidance.
- Reuse rules already in context; read only relevant changed/missing sections.
  Use scoped searches and bounded output. Save long logs only when needed for
  recovery; reuse passing checks on unchanged relevant inputs, retaining CI.
- Keep the primary model/settings. Prefer one bounded `chatgpt-web/gpt-6-pro`
  at `max` early when independent work advances the task. Small, coupled or
  duplicate work stays local. Use a lower Web model only for trivial work.
  Verify the selected model; retry capacity once, never silently downgrade.
  Bridge/Native2 failure: continue locally and report once; do not interrupt
  another task's launcher. Codex owns integration, checks and release.
- Detailed reasoning, output recovery and evidence policy:
  `docs/operations/concurrent-codex-sessions.md` → Execution efficiency.
  Read the needed subsection when these defaults leave a decision unresolved;
  do not reload it for every task/check.

## Scope and authority

Diagnosis/review is read-only unless changes are requested. Otherwise, unless
local-only/no-deploy/draft/PR-only, the assignment includes scoped publication,
merge, deployment, verification and owned cleanup. `모든권한` and equivalents
persist until narrowed/revoked; finish the earliest assignment without repeated
permission. Same-user Academy tasks/owned subagents may exchange non-sensitive
scope, paths, SHAs and CI/verification evidence. External recipients, authentication,
secret transfers and ambiguous destructive targets need exact authorization.
Preserve tenant/user data, current HOLDs, release gates, managed permissions and
higher-priority action-time approvals. Give formal review the exact target,
impact and user instruction; never work around a denial.

Explicit deployment/release/production/rollout continuation also authorizes that
exact run's GitHub `production` approval via the official authenticated API.
Require platform readback before mutation; no protection bypass or unrelated-run
approval. Rejection/ineligibility is a technical blocker, not a reconfirmation
request. Owner: `docs/operations/github-governance.md`.

Docs/instruction/local-settings changes require applicable syntax/path/contract/
diff checks and required CI; no separate product build, deployment or mutating
QA solely for prose. Executable changes use their owning gates.

## Delivery responsibility

A deployment request remains open until the requested changes are included in
the deployed candidate, its exact live revision and affected flows are verified,
and owned cleanup is complete. Commit/CI/merge/handoff is not completion. Keep
one release executor; transfer outcome responsibility only after the receiver
acknowledges the exact candidate, remaining work and tracking. Continue assigned
fixes/verification. Failed gates require diagnosis and correction; genuine
external waits need a named owner, release condition and verified existing
authorized continuation. Do not leave runnable work for the user to re-request,
create duplicate chats, or imply unverified background work. Preserve gates and
managed denials. Details: `docs/operations/concurrent-codex-sessions.md` →
Delivery completion and continuation. Docs-only scope remains as defined above.

## Product boundaries and evidence

For backend work motivated by a UI/UX or user-journey complaint, inspect the
current affected journey and data/API boundary before changing behavior. Treat
the suggested fix as a hypothesis, consider relevant empty/error/retry,
interruption, duplicate-action, role, and tenant cases, then verify the normal
journey and recovery with the frontend owner. Keep the review proportional;
the frontend review method is in `../frontend/docs/REAL-USE-REVIEW-MANUAL.md`.

- Resolve tenant at request/job entry and scope every business query. Missing/
  ambiguous context fails closed: no default, hostname override, cross-tenant
  fallback. Keep business decisions in the owning domain.
- Preserve manual/user-authored/approved data and references; automated analysis
  uses proposal/review. Before destructive/bulk/storage work enumerate exact
  tenant/object targets/counts, exclude user-created data, establish assigned
  scope, and verify post-state. Ask only for unresolved target/scope.
- Video/Messaging/AI/Tools have separate queues/state; encoding is AWS Batch only.
  Preserve correlation IDs and structured production logs.
- Messaging: `docs/ssot/messaging-policy.md`, exact approved owner templates,
  no SMS/LMS fallback. Matchup: `docs/domain/matchup.md`, preserve manual cuts/
  approvals. Accounts: `docs/domain/parent-account.md`, `docs/domain/student-core.md`,
  `docs/domain/student-lifecycle.md`.
- Prove every affected role's ordinary successful action through API,
  persistence, worker, reload/downstream screens, and visible failure/recovery.
  Guards, disabled actions, notices, swallowed errors, empty-success fallbacks,
  and green CI alone are insufficient. Only Beta labeled before entry permits a
  documented incomplete path. Retain legitimate safety boundaries and inspect
  callers/compatibility before removing code. Evidence owner:
  `docs/operations/change-risk-and-release-bundle.md`.

Every behavior change updates its current-state owner and indexes new owners in
`docs/README.md`: purpose, actors/flow, permissions/invariants, API/data/event
ownership, failures/retries, cross-repo links, verification. Removal/replacement
also records reason, migration/compatibility, existing-data fate. Plans belong
in `docs/refactor/`.
Behavior-preserving internals may omit product docs only with a final explanation
and supporting verification.

## Delivery and isolation

Keep canonical `C:\academy\backend` and `C:\academy\frontend` on clean `main`.
For local Windows work, export the lifecycle helper from fresh backend
`origin/main` as described in the session owner’s `Start a task` section;
create/inspect an owned current-main worktree. Never bootstrap from a stale
canonical helper. For GitHub Codespace Linux work, use
the remote Git worktree procedure in `docs/operations/concurrent-codex-sessions.md`.
Below 10 GB free on C:, use a Codespace for new installs and builds; local
`Start` refuses unless explicitly limited to lightweight recovery work.
Never mutate a foreign tree. One task owns release; others hand off exact
committed SHA/CI. Close only clean, merged/patch-
equivalent branches; intentional WIP needs a named recovery commit. Sync after
active tasks/releases finish. Owner: `docs/operations/concurrent-codex-sessions.md`.

Before production read `docs/operations/deployment-modes.md`,
`docs/operations/persistent-development-runtime.md`, and `scripts/v1/README.md`.
Executable owners are `.github/workflows/v1-build-and-push-latest.yml` and
`scripts/v1/deploy.ps1`; retain their complete gates:

1. Immutable digest → isolated persistent development migration, resource-denial,
   identity/health, and synthetic Excel/PPT/R2 smoke.
2. Release-bound sanitized isolated preprod env/dedicated DB role, production-DB
   denial, health/identity/CDN → confirmed temporary EC2 termination.
3. Only then production expand/contract migration and healthy ASG/ALB rolling
   replacement, retaining old capacity until healthy. AI/Tools warm baseline
   precedes launch-before-terminate digest refresh.
4. Runtime digest/queues/Batch and affected journeys pass before `latest` aliases
   and successful manifest promotion.

Use configured OIDC/secret stores first; never print/copy credentials. Assigned
manual workflows may use configured account-root/master credentials when needed
without weakening gates. Manual mutation requires clean exact latest `main`,
complete successful manifest ancestry, and
`scripts/v1/assert-production-source-freshness.ps1`.

No default 04:00 wait: prove old/new API/DB compatibility and uninterrupted
playback/editing without forced reload. Preserve exact current HOLD scope/state
until its owner's release conditions pass; historical holds or unrelated static
findings are not blanket holds.
Unresolved interruption/incompatibility requires a separate window.

## Verification

Run focused tests, then applicable repository gates:

```powershell
python manage.py check --settings apps.api.config.settings.test
python manage.py makemigrations --check --dry-run --settings apps.api.config.settings.test
python -m ruff check apps/ academy/
python scripts/lint/check_submission_lifecycle_boundary.py
python scripts/lint/refactor_boundary_snapshot.py --strict-touched
python -m pytest tests/test_smoke.py -v --tb=short -x
```

Rules/docs work follows workspace `docs-and-rules-sync` and applicable contract
checks. Start/finish with both repository statuses and finish `git diff --check`.
Stage explicit files only; preserve other work.
