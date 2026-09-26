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
Keep the QA lease and alert the owner for exact recovery. A cancelled/failed
finalization can be retried as the restoration job: download by the immutable
artifact ID, verify the original snapshot hash, then acquire a new attempt's
lock after exact capacity ownership readback, then check sessions/queues under that lock. Never reconstruct an artifact name
from the retry attempt or release an older/different holder's lock. Main workflow
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
checks source identity and the exact-head successful backend quality workflow before AWS authentication and rechecks before publication.
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


### Durable publication recovery (review fixes)

Isolated QA persists metadata in DynamoDB under
__candidate_publication__:candidate:<run>:<original-attempt>.
The record contains version coordinates, source versions, acknowledged restore
versions and unresolved write intents; no environment values or credential hashes.
Every journal write uses one transaction with a live shared-lock ownership check
and a revision compare-and-swap. The restoration job addresses the original
snapshot's run/attempt, including on a later job retry. A missing record is not
assumed safe: both current SSM output versions must match the captured baseline.
A pending write with an uncertain acknowledgement stays HOLD. Known acknowledged
outputs can be restored, but no baseline activation follows an unresolved intent.
Successful restoration versions are recorded before readback; retry verifies
their values against the exact old versions without republishing them. Any
genuinely newer version still blocks rollback. Journals have no automatic TTL;
retain unresolved journals with their baseline coordinates.

Recovery lock acquisition recognizes the captured baseline and this exact run's
QA capacity even when replacement stopped midway (pending/stopped/multiple).
It tries renewal and then conditional acquisition for an expired/absent lock;
it cannot replace another live lock. Session/queue checks run after acquisition,
so a busy-slot HOLD does not strand an otherwise releasable shared lock.
Only after baseline health/smoke succeeds may owned residual capacity be removed.
Foreign capacity, active sessions, unresolved queues or unknown writes stay HOLD.

### Bounded QA window protocol — inactive pending runtime connection

candidate_qa_window.py is an offline-tested control-plane protocol, not an
enabled QA endpoint. No workflow calls it and no live adapter or window IAM grant
is provisioned. RuntimeAdapter fails closed: open, renew, completion and restore
authorization cannot succeed without trusted runtime observations. The A workflow
still performs infrastructure smoke and restores immediately. Do not hand that
ephemeral runtime to product acceptance owners.

A lease binds a random ID, exact owner task ID, workflow lock owner, source SHA,
all four runtime digests, QA-only SSM endpoint/profile, approved PR scope (509/511),
baseline SHA256 and started/expires/renewed timestamps. Duration is at most
90 minutes total; new admissions stop 30 seconds before expiry. Lock renewal and
lease revision advance in one DynamoDB transaction. Replayed IDs, losing CAS
renewals and unapproved scopes fail. Expiration never deletes the lease or
permits inferred restoration. Fresh readback includes all three queues,
all three worker in-flight ID sets, active sessions and cleanup0. Completion
also requires exact-bound immutable evidence for actual roles, save/reload,
tenant/permission and failure/recovery. Residual activity records HOLD and keeps
rollback coordinates. Queue0 by itself is insufficient.

Required execution-layer handoff (product files are not modified by #513):

| Owner | Exact file/interface | Acceptance |
| --- | --- | --- |
| Existing API/exam owner, 01a0d377-52af-7233-b3e0-422bd561c8c1 | New apps/api/middleware/candidate_qa_admission.py; registration in apps/api/config/settings/development.py; shared lease schema from scripts/v1/candidate_qa_window.py | QA-only enablement. Bind request to committed lease ID/revision, owner, candidate SHA/digests and permitted scope. Reject new mutation at expiry/drain with an explicit recoverable QA_WINDOW_CLOSED response. Direct-port or missing/forged lease must not bypass admission. Reads needed for recovery remain safe. Production behavior unchanged. |
| PPT + Matchup owners jointly, one designated writer | academy/framework/workers/ai_sqs_worker.py, academy/adapters/queue/sqs/ai_queue.py, academy/adapters/queue/sqs/tools_queue.py | Stop new receive before expiry/drain. Recheck lease after long polling, safely release unstarted receipts, allow already-started jobs to finish. Expose exact current job/receipt identity and freshly observed idle state for AI and Tools; do not infer idle from queue metrics. Existing source-owned output/data rules remain. |
| Release foundation owner, coordinated with messaging owner | apps/worker/messaging_worker/sqs_main.py | Equivalent admission/drain and in-flight readback for development messaging, keeping mock/no-provider boundary. No forced stop of an active job. |
| Release foundation owner after those commits | scripts/v1/candidate_qa_window.py RuntimeAdapter.observe/drain; scripts/v1/candidate_slot.py; .github/workflows/candidate-prepare.yml; candidate IAM templates | Read exact trusted runtime, enforce revision/expiry at API and workers, publish endpoint only after gates. Retest renewal race, expiry during work, connection loss, incomplete cleanup, foreign/replayed lease and all live accepted flows. Restore only after fresh closed-admission/session/worker/queue/cleanup0 readback; healthy baseline before QA termination. |

The runtime adapter must query a trusted control channel. User-supplied JSON,
route mocks, a successful health check or a typed Readback fixture is not live
proof. Runtime renewal must not allow a staged expiry to grant admission before
the corresponding DDB CAS succeeds. Endpoint access must be limited to the
admission-protected port, including SSM forwarding. Shared product-file changes
require the listed owners' coordinated commits, followed by exact-artifact
integration; #513 remains draft/HOLD until these connections and required CI pass.

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


### Runtime helper integration contract (stacked admission work)

The shared apps/infrastructure/qa_lease.py helper is opt-in only. Normal
development/production creates no QA client/thread. A partial QA configuration
fails closed. ACADEMY_QA_MODE=isolated-qa, runtime environment development,
pinned lease ID and binding SHA256 are required. The binding now also includes
exact disposable tenant_ids and message_key_version. The live lease and
shared lock are read atomically; every admission uses the committed revision.

Use get_admission_gate(kind) for one process-local gate, not a new instance per
job. admit() returns fresh lease metadata; assert_tenant() accepts only an
authoritative resolved tenant in that lease. begin(operation_id, tenant_id=...,
message=..., job_id=...) records only a hash of the full job/receipt identity,
lease revision, tenant and signed message ID. The API's exact authentication
bootstrap may use purpose='auth' without a tenant only before the separately
owned post-auth membership/token-binding gate. This does not authorize enqueue.

Producers call stamp_message(payload, tenant_id, job_id=..., queue_kind=...)
using the authoritative job tenant. Caller stamps are overwritten. The signature
covers the canonical body hash, job/destination, lease/owner/attempt/revision,
candidate images/source, tenant, random message ID, issue time and expiry.
validate_message requires exact current revision and matching authoritative
job/tenant before processing. Existing in-flight work may finish after expiry;
new receipts must be released without execution. Invalid/stale receipts create
nonsecret HOLD evidence, stop new admissions for that process and remain visible
for owned recovery rather than being silently deleted.

Nonce claims use conditional DynamoDB transactions, including the current lease
revision. Same-identity in-flight duplicates raise QaMessageInFlight; completed
duplicates raise QaMessageCompleted. A successful handler/callback explicitly
calls complete_message(payload); otherwise context exit records retryable so
legitimate callback retries can redeliver. An uncertain claim/outcome retains
HOLD evidence. No crashed in-flight claim is reclaimed by an assumed timeout.

The runtime reads only the pinned SecureString version at
/academy/qa-leases/<lease-id>/message-signing-key:<version> for signing/verification.
No key is logged or stored in the activity/journal artifacts. Offline fixtures
inject a synthetic key. No actual key or IAM grant has been created. Runtime
readback must match fresh process files to the actual expected containers/process
inventory; a file alone or missing worker is not evidence of idle capacity.


### QA state and acceptance cutoff

OPEN is stored as active. It permits fresh bound authentication, mutations and
new jobs before the 30-second admission cutoff. All role/result, save/reload and
cross-role evidence must finish by the hard expiry. begin_drain records draining
atomically and preserves drain_from_revision for previously issued JWTs. While
draining and before hard expiry, only safe GET with an existing valid bound JWT
is permitted; no login, refresh, mutation or new receive. The API owner enforces
post-auth tenant membership and bounds token TTL by the lease hard expiry.
inspect_lease() is fresh metadata, not admission or authentication permission.

CLOSED/expired permits no product authentication. Only trusted control-plane
readback, retained artifacts and owned cleanup/restoration remain. Missing
acceptance evidence at the cutoff is FAIL/HOLD. No recovery credential extends
product access. Completion must precede trusted restoration and requires the
closed-admission, worker/session/queue and cleanup0 proofs described above.
