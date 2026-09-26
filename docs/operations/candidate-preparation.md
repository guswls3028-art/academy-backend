# Isolated candidate preparation and promotion

## Status and owner

This is an additive, opt-in infrastructure path. The release owner enables
`ACADEMY_CANDIDATE_ENABLED=true` only after the prerequisites below have exact
readback. An absent flag or absent new credential does not change normal main
push/manual deployment. The legacy publishers remain the default branch.

Implementation owners are `candidate-prepare.yml`, `candidate_runtime.py`,
`candidate_prepare.py`, `candidate_manifest.py`, the candidate IAM templates,
and the optional candidate inputs in `v1-build-and-push-latest.yml`.
Existing development/preprod, production migration, health, rolling replacement,
runtime verification, last-successful manifest and latest-alias gates remain
mandatory. Follow [persistent development](persistent-development-runtime.md)
and [deployment modes](deployment-modes.md) for the existing executors.

## Two source boundaries

| Path | Source and identity | Image/runtime boundary | May promote? |
| --- | --- | --- | --- |
| A: isolated-qa | Exact open same-repository PR head; controller is exact current main; environment candidate-qa | Six academy-qa-* ECR repositories; academy-api-qa profile/role; development DB, queues and R2 only | Never |
| B: production | Candidate source and controller are both exact current remote main; environment candidate-production | Six existing immutable academy-* image repositories; ordinary isolated development profile | Only the exact prepared artifact, through all existing gates |

A uses the single persistent development slot, replacing its former instance
only after the new instance passes the baseline gate. It does not leave a second
worker pool consuming the development queues. The QA profile cannot pull
production repositories. SSM commands are restricted to launch-tagged
`QaMode=isolated-qa` instances. Lifecycle tagging may change only Lifecycle and
VerifiedReleaseId, so it cannot relabel an old, more privileged instance into
the QA command scope. Production EC2/ASG/ALB/Batch mutations are not permitted.

Both paths serialize with `academy-production-mutation` and the existing
DynamoDB partition `__deployment_control_v2__`. The run/attempt owns the lock;
registration and artifact publication finish before release. Do not interrupt
another task or reuse its lock owner. The preserved development slot is an
intentional runtime, not temporary QA residue.

PR #509 retains its pre-merge functional HOLD: actual PPT/Matchup/provider,
save/reload, permissions, tenant isolation, failure/recovery and disposable-data
cleanup must pass in A. Infrastructure smoke never clears that HOLD.
After the owner clears it and merges, B builds exact-main images. Only unchanged
functional inputs/tree evidence may be reused; freshness, manifest ancestry,
environment boundaries and official gates always rerun. Frontend #591 remains
ordered after #509 production/readback. No other product HOLD is changed here.

## QA slot lifecycle and restoration

A requires a named `slot_owner`. Before taking the lock it checks the existing
slot lease, active SSM sessions and all three development queues (visible,
in-flight and delayed messages). Busy, missing or ambiguous readback fails closed;
no session is terminated and no queue is drained. After acquiring the shared lock
it checks again and snapshots the baseline's exact image URIs, instance/profile/
capacity, release ID and pinned API/worker environment versions.

Those coordinates are nonsecret launch tags written by the development executor.
An older instance without complete coordinates cannot be guessed into a baseline.
First establish a verified baseline through the normal owned deployment path.
An existing SlotLeaseOwner belonging to another task blocks every replacement,
including the legacy path. Named QA capacity carries QaMode and SlotLeaseOwner.

The baseline snapshot is uploaded before any candidate launch; its SHA256 is a
job output. The QA job retains the shared lock across its finalization job.
After success or failure, a separate trusted-main restoration job assumes the
candidate-production identity, verifies that exact snapshot and owner/session/
queue state, restores the previous SSM values, and restores the original image/
profile through the same health gate. The QA identity never gains PassRole for
the more privileged baseline profile. If the original instance was untouched,
the restoration job reuses it and runs its smoke rather than creating capacity.

Final readback requires the exact original versions/digests/profile, one active
baseline, idle queues, no active session, and zero live owned QA capacity/lease
tags. Per-run IAM roles/queues are never created; the preprovisioned foundation
roles/repositories remain intentional resources. Infrastructure smoke creates no
DB rows and verifies deletion of its own file/R2 artifacts. Product-specific
data cleanup remains a separate acceptance gate.

An expired/lost lock, another owner, active session, failed candidate termination
or failed restoration blocks completion. Do not force recovery or claim cleanup0.
Keep the QA lease and alert the owner for exact recovery. Main workflow
concurrency and the explicit lease guard prevent a following deployment from
silently replacing that held QA slot.

The current A workflow proves the infrastructure lifecycle and restores the slot;
it does not issue a persistent QA endpoint or claim the pending product/provider
acceptance. Approved product QA must run inside this owned lifecycle before
finalization. No product HOLD is cleared by preparing an artifact.

## Independent environment sources

The new publisher never reads `/academy/api/env` or `/academy/workers/env`.
There is no fallback to copying a production environment.

- Development: `/academy/api/development/base-env`,
  `/academy/workers/development/base-env`,
  `/academy/api/development/db-credentials`,
  `/academy/r2/development/credentials`.
- Optional approved provider capability:
  `/academy/providers/development/gemini-api-key`.
- Preprod: `/academy/api/preprod/base-env`,
  `/academy/api/preprod/db-credentials`,
  `/academy/r2/preprod/credentials`,
  `/academy/api/preprod/cdn-credentials`.

Sources are exact SecureStrings, with Environment and Owner tags. Base sources
also require Purpose=candidate-base-config and SchemaVersion=1. The base
allowlist is executable in candidate_runtime.BASE_KEYS. Required nonsecret
fields are AWS_DEFAULT_REGION=ap-northeast-2, independently reviewed DB_HOST,
DB_PORT=5432 and DB_SSL_MODE=require. Unknown keys, credentials in the base,
non-string/multiline values, and API/worker DB endpoint divergence fail closed.

The dedicated DB role/password and R2 credentials remain separate. Development
uses only academy-development-artifacts; its CDN signing secret is newly
generated in memory and never copied from production. Preprod retains the
existing read-only academy-video/CDN playback contract: its read-only credential
and dedicated CDN credential must be provisioned through their owning approved
process. The publisher must not obtain them by reading a production env blob.

Metadata preflight returns version numbers, not secret values. Publication fetches
exact name:version pairs, composes an allowlisted environment in memory, writes
the existing stage output names, and verifies exact version/value readback.
Receipts contain versions and public release coordinates only. Values must never
enter command arguments, reports, Git, artifacts or logs.

Gemini is optional for infrastructure preparation and mandatory when
provider_qa=true. A missing disabled provider is recorded unavailable, never pass.
The API receives no Gemini key; the worker environment carries the approved
development key. A shell-capable preparer is therefore secret-capable: container
placement is not a credential isolation boundary. Header-auth source checks must
pass before injection. Text/vision pins remain gemini-2.5-flash-lite and
gemini-2.5-flash; there is no model substitution. Access in the separate provider
project remains an actual post-approval gate, not an offline test result.

## Prerequisites and activation

1. Create candidate-qa and candidate-production GitHub environments with exactly
   one selected deployment branch policy: type=branch, name=main. Do not replace
   the existing production environment rules or bypass review.
2. On clean exact remote main, acquire the existing deployment lock, then run
   the default read-only plan:
   `python scripts/v1/converge_candidate_prerequisites.py --output candidate-plan.json`.
   Review it, apply with `--apply --lifecycle-plan candidate-plan.json`, and use
   `--readback` before release. `--provision-environments` with Apply creates
   missing main-only environments; it never rewrites existing protection rules.
   The helper creates only owned candidate IAM roles/profile and six QA ECR
   repositories, plus the exact nonproduction source-read policy on the existing
   release role. It creates/reads no secret values. Existing foreign roles are
   rejected rather than adopted. Release the same owner lock in a finally block.
3. Candidate ECR repositories must have no independent native lifecycle policy.
   The plan saves each exact existing document and its SHA256. Apply removes a
   policy only when the reviewed plan still matches, under the shared lock,
   and verifies absence. Preserve that plan for exact policy restoration.
   A missing/stale plan fails closed; no image is deleted by this operation.
   The immutable release tag permits at most the existing latest exclusion.
4. Provision/review independent source records and ownership tags. Set the
   nonsecret repository variable ACADEMY_PRODUCTION_DATABASE_NAME to the
   reviewed actual production DB name used exclusively for the denial check.
   Existing isolated output parameters must exist so rollback has exact versions.
5. Verify metadata, active IAM/trust/environment policies, and URI deny boundaries.
   Then enable ACADEMY_CANDIDATE_ENABLED. Do not dispatch with provider_qa=true
   until the external credential and cost scope are approved.

The foundation code alone does not establish that these cloud prerequisites,
provider access, or product QA have passed. Keep those states explicit.

## Prepare and promote

Dispatch from main with mode=isolated-qa, exact PR source_sha and pull_request,
or mode=production and exact main source_sha (no PR input). The trusted controller
checks source identity before AWS authentication and rechecks before publication.
Docker sources come from the explicit candidate checkout; controller scripts
come only from main. Runtime builds use the freshly built base digest; the base
Dockerfile already pins its upstream image digest. This is immutable artifact
identity, not a claim that package repositories make builds bit-reproducible.

The artifact academy-candidate-<run>-<attempt> contains only release-manifest.candidate.json.
It binds workflow, repository, controller/source SHA, run/attempt, six exact
repository/digest/tag entries, development evidence, model pins and 30-day expiry.
The development evidence proves the exact image URIs, isolated runtime checks,
and synthetic file/R2 cleanup. It does not claim product-specific DB cleanup.

Promote by dispatching the existing V1 workflow on main with all three inputs:
candidate_artifact_id, candidate_artifact_digest (sha256: prefix), and
candidate_release_id. The normal build is skipped only for this explicit mode.
Restoration verifies the GitHub artifact metadata/archive SHA256, successful
trusted preparation run/attempt, bounded ZIP members, expiry, exact current main,
and ECR immutable tag-to-digest readback. It repeats validation under the shared
lock immediately before production migration. The normal ECR scan, independent
development and preprod gates, temporary preprod termination, compatible
migration, healthy replacement, runtime readback and successful publication
remain in the existing production workflow.

Do not use an A artifact in B, change a manifest, rebuild between gates, or
replace failed provider QA with an infrastructure smoke success.

## Retention, failure and rollback

The Actions artifact retains 30 days. Each candidate image has an immutable
candidate-until-<expiry>-<run>-<attempt> tag. The existing ECR cleanup protects
unexpired candidates even if their sha-* tag is older than keep-N, and follows
the complete child-index closure. Missing/malformed protected manifests or
retention tags fail closed. Existing live-runtime digest protection remains.
QA repositories are not automatically added to the production cleanup inventory;
their removal requires exact owned targets after retention and runtime checks.

A failed environment publication restores acknowledged owned versions.
A failed runtime keeps the prior active instance and terminates its failed
candidate; its workflow restores the previous parameter versions. Rollback
refuses if another owner has since published a new version. An ambiguous AWS
write/rollback failure blocks activation and requires exact parameter/version
readback under the same lock; never guess that cleanup succeeded.

Disabling ACADEMY_CANDIDATE_ENABLED stops new preparations without changing the
legacy deployment path. Do not delete shared queues, development DB/user data,
or unexpired artifacts/images as rollback. Production rollback continues to use
the existing last-successful release procedure and retains old capacity until
replacement health proves continuity.

## Verification and external approval package

Offline gate:
`python -B -m unittest scripts.v1.test_candidate_foundation -v`.
Run the existing infrastructure safety, candidate-env, workflow governance,
source-freshness and frontend-development-IAM contracts for changed executable
boundaries. Required PR CI also runs. These are not live IAM/provider evidence.

Before requesting credential approval, the release owner supplies the final
foundation commit/PR/CI, prerequisite readback and exact outstanding source names.
One bounded external provisioning approval must identify:

- an isolated Gemini project and named human project/billing/rotation owner;
- only the development Gemini key above, exact two model IDs, expiration/rotation
  no later than 90 days, and the approved synthetic-only fixture set;
- the independent preprod read-only CDN credential if not already provisioned;
- the billing tier, provider-enforced quota where supported, run/request limits,
  budget notification threshold and hard-stop mechanism (a budget alert alone is
  not a spending cap);
- SecureString storage and no key transfer through chat/Git/artifacts;
- revocation/rotation, environment rollback and exact disposable QA cleanup0
  acceptance before clearing the product HOLD.

Do not create projects/keys, retrieve credentials, call providers or mutate
tenant/user data merely to complete this package. Those actions remain pending
until their exact approved scope exists.
