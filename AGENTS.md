# Academy Backend — Codex Instructions

Self-contained instructions for the backend Git root.

## Sources and context

- Entry: `docs/README.md`; policy: `docs/ssot/`; current release:
  `docs/releases/README.md` `CURRENT` (not runtime evidence).
- Workflows/scripts/settings/migrations/runtime readback outrank code/tests,
  then SSOT, owning docs, and plans/reports/agent guidance.
- Read relevant rules/skills once; reopen only changed/missing sections. Use
  bounded `rg`/targeted reads and concise evidence; keep full logs in artifacts.
  Reuse unchanged passing checks unless failures/risks remain. Delegate only
  useful bounded independent work with minimal context; delegation is optional.
- For risk-based effort, review delegation, truncated-output recovery or evidence
  reuse, read `docs/operations/concurrent-codex-sessions.md` → Execution efficiency.
- In this owner's local sessions, use the installed ChatGPT Web bridge for
  meaningful independent work:
  `chatgpt-web/gpt-6-pro` at `max` is the default subagent. Start one bounded Web
  task early; use a lower Web model only for genuinely trivial work. Preserve
  the primary Codex model, tenant/security boundaries, and Codex ownership of
  integration, checks, and release. Retry Pro once on capacity errors; do not
  silently downgrade consequential work. If the bridge is unavailable, continue
  locally and report that once.

## Scope and authority

Diagnosis/explanation/review is read-only unless a change is requested. Unless
local-only/no-deploy/draft/PR-only/read-only, implementation/change/build and
release/operations/cleanup authorize their full in-scope workflow: commit, push,
PR, merge, explicitly authorized messaging, deployment, production verification,
cleanup. No repeated
permission for these steps.

`모든권한`, `모든권한 있음`, `모든권한o`, and equivalents retain that authority
until narrowed/revoked; finish the earliest assignment first. Never expand scope,
guess destructive targets, waive data protection/current HOLDs/gates, override
higher-priority action-time confirmation, or infer platform approval.

Explicit deploy/release/production/continue instructions authorize that exact
run's GitHub `production` review via official authenticated API; verify approval
before mutation. No protection bypass/other-run approval. Rejection/ineligibility
is a technical blocker, not a reconfirmation request. Owner:
`docs/operations/github-governance.md`.

Docs/agent-config-only changes need publication, required repository CI, and
applicable syntax/path/contract/diff checks; never skip or bypass required CI.
No separate application build/deployment or mutating live QA is needed unless
an executable contract changes.

## Product boundaries and evidence

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
For local Windows work, create/inspect an owned current-`origin/main` worktree
with `scripts/codex/session-worktree.ps1`. For GitHub Codespace Linux work, use
the remote Git worktree procedure in `docs/operations/concurrent-codex-sessions.md`.
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
