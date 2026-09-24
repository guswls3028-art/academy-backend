# Concurrent Codex session workflow

This is the owning Academy contract for local work that spans multiple Codex
tasks. It prevents shared dirty trees, accidental cross-session staging, stale
deployment sources, and abandoned worktrees without weakening the production
continuity gates.

## Standing task authority

Unless the user explicitly limits the task to local-only, no-deploy,
draft/PR-only, or read-only work, an assigned implementation, change, or build
includes the normal in-scope commit, push, PR, merge, messaging, deployment,
production verification, and residue cleanup steps. GitHub publication and
production deployment do not require a separate request. Release, operations,
and cleanup assignments carry the same standing authority. Do not pause for a
second approval at each step; record the exact source SHA, target, checks, and
readback instead. This does not broaden the task, make an ambiguous destructive
target safe, waive tenant or user-data protection, bypass an explicitly applicable change window or
continuity gate, or make an external approval true without platform readback.
An explicit instruction to deploy, release, apply to production, or continue a
specific rollout includes authority to submit that rollout's GitHub
`production` environment approval through the official authenticated API; do
not pause for a second confirmation. Require GitHub to record the approval
before mutation, and never remove the protection, approve another queued run,
or infer approval from the instruction alone. If GitHub rejects the review or
the configured identity is ineligible, preserve the error and report that
technical blocker without asking the user to repeat the same authorization.

Compatible patches do not wait for a default 04:00 slot. Use the current
[deployment timing and continuity policy](deployment-modes.md) and retain
technical holds until their actual release conditions pass. Historical task
windows and old automation snapshots are not current release evidence.

## Execution efficiency

Read only the applicable subsection once per task when choosing reasoning,
delegating, recovering output or reusing evidence; reread changed material.
Optimize total consumption and
elapsed time per verified completed task, including rework; shorter prompts or
less reasoning alone do not establish savings or equal quality. Safety,
acceptance criteria, scoped HOLDs, required CI and delivery ownership still apply.

### Reasoning by actual change impact

Keep the user defaults at the chosen model and `medium` for normal and Plan
work. Never copy the current task's `ultra` into new-task/subagent defaults.

| Actual work | Reasoning choice |
|---|---|
| Read-only lookup, prose cleanup, clear small fixes, ordinary existing-pattern implementation | `medium` |
| Frontend/backend contract changes or broad structural changes | Consider `high` for design and final review |
| Tenant isolation, auth/authorization, user-data integrity, message duplication/retry, migrations, deployment/recovery safety | Start the relevant analysis/review at `high` or above, before a failure |
| Adequate evidence but unresolved cause/design, or repeated edit/test failure without new evidence | Change approach or consider higher reasoning; do not repeat the same loop |
| Complex analysis unresolved at `high`, or a specifically justified high-risk review | Consider `ultra` only for that scope |

Classify the actual impact, not words appearing in a document. Missing context,
truncated logs, access denial and environment faults need evidence or recovery,
not a larger reasoning setting.

Instructions saying "think at high" do not change runtime effort. When actual
risk requires High or above and the primary effort is lower or unverified, use
the supported scoped delegation below at that effort before consequential
implementation. This policy authorizes that request; no repeat user request is
needed. A primary
task already at the required effort can perform the analysis itself; do not add
a duplicate reviewer solely for effort coverage.

If delegation is unavailable, select the required effort in the app's
model/reasoning picker before the relevant work; an existing selection applies to
subsequent turns. A CLI invocation can use
`codex -c 'model_reasoning_effort="high"' -c 'plan_mode_reasoning_effort="high"'`
without changing saved defaults. The app-server supports an explicit next-turn
`effort` override, but AGENTS cannot switch an in-flight turn itself. If the
current interface cannot provide either path, report the required setting/scope,
defer that high-risk work until the effort requirement is met, and continue
independent work. Neither policy nor a High child changes the primary task's
runtime effort; do not claim automatic switching.
Use only values advertised by the installed model. A task run at `ultra` is not
evidence that `medium` preserves its quality.

### Valuable delegation and context

Keep one concurrent subagent and default subagent effort `medium`. This limits
simultaneously open subagents, not lifetime agent count, total tokens or cost.
Small tasks need no agent. Reuse completed findings or the existing agent for a
new bounded question instead of sequentially spawning near-duplicate workers.

Supply the objective, exact paths/symbols/SHA, relevant constraints/invariants,
expected result, and established facts/failure evidence. Omit unrelated history
and whole documents/logs; do not omit evidence essential for correct review.
With the available collaboration tool, `fork_turns: "none"` excludes surrounding
conversation history. A positive integer string carries that many recent turns,
not a token allowance. Runtime/project/tool instructions still occupy context,
and files remain shared: a short prompt does not prove a small actual input.
If the interface cannot control inheritance, record that limit instead of
promising reduced context.

When higher-effort coverage is required above, request one scoped reviewer to
check the proposed approach, invariants and failure boundaries before the risky
implementation, then incorporate its findings. Continue independent work while
it runs. Reuse that reviewer for the resulting relevant diff/evidence when
needed; send only the changes and new evidence, not a second full investigation.
Where supported, use
`spawn_agent(task_name="focused_review", fork_turns="none", reasoning_effort="high", message=<bounded brief>)`.
The actual effort argument matters. Omitted/`all` history forks inherit the
parent's model/effort and cannot accept these overrides; avoid them when they
would carry `ultra`. Keep the model choice unchanged unless separately assigned.

Verified with Codex 0.154.0-alpha.6.2: `multiAgentMode` input is deprecated/ignored
and the response always says `explicitRequestOnly`; this field does not prove
delegation behavior. The model catalog associates Ultra with automatic task
delegation. A saved medium default alone does not arrange review: the agent must
invoke the explicit request under this policy and check the current tool contract.

### Output recovery and complete review

Keep `tool_output_token_limit=4000`: it budgets each individual tool/function
output stored in history, not the whole conversation/task, generated stdout or
an archive. Tool-specific output limits/pagination may also apply.

When long output must remain inspectable, explicitly redirect it to a suitable
temporary/existing log before execution. Report exit code, failure summary,
source path and relevant ranges. Do not permanently archive all successful
output. If truncated, search/read missing ranges from that source; do not rerun
the command or repeatedly dump the full log just to recover stdout. If a
connector truncated upstream, use its pagination/refetch path. The setting
does not guarantee original-file storage, and raising it cannot restore missing
upstream content. Never repeat a mutation solely to recover output.

Track which files/sections were actually inspected. Partial diff/log excerpts
do not establish complete contract, migration or permission review; inspect the
missing material before closure. An unavailable required source remains an
evidence gap, never an assumed pass. Consider a supported per-command/read limit
override only for recurring, evidenced re-read costs; do not change the global
limit speculatively or confuse display budget with history storage.

### Durable context, evidence reuse and measurement

Keep automatic memory use/generation off and existing memory files intact.
Use versioned owners instead of a new diary or memory index. Before decisions
that depend on them, read current HOLD scope/owner/release conditions in the
[deployment owner](deployment-modes.md) and frontend `DEPLOYMENT-OPERATIONS.md`,
and the exact SHA/environment/run evidence in the
[change-risk owner](change-risk-and-release-bundle.md). Missing release evidence
does not clear a HOLD. Record important design decisions and reusable rejected
approaches with their reasons in the affected current-state owner only when
needed; do not reconstruct prior conversations or accumulate speculative TODOs.

Before consequential edits, identify the affected successful user outcome and
relevant failure/invariant checks in the existing task plan/context. Before
closure, map each to actual evidence or explicitly report the unresolved gap;
do not weaken acceptance criteria to meet a token budget or create a separate
checklist document for every task.

Reuse a check only after confirming relevant code, tests, dependencies, inputs
and environment match, or documenting why their differences cannot affect the
claim. Keep the tested SHA/environment and artifact/run link with that claim.
File extensions such as `.md` or "config" do not prove unchanged behavior;
assess effective behavior and retain required CI. Canonical and owned worktrees,
saved defaults, new-session values and existing task/composer state are distinct.
Read this owner from the fresh owned checkout/origin/main when canonical is stale.

Fast is a separate speed/usage selection for the same model, not reasoning.
Check installed catalog, authentication route, effective tier and current
[official Speed guidance](https://learn.chatgpt.com/docs/agent-configuration/speed)
before changing it. Preserve explicit task choices; an absent saved preference
does not establish the tier of an existing turn. Never apply API billing claims
to subscription usage or infer a credit multiplier from raw token counts.

For a few ordinary subsequent tasks, use the existing completion report/artifact
to note task type/risk, actual model/effort/tier, unique primary+child usage and
elapsed time, edit/check repeats, review omissions and later reopening. Count
each usage event once (not parent inherited baselines plus child totals). Measure
subscription-window changes only when resets and other task consumption can be
separated. Compare verified completions including rework, not cached-token ratio,
reasoning share or AGENTS size. Separate canonical/owned and new/existing session
cohorts. A small medium/high comparison needs the same starting state and
acceptance criteria; no mass benchmark or quality-equivalence claim by default.

Configuration semantics: [official reference](https://developers.openai.com/codex/config-reference/).

## Ownership model

- `C:\academy\backend` and `C:\academy\frontend` are canonical readback roots.
  Keep them on clean `main`; feature work does not edit them directly.
- Every task owns a unique lowercase session slug, branch, and worktree path.
  Cross-repository work uses the paired paths under
  `C:\academy\_worktrees\sessions\<session>\backend|frontend`.
- A worktree belongs to exactly one task. Other tasks may inspect it read-only
  but never stage, commit, reset, clean, rebase, or run generators in it.
- Exactly one task is the release owner. Other tasks stop at a reviewed branch
  or PR plus CI evidence and hand the exact commit SHA to the release owner.

The Git branch and registered worktree are the ownership record. Do not create
a parallel session registry, memory file, or shared scratch branch.

## Start a task

Choose a slug that combines the feature and a short task identifier, for
example `attendance-019fb78c`. From outside a target worktree run:

```powershell
pwsh C:\academy\backend\scripts\codex\session-worktree.ps1 `
  -Action Start `
  -Session attendance-019fb78c `
  -Repository both
```

`Start` fetches `origin/main` and creates a unique branch and worktree for each
selected repository. Use `backend`, `frontend`, or `both` to match the actual
scope. If a foreign dirty tree already exists, leave it untouched and still
start from current `origin/main` in the owned worktree. `Start` warns when its
volume has less than 10 GB free before a large install or build adds pressure.

Before editing, record the emitted base SHA and confirm the intended paths with:

```powershell
pwsh C:\academy\backend\scripts\codex\session-worktree.ps1 `
  -Action Inspect -Session attendance-019fb78c -Repository both
```

## Work and integration

- Run commands, generators, local servers, and tests only from the owned path.
  Use an explicit unique local port and point E2E at that server.
- Keep a cross-repository feature on paired branches with the same session slug.
- Before handoff, fetch and integrate current `origin/main` in the owned branch,
  resolve conflicts there, run focused checks, and report both exact SHAs.
- Never deploy a local-only commit, a dirty tree, a detached HEAD, or a branch
  that is not merged into current `origin/main`.
- A release owner records the chain `origin/main SHA -> candidate image digest
  or frontend version -> live runtime revision`. Backend deployment must also
  pass `scripts/v1/assert-production-source-freshness.ps1`; frontend deployment
  remains owned by `.github/workflows/quality-gate.yml` and its exact
  `GITHUB_SHA` readback.

## Finish or hand off

A task is not complete merely because existing changes were “preserved.” Pick
one explicit terminal state:

1. **Merged:** branch is contained in `origin/main`, or every remaining commit
   is patch-equivalent to `origin/main`; the worktree is clean and the session
   worktree and local branch are removed.
2. **Review pending:** clean committed branch/PR, named owner, exact SHA, and a
   stated merge or discard decision. It remains a worktree until resolved.
3. **Intentional WIP:** a named `wip/` branch with a recovery commit, owner, and
   next decision. It is never a deployment source. Raw uncommitted WIP is not a
   durable handoff state.

For the merged state, the close command performs a full preflight and refuses
dirty, foreign, or unmerged worktrees before deleting anything. A squash or
cherry-pick merge is accepted only when `git cherry origin/main HEAD` contains
no `+` commit; any unique commit preserves the branch. `Close` clears only
ignored backend Python caches (`.pytest_cache/`, `.ruff_cache/`, and
`__pycache__/`) after verifying every ignored path. It refuses other ignored
local files and directories before Git can unregister a worktree and leave an
incomplete directory. Clear only confirmed regenerable outputs in the exact
session, then close a merged session promptly:

```powershell
pwsh C:\academy\backend\scripts\codex\session-worktree.ps1 `
  -Action Close `
  -Session attendance-019fb78c `
  -Repository both
```

After active sessions and releases finish, fast-forward clean canonical roots:

```powershell
pwsh C:\academy\backend\scripts\codex\session-worktree.ps1 -Action Sync
```

`Sync` refuses dirty, non-`main`, or divergent canonical roots. It never resets,
rebases, force-checks out, or deletes user work.

`Close` validates integration against freshly fetched `origin/main`, not the
possibly stale canonical checkout. After that fail-closed preflight succeeds it
removes the exact session branch even when concurrent work intentionally keeps
the canonical `main` behind the remote.

## Local disk capacity

Check free space before large dependency installs or worktree batches:

```powershell
Get-PSDrive C | Select-Object @{Name='FreeGB';Expression={[math]::Round($_.Free/1GB,1)}}
```

When free space falls below 10 GB, inspect and close old merged, clean sessions
first. Preserve unmerged branches, dirty worktrees, ignored local data outside
confirmed regenerable outputs, and `_artifacts/` evidence. A failed `Close`
may leave a directory after Git unregisters the worktree; inspect its remaining
files before any manual cleanup. `pnpm` hardlinks package files across many
worktrees, so directory totals can overstate physical disk use. Use volume free
space before and after maintenance to measure actual savings.

On NTFS, mark the session and artifact parent directories as compressed so new
children inherit compression without deleting data:

```powershell
compact /C /Q C:\academy\_worktrees\sessions C:\academy\_artifacts
Get-Item C:\academy\_worktrees\sessions, C:\academy\_artifacts |
  Select-Object FullName,Attributes
```

Both parent directories must report `Compressed`. This does not compress
existing descendants. Compression can add CPU cost to builds, so measure it
locally before extending it to existing dependency trees.

## Verification

```powershell
pwsh scripts/codex/test-session-worktree.ps1
pwsh scripts/codex/session-worktree.ps1 -Action Inspect
git diff --check
git status --short
```

Final reporting lists canonical backend/frontend SHAs, the deployed revision or
digest when release work occurred, every remaining noncanonical worktree, and
the owner/decision for each unmerged or dirty branch.
Cross-repository product delivery also uses the fail-closed
[change-risk and release-bundle contract](change-risk-and-release-bundle.md) to
read the two official runs, pending approvals, backend manifest and lock, and
frontend live revision without inventing a shared Git transaction.
