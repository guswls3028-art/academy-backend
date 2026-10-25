# Concurrent Codex session workflow

This is the owning Academy contract for local work that spans multiple Codex
tasks. It prevents shared dirty trees, accidental cross-session staging, stale
deployment sources, and abandoned worktrees without weakening the production
continuity gates.

## Standing task authority

Unless the user explicitly limits the task to local-only, no-deploy,
draft/PR-only, or read-only work, an assigned implementation, change, or build
includes the normal in-scope commit, push, PR, merge, internal task handoffs,
deployment, production verification, and residue cleanup steps. GitHub publication and
production deployment do not require a separate request. Release, operations,
and cleanup assignments carry the same standing authority. Do not pause for a
second approval at each step; record the exact source SHA, target, checks, and
readback instead. This does not broaden the task, make an ambiguous destructive
target safe, waive tenant or user-data protection, bypass an explicitly applicable change window or
continuity gate, or make an external approval true without platform readback.
Same-user Academy tasks and owned subagents can share non-sensitive paths,
SHAs, PR/CI state, and verification evidence without per-message approval.
External recipients, account authentication, secret transfers, ambiguous
destructive targets, and higher-priority action-time approvals remain separate.
Confirm target and impact read-only when needed. Present the exact current user
instruction to formal approval review; never route around a denial. Managed
session permissions and release gates still apply.

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

Optimize verified completion including rework, not prompt length alone. Reuse
applicable instructions already in context; read only the needed subsection.
Safety, successful outcomes, current HOLDs, required CI and delivery still apply.

### Reasoning by actual change impact

Keep the primary model/effort selected by the user. Match analysis and review to
actual risk: ordinary edits use existing patterns and focused checks; tenant,
auth, data integrity, retries, migrations and release/recovery changes require
explicit invariant/failure review. Prefer an independent Web Pro reviewer when
it adds evidence or catches a consequential risk, not merely to satisfy an
effort label. Do not block a task solely because the active effort is unknown,
or change global settings to satisfy prose. Pause only work with a concrete
unresolved safety, authorization or acceptance gap; continue independent work.
Repeated failure without new evidence calls for a changed approach. Missing
context, truncated output and environment faults need recovery, not more effort.

### Valuable delegation and context

Prefer one concurrent `chatgpt-web/gpt-6-pro` / `max` worker for meaningful
independent work, dispatched early while Codex advances a disjoint scope.
Small, tightly coupled, duplicate or coordination-heavy work stays local.
Lower Web models are for genuinely trivial work only. Workers execute their
bounded assignment directly; do not spawn recursively to fulfill this preference.

Give objective, owned worktree/disjoint files, acceptance criteria, relevant
rules, exact paths/SHA and established evidence; use `fork_context: false`.
Runtime/project/tool instructions may still be inherited. Reuse the worker for
follow-ups, sending only changes/new evidence. Codex integrates and owns release.
Verify the actual selected model on spawn/resume; saved defaults do not prove
successful Web execution. Preserve primary settings and protected-data blocks.

Retry once for an actual capacity error; do not silently downgrade. Other bridge
or Native2 failures: preserve work, report once and continue locally. Diagnose
the bridge only when needed for the task; a generic spawn error is not proof of
an account limit. Defer if another task owns the launcher; never cancel its turn
or restart shared runtime. Specific recovery is in [Web bridge recovery](#web-bridge-recovery).
Remote hosts have their own configuration; verify the execution host before
claiming Web routing. Use only model/effort values advertised by the interface.

### Output recovery and complete review

Keep `tool_output_token_limit=4000` unless measured recurring recovery cost
justifies a change. It limits stored tool output, not the task token budget or
original stdout; tool-side limits also apply. Search before full-file reads.
Batch independent reads, cap aggregate returned text, and keep large results
in a tool store/log for selective extraction. Do not print entire tool schemas,
connector results, documents or successful logs when a bounded summary suffices.

Save long output before execution only when it must remain inspectable. Return
exit code, relevant failures and evidence path. Recover truncated ranges from
the source/pagination; never rerun a mutation solely for output or redump the
whole result. Inspect missing sections required for the claim; partial excerpts
are not complete contract/permission review. Do not claim unavailable evidence.

### Durable context, evidence reuse and measurement

Automatic memory generation/use remain off. Preserve existing memory history;
explicit updates use the established ad-hoc extension. Current policy lives in
versioned owners, not diaries or a parallel index. Read only relevant memory
on request; historical task settings/HOLDs are not current instructions.

Before consequential edits, identify the affected successful outcome and failure
boundaries in the existing task context; map them to evidence at closure without
creating a separate checklist. Reuse evidence when relevant code/tests,
dependencies, inputs and environment match, or explain why differences do not
affect the claim. Retain exact SHA/environment/run links and required CI.
Docs-only changes need syntax/path/diff and affected contract checks, not a
product build/deployment or mutating QA solely for prose.

Consult deployment/HOLD owners only when a decision depends on that release;
missing evidence never clears a gate. Canonical vs owned checkouts, saved defaults
vs active turns, and input tokens vs subscription use are distinct.
Measure tokens/timing only for an explicit optimization task or observed
regression, not every subsequent task. Count primary/child events once and
include rework. Instruction-size reduction is not measured subscription savings.
Do not tune Fast, context/compaction limits or models speculatively; preserve
explicit user choices and verify current supported configuration before changing.

Reference: [official configuration](https://developers.openai.com/codex/config-reference/).

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
example `attendance-019fb78c`. A dirty or stale canonical checkout must not choose
the lifecycle implementation: an old helper can miss current session filtering,
disk and cleanup safeguards even when it fetches a fresh worktree afterward.
Fetch the current script without modifying that checkout, then run it from an
owned artifact directory with the explicit workspace root:

```powershell
git -C C:\academy\backend fetch origin main
if ($LASTEXITCODE -ne 0) { throw 'Cannot fetch current lifecycle source' }
$sessionHelper = 'C:\academy\_artifacts\attendance-019fb78c\session-worktree.ps1'
$helperSource = git -C C:\academy\backend show origin/main:scripts/codex/session-worktree.ps1
if ($LASTEXITCODE -ne 0) { throw 'Cannot read current lifecycle source' }
New-Item -ItemType Directory -Force (Split-Path $sessionHelper) | Out-Null
[IO.File]::WriteAllText($sessionHelper, ($helperSource -join "`n") + "`n")
pwsh -NoProfile -File $sessionHelper -WorkspaceRoot C:\academy `
  -Action Start `
  -Session attendance-019fb78c `
  -Repository both
```

`Start` fetches `origin/main` and creates a unique branch and worktree for each
selected repository. Use `backend`, `frontend`, or `both` to match the actual
scope. If a foreign dirty tree already exists, leave it untouched and still
start from current `origin/main` in the owned worktree. `Start` refuses a new
local session when its volume has less than 10 GB free, before fetching or
creating a branch. Use a Codespace for new dependency installs and builds.
For a small recovery task that needs a local worktree, `-AllowLowDisk` explicitly
permits `Start`; keep installs and builds remote and close the worktree afterward.

Before editing, record the emitted base SHA and confirm the intended paths with:

```powershell
pwsh -NoProfile -File $sessionHelper -WorkspaceRoot C:\academy `
  -Action Inspect -Session attendance-019fb78c -Repository both
```

After creation, lifecycle commands may use that owned checkout's current helper.
Keep using `-Session` for inspection; canonical synchronization remains separate
and must not overwrite foreign changes or run while another task owns release.

`Inspect` resolves the fetched main revision once per repository and reuses its
ahead/behind result to recognize already merged worktrees. Divergent branches
still receive the full merge/patch/tree-equivalence check. This only reduces
read-only Git process overhead; `Close` retains its independent fresh-revision,
dirty/ignored-data and race checks before any removal.
The lifecycle contract limits inspection of a merged backend/frontend pair to
12 Git subprocesses (previously 16). Compare timings on the same worktrees and
alternate baseline/current runs; wall-clock timing is diagnostic, not a release gate.

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

1. **Merged:** branch is contained in `origin/main`, or no exclusive merge commit
   remains and its ordinary commits are patch-equivalent or its complete tracked
   tree equals the checked main tree, or the historical integration proof below
   succeeds; the worktree is clean and the session
   worktree and local branch are removed.
2. **Review pending:** clean committed branch/PR, named owner, exact SHA, and a
   stated merge or discard decision. It remains a worktree until resolved.
3. **Intentional WIP:** a named `wip/` branch with a recovery commit, owner, and
   next decision. It is never a deployment source. Raw uncommitted WIP is not a
   durable handoff state.
4. **Reviewed superseded:** the task owner has verified that current published
   behavior or an explicit current policy replaces every proposed change, with
   no needed work or applicable HOLD left. `Retire` removes the clean checkout
   while retaining its original branch, a named recovery ref and a verified Git
   bundle. This is a recorded supersession decision, not a claim that the old
   commits were merged. Preservation alone does not satisfy this state.

For the merged state, the close command performs a full preflight and refuses
dirty, foreign, or unmerged worktrees before deleting anything. A squash or
cherry-pick merge is accepted when `git rev-list --merges origin/main..HEAD` is
empty and either `git cherry origin/main HEAD` contains no `+` commit or the
complete tracked trees at the recorded main and HEAD SHAs are identical. The
latter recognizes multiple ordinary commits squashed into one without resetting
the branch; published original commits remain on their existing remote ref.
`git cherry`
does not prove merge-resolution equivalence, so an exclusive merge preserves
the branch even when its ordinary commits are equivalent. `Close` binds this
check to the recorded main and HEAD SHAs, then rechecks the registered path,
branch, HEAD, clean status and ignored data immediately before removal. Branch
deletion compares the recorded SHA atomically; a concurrent advance is preserved
and reported for recovery. Keep exclusive ownership until cleanup finishes.

For a historical squash after main has advanced, first identify the actual
merged PR and its integration commit. `Close -ExpectedIntegrationCommit <40-SHA>`
requires one repository and an explicit `-ExpectedBranch` or
`-ExpectedDetachedHead`. It independently requires that integration commit to be
an ancestor of the freshly checked main and proves either the complete tracked
trees are identical or the aggregate branch change exactly matches that
single-parent integration commit. The latter compares paths, file modes and full
before/after blob IDs from the unique merge base, including binary files and
merge resolutions; it does not rely on whitespace-insensitive patch IDs or PR
status alone. Extra unmerged changes, ambiguous bases and unrelated integration
commits fail closed. For bundled or independently cherry-picked work, it also
accepts a conflict-free `git merge-tree` from a unique merge base only when the
resulting tree exactly equals the specified published commit. This proof refuses
configured custom merge drivers and disables merge normalization. This proves that
merging the branch would add no content; the real index and checkout are untouched.
Later main changes may have superseded the integrated work;
this proves historical publication, not that the old behavior remains current.
Retain original tips under named recovery refs (and record their paths/SHAs) when
performing a bulk reconciliation, including tips whose remote branch was deleted.
Dirty state, ignored-data and race safeguards still apply. The lifecycle contract
tests cover historical squash/tree and merge-no-op success, unpublished/unrelated
proofs, extra unmerged work, conflicts and `-WhatIf` preservation.

### Retire a reviewed superseded proposal

Use this only within an authorized paused-session takeover/cleanup. Review the
original objective and every changed path against current owners, code and
relevant verification evidence. Integrate and release any necessary remainder
first. Do not use retirement for an unreviewed diff, a failing required gate,
unfinished feature, or an applicable platform/security HOLD. Do not manufacture
a merge solely to make cleanup accept an obsolete proposal.

Create a durable JSON review under `_artifacts/sessions/` with these fields:

- `version: 1`, `repository`, `session`, exact `head`, shared-ancestor `base`,
  exact current `reviewed_main`, `decision: "superseded"`, a concrete `reason`,
  and boolean `pending_work: false`, `hold: false`.
- `files`: exactly the paths changed from `base` to `head`, each with `path`,
  `disposition` (`already-present`, `replaced` or `obsolete`), `reason`, and a
  nonempty `evidence` array. Each evidence item identifies a `path` in the
  reviewed main and explains the replacement in `reason`. Record relevant
  verification/deployment receipts alongside the review when the original
  objective requires them.
- `recovery_ref`: a `refs/heads/wip/…` ref at the exact reviewed head;
  `bundle_path`: a durable bundle under `_artifacts/sessions/`; and its
  `bundle_sha256`. The bundle must advertise the reviewed head. A compact bundle
  may depend on retained canonical main history; record that prerequisite and
  restore with `git fetch <bundle> <recovery-ref>` into a repository containing it.

Invoke the fresh helper with `-Action Retire`, one `-Repository`, `-Session`,
`-WorkspaceRoot`, exact `-ExpectedBranch` or `-ExpectedDetachedHead`,
`-RetirementRecord` and the reviewed file's SHA256 as `-ExpectedReviewHash`.
The record and bundle must be outside every session's disposable `scratch/`
directory; the helper rejects evidence that a later session close could erase.
`-WhatIf` runs the evidence checks while retaining the checkout and refs.
The helper validates coverage, published evidence paths, the current main and
head, the recovery ref, bundle hash, prerequisites and actual object pack. It
rechecks review/recovery evidence at removal and preserves the ordinary clean,
ignored-data, registered-path, branch and HEAD guards. Both original branch and
recovery ref survive; ignored user data must first be retained outside the tree.
Only owned marked scratch is disposable. `SESSION_WORKTREE_RETIRED` identifies
this outcome separately from `SESSION_WORKTREE_CLOSED`.

These checks bind the review to exact recoverable content; they cannot decide
semantic equivalence. The task owner remains responsible for the per-path
decision and for completing actual product work. The lifecycle contract tests
exercise invalid/pending/held reviews, missing evidence, dirty source, corrupt
bundle data, recovery-ref races, WhatIf and successful recoverability.

`Close` clears ignored backend Python caches (`.pytest_cache/`, `.ruff_cache/`,
and `__pycache__/`) and frontend dependency/build outputs (`node_modules/`,
`dist/`, `.vite/`) after verifying every ignored path. Git unlinks dependency
junctions without deleting their shared targets. Other ignored data, including
`.env` files and test evidence, still blocks closure before either paired
worktree is removed. Move needed test evidence into the session's durable
artifact directory before closing; never discard a failing gate's evidence.
Then close a merged session promptly:

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

A reused owned worktree may have a later `codex/*` branch that no longer matches
its original session name. After confirming the exact registered path and that
no active task/process needs it, pass `-ExpectedBranch <exact-current-branch>`
with `Close` and one repository. A missing or mismatched explicit branch still
refuses a reused checkout; dirty, unmerged, ignored-data and remote-integration
checks remain unchanged. This does not authorize closing another task's checkout.
`Close` validates integration against freshly fetched `origin/main`, not the
possibly stale canonical checkout. After that fail-closed preflight succeeds it
removes the exact session branch even when concurrent work intentionally keeps
the canonical `main` behind the remote.

## Session output lifecycle

`Start` prints `SESSION_ARTIFACTS` and creates
`_artifacts/sessions/<session>/scratch/` with an ownership marker. Use the parent
session artifact directory for durable summaries, exact SHAs, release/QA evidence
and needed screenshots. Use `scratch/` only for regenerable build intermediates,
conversion scratch files and one-off download staging. Source code, user materials,
credentials, WIP recovery commits and required evidence never belong in scratch.
Shared SDKs, package stores and browser installations remain shared; do not copy
or reinstall them for every task.

After the final registered repository in a clean merged session closes, `Close`
removes that session's marked scratch directory. It preserves durable siblings,
foreign/unknown scratch, linked roots, unmerged work and dirty worktrees.
`-WhatIf` changes neither worktrees nor scratch. Cleanup errors are visible;
if a file lock leaves scratch after closure, release the owning process, verify
the marker and absence of registered worktrees, then remove only that exact
scratch path. Never kill another task's process or delete an unknown directory.
Existing unmarked artifact folders are not adopted or expired automatically.

Each repository's `pr-cache-cleanup.yml` removes caches only for numeric
`refs/pull/<number>/merge` references whose PR is currently closed. Main, tags,
other branch refs and open PR caches remain reusable. The closed-PR event handles
normal completion; a daily sweep handles late cache uploads. The write-scoped
workflow never checks out or executes PR code. Listing, state lookup or deletion
errors fail the job. Actions artifacts and required release evidence are not
cache-cleanup targets. Existing artifact retention and release gates still apply.
The backend native-image check also disables unused `.dockerbuild` record uploads
while retaining its build and verification job logs.

## Local disk capacity

Completed detached worktrees require an explicit reviewed identity. After
`Inspect`, close one exact repository with `-ExpectedDetachedHead <40-character
HEAD SHA>`. This option is valid only for `Close`, cannot be combined with
`-ExpectedBranch`, and refuses named branches, changed HEADs, dirty/unmerged work
and unknown ignored data. `-WhatIf` performs the same preflight without deleting
anything. Registered paths, fresh main, integration and race checks still apply;
detached cleanup never deletes a pre-existing named branch. Immediately before
removal it creates a unique `wip/detached-close/` recovery branch and rechecks
the reviewed SHA. A concurrent commit aborts cleanup and retains that branch;
successful cleanup removes only its own unchanged recovery ref with compare-and-
delete. Preserve unresolved candidates.

Keep the local machine for editing, review and lightweight checks. Reuse the
existing Codespace for dependency-heavy installs, builds and browser suites;
this is the default before the local disk becomes full. Keep one primary remote
development environment and create another only for an isolation requirement.
Do not lower production warm capacity or skip tests to save development costs.

Check physical free space before starting local work. `Start` warns below 20 GB
and refuses below 10 GB unless explicitly limited to lightweight recovery.
Directory totals can include many hardlinks and are not a savings estimate:

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

## Remote development when local space is low

Run dependency-heavy development in a GitHub Codespace created from
`academy-backend/main`; this is mandatory when C: has under 10 GB free. This moves
its working files,
package installs, builds, and owned worktrees to remote storage; Git alone is
source control and does not execute builds. Use the existing Codespace if it is
available. Use a 15–20 minute idle timeout and stop it after work; review its
compute and storage usage in GitHub. A 7-day retention period is suitable only
after completed work is pushed and recoverable; never shorten an existing
environment's retention without checking its uncommitted and unpushed work.
A stopped Codespace retains files until its
retention deadline, so push completed branches and PRs before that deadline.

In the Codespace, keep `/workspaces/academy-backend` and, for frontend work,
`/workspaces/academy-frontend` as clean canonical checkouts. Clone the frontend
only when needed:

```bash
git clone --filter=blob:none --single-branch --branch main \
  https://github.com/guswls3028-art/academy-frontend.git \
  /workspaces/academy-frontend
```

Install dependencies only for the current task.
Authenticate the remote Codex CLI with the user's account; never commit its
authentication file. The local ChatGPT Web bridge configuration does not carry
over to the Codespace. Verify the selected remote model and runtime before
claiming Web Pro delegation there.

The Windows `session-worktree.ps1` uses `C:\academy` paths and does not run in
the Linux Codespace. Preserve the same ownership and release rules with Git's
native worktree commands. From a clean canonical repository, for each needed
repository:

```bash
slug=example-01a0d31f
repo=/workspaces/academy-backend # or /workspaces/academy-frontend
git -C "$repo" fetch origin main
mkdir -p "/workspaces/_sessions/$slug"
git -C "$repo" worktree add -b "codex/$slug" \
  "/workspaces/_sessions/$slug/$(basename "$repo")" origin/main
git -C "$repo" rev-parse origin/main
git -C "/workspaces/_sessions/$slug/$(basename "$repo")" status --short
```

Use a unique slug and record the base SHA. Work, commit, push, and run focused
checks inside only that owned remote worktree. Hand off exact SHA and CI to the
release owner; the production source and gates are unchanged. Keep review-pending
worktrees. After integration, fetch `origin/main`, verify the worktree is clean
and `git cherry origin/main HEAD` has no `+` commits, then remove only the exact
owned worktree and branch. Preserve unmerged or dirty work. Stop the Codespace
with `gh codespace stop -c <name>` after remote commands finish.

## Verification

The backend quality gate runs the lifecycle fixture on Windows, including
exclusive merge resolutions and branch advances during cleanup. Local checks
use the same script; a passing parse alone does not verify deletion safety.

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

## Web bridge recovery

Load only for an observed bridge failure that needs recovery. Not a routine
preflight or required extra review. Preserve other tasks and managed controls.

If a Web follow-up fails with `MissingTrustedCodexEnvironmentError` / missing
`cwd` in trusted context, preserve its diff and stop sending the same request.
The native bridge could not establish the current turn's trusted workspace;
healthy browser/proxy checks and explicit model selection do not prove that
workspace context is present. Create one fresh bounded worker through the native
agent interface and verify a harmless file operation in the genuine workspace
before continuing. Carry forward relevant paths and commits, not full history.
Never forge environment-context text, weaken trusted-context validation, or
restart a healthy shared launcher for this error. The 2026-09-29 failure was
recovered through a fresh Web Pro worker's successful native read; the exact
historical omission/compaction cause and any released bridge fix were not proven.

For a launcher-backed installation, HTTP `/healthz` alone proves only the proxy
listener. Recovery must also pass `doctor --json`, `browser check`, and one actual
connector file/tool operation. Do not start standalone `serve` as a replacement
for a missing launcher browser. An owned orphan proxy may be gracefully drained
and shut down only after the official control endpoint confirms zero HTTP and
browser turns; resume it if idleness cannot be proved. Preserve an active launcher,
unknown listener and other tasks. Start the installed launcher hidden, retain its
existing authentication/model/approval settings, then verify the browser, proxy,
tunnel and actual selected Web model before dispatching consequential work.
