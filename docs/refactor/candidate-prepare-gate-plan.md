# Candidate preparation boundary (draft)

This is a design contract for the pending PPT/Matchup integration. No candidate
workflow, GitHub environment, IAM role, Google key, or base configuration is
provisioned by this change. PR #509 remains Draft; the existing production
release workflow remains the only active deployment path.

## Exact nonproduction sources

`scripts/v1/candidate_prepare_sources.py` owns the metadata-only preflight.
It calls SSM `DescribeParameters` and `ListTagsForResource`; it does not fetch
parameter values or publish runtime environments. A future publisher must
finish this preflight before any write and may read only these exact sources:

| Stage | Nonsecret base config | Dedicated credential references | Provider secret |
|---|---|---|---|
| development | `/academy/api/development/base-env`, `/academy/workers/development/base-env` | `/academy/api/development/db-credentials`, `/academy/r2/development/credentials` | `/academy/providers/development/gemini-api-key` |
| preprod | `/academy/api/preprod/base-env`, `/academy/workers/preprod/base-env` | `/academy/api/preprod/db-credentials`, `/academy/r2/preprod/credentials` | None; external AI disabled |

Both base parameters must be versioned SecureStrings tagged with exact
`Environment`, `Purpose=candidate-base-config`, `SchemaVersion=1`, and a
nonempty `Owner`. The Gemini parameter additionally requires a separate
development Google project ID, owner, rotation owner and unexpired `RotateBy`
tag. The existing DB/R2 parameters have no owner or rotation tags; that is a
stabilization finding, not an excuse to use production credentials. Missing
or mismatched metadata fails before mutation. Parameter values and secret
tags never enter job summaries or artifacts.

The source module classifies runtime key **names** as nonsecret base config,
dedicated DB/R2 credential, provider secret, generated runtime identity, or
prohibited production-only. Unknown base keys fail closed. An operational
publisher still needs a complete reviewed allowlist for API and worker runtime
settings; the current metadata preflight is not a deployment gate by itself.
It must not read `/academy/api/env`, `/academy/workers/env`, or the legacy
derived `/academy/{api,workers}/{development,preprod}/env` as a fallback.
Gemini may be injected only into the isolated development API and AI worker
paths that call it; preprod has no Gemini key.

## Candidate identity and IAM skeleton

The separate preparation role should be `academy-gha-candidate-prepare`.
Its OIDC trust must match `aud=sts.amazonaws.com` and exact backend repository
`sub=repo:guswls3028-art/academy-backend:environment:candidate`. GitHub's
default environment subject does not also contain the branch. The candidate
environment must therefore allow **only exact main**, require its designated
reviewer, and prohibit administrative bypass. The trusted workflow checks
repository, `refs/heads/main`, exact remote main SHA, environment policy and
review readback before requesting credentials. Existing production role trust
and environment stay unchanged.

| Prepare permission | Resource boundary |
|---|---|
| ECR auth, image upload/readback and critical scan | Six exact Academy image repositories; no delete, repository creation or tag-policy mutation. `GetAuthorizationToken` alone uses `*` where required by AWS. |
| EC2 launch, describe, tag and terminate; SSM command/readback | Exact development and preprod AMI, subnet, security group, instance profile, release tags and candidate instances. No ASG/ALB/Launch Template production mutation. |
| `iam:GetInstanceProfile`, `iam:PassRole` | Exact existing development and preprod instance profiles/roles, only to EC2. No other IAM write or `sts:AssumeRole`. |
| SSM metadata/read/write, KMS decrypt | Exact new base configs, existing nonproduction DB/R2 credentials and versioned candidate env outputs. The GitHub role may inspect Gemini **metadata**, not decrypt its value. Runtime read of the key needs exact development instance-role scope; the current shared host cannot claim per-container IAM separation. |
| DynamoDB lock operations | Exact release-control table and a reviewed candidate lock partition key with `dynamodb:LeadingKeys`. The prepare lock must serialize conflicting releases and be released on success/failure. |

Before an IAM apply, the official convergence owner must resolve every ARN,
action, condition, and production deny against current SSOT and live policy;
simulate allowed and denied calls, record current/proposed hashes and a
rollback document, then read back the actual trust, attachment and effective
policy. A broad session policy on the existing production-capable role is not
an acceptable substitute. No AWS IAM change is authorized by this document.

## Prepare / promote contract still to implement

1. Main push of an exact reviewed SHA prepares six immutable image digests and
   verifies isolated development plus preprod, including termination and
   cleanup zero. It cannot run production migration, alias movement or deploy.
2. An immutable candidate artifact records main SHA, run/attempt, image
   digests, environment versions, test/cleanup evidence and its own checksum;
   retain it for 30 days and read back `expires_at`, digest and `head_sha`.
   Do not add a `ci-build.latest.md` report commit to main.
3. ECR cleanup must preserve each unexpired candidate digest and child
   manifests beyond the normal newest-ten count. Missing/expired/mismatched
   evidence blocks promotion; an ECR tag alone never authorizes it.
4. A separate protected production dispatch reacquires the shared lock,
   verifies remote main equals the candidate SHA and the exact artifact/ECR
   digest map, then runs the unchanged production compatibility, rolling
   health, rollback and manifest-publication gates without rebuilding.

The current development and preprod publishers derive their env blobs by
decrypting production env. A candidate publisher cannot reuse them. The
nonproduction base configs and dedicated Gemini key do not yet exist, so
candidate preparation and real Gemini seven-photo QA are blocked. A Google
project owner must identify a separate development project and create a
Generative Language API authorization key with project quota/spend limits;
never copy the production key. Store it only in the dedicated SecureString,
set owner/purpose/rotation metadata, and define readback and revocation before
enabling the workflow. The adapter sends it in `x-goog-api-key` rather than a
URL query parameter; focused tests prohibit key exposure through errors/logs.
