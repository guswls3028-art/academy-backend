# SSM Runtime Environment Parameters — JSON Schema (Source of Truth)

## API parameter: `/academy/api/env`

The production API reads one SecureString JSON object. In addition to the
database, cache, AWS, and application keys maintained by the v1 deployment
scripts, video playback requires:

| Key | Contract |
|-----|----------|
| `CDN_HLS_BASE_URL` | Exactly `https://cdn.hakwonplus.com` |
| `CDN_HLS_SIGNING_SECRET` | Non-empty secret with at least 32 characters |
| `CDN_HLS_SIGNING_KEY_ID` | Active signing key identifier, currently `v1` |
| `TENANT_DB_USAGE_ENABLED` | `true` only during an approved sampled DB telemetry window; otherwise `false` |
| `TENANT_DB_USAGE_SAMPLE_RATE` | Decimal string from `0.01` to `1.0`; pilot workflow allows `0.05` or `0.10` |
| `TENANT_DB_USAGE_SLOW_REQUEST_MS` | Integer threshold; pilot control fixes this to `1000` |

Production settings fail closed when the canonical CDN URL or signing secret is
missing. `Sync-ApiEnvFromSSOT` validates the same contract before writing the
parameter or refreshing API instances, so a deployment cannot silently fall
back to an unsigned R2 URL.

The production-approved product-usage pilot control may update only the three
`TENANT_DB_USAGE_*` keys with a preserve-and-readback script. Its GitHub OIDC
role has `ssm:PutParameter` only on the exact `/academy/api/env` ARN; it does not
grant a wildcard SSM write.

### Common Solapi runtime identity

`SOLAPI_API_KEY`, `SOLAPI_API_SECRET`, and `SOLAPI_SENDER` must be present and
exactly equal in the plain-JSON API parameter and base64(JSON) worker
parameter. Equality between the two documents is not enough: the configured
sender must equal the single normalized sender returned by the provider's
ACTIVE sender endpoint for those exact credentials. Raw credentials, sender
numbers, and decrypted parameter documents are never written to local backup
files or operator output.

`scripts/v1/reconcile_common_alimtalk_sender.py` is the only sender drift
repair path. It changes only `SOLAPI_SENDER`, preserves the decoded key sets and
every other value, and refreshes only Messaging and API under the shared
production mutation lock. A failure before runtime refresh restores the exact
original parameter values. Every write must advance the observed SSM version by
exactly one and preserve the exact KMS KeyId; a concurrent version, raw value,
or KMS drift retains the lock instead of claiming success or overwriting again.
A failure after refresh starts keeps the target configuration and lock for
forward convergence.

`OWNER_TENANT_ID` has a compatibility runtime default of tenant 1, but the two
production parameters must make that same fixed value explicit. The only
supported repair is `scripts/v1/reconcile_common_alimtalk_owner_tenant.py`; it
does not accept an owner argument and permits only an absent key or the exact
string value already represented by the runtime default. It preserves every
other key and value, including the three common Solapi keys. Apply writes only
the missing owner key, validates exact version/raw/KMS state, and refreshes
Messaging then API. When both SSM documents are already explicit, apply still
requires InService HMAC, API health, and post-read queue evidence; a stale or
missing runtime owner is forward-converged through the same Messaging then API
refresh. Runtime readback exposes only boolean/HMAC evidence. A
pre-refresh partial failure restores the exact original documents; a refresh,
readback, concurrency, or rollback ambiguity retains the shared lock for
forward convergence. Rollback reasserts exact shared-lock ownership before any
compensating write, and lock loss after a write retains the lock record without
attempting an unowned rollback. `check-workers-sender-queue.ps1` reports separate
API/worker configured, equality, and expected-value booleans without printing
the owner value; only JSON string `1` is accepted, never a numeric, null, or
string-coerced value.

Every supported standalone writer for `/academy/api/env` or
`/academy/workers/env` must hold the same shared production mutation lock for
its complete read-transform-write boundary. Deploy-owned bootstrap/sync writers
assert the deploy lock immediately before publication and rollback; delegated
worker restart inherits one owner instead of acquiring a nested lock. The
generic `core/ssm-safe-update.ps1` refuses protected runtime writes unless the
runtime-env lock is already held. Its decrypted source and transformed payload
exist only in a process-owned temporary directory that is deleted on both
success and failure. Direct `aws ssm put-parameter` runbook recipes are not a
supported mutation path.

## Preprod API parameter: `/academy/api/preprod/env`

This is a release-bound Advanced SecureString version, never a mutable alias for
production. It keeps `DJANGO_SETTINGS_MODULE=apps.api.config.settings.prod` and
the production-shaped CDN/R2 playback read path, but must change or remove all
side-effect authority before publication:

| Boundary | Contract |
|----------|----------|
| Runtime identity | `ACADEMY_RUNTIME_ENV=preprod`, exact `ACADEMY_PREPROD_RELEASE_ID` |
| Database | `academy_api_preprod`, dedicated `academy_api_preprod_app` role, production DB `CONNECT` denied |
| Signing | derived preprod `SECRET_KEY` and `MESSAGING_TENANT_BINDING_KEY`; no production values |
| Messaging | `SOLAPI_MOCK=true`, credential fields empty, `MESSAGING_DRY_RUN_TRIGGERS=*` |
| Billing | Toss disabled and keys empty; bank transfer and billing-key writes disabled |
| External providers | OpenAI, Anthropic, VAPID private key, static AWS credential fields empty |
| CDN/R2 | signing config는 유지하되 production R2 key는 제거. `/academy/r2/preprod/credentials`의 `ACCESS_MODE=read-only`, production과 다른 key pair, 같은 video bucket만 허용 |

`Set-IsolatedPreprodApiValues` applies the boundary and
`Assert-IsolatedPreprodApiValues` verifies both the in-memory candidate and the
exact versioned SSM readback.

`/academy/r2/preprod/credentials`는 bucket-scoped Object Read/List 전용
Cloudflare R2 key를 담는다. JSON은 `ACCESS_MODE`, `R2_ENDPOINT`, `R2_REGION`,
`R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_VIDEO_BUCKET`을 포함한다. 코드의
`ACCESS_MODE=read-only` 검증은 credential 발급 권한 자체를 증명하지 않으므로,
운영자는 Cloudflare에서 대상 video bucket의 Object Read/List 외 권한이 없는
token인지 별도 readback하고 증거를 보존한다.

## Worker parameter: `/academy/workers/env`

## Format

- **Type:** SecureString
- **Value:** Either:
  - **Plain JSON:** Single-line JSON object (no legacy KEY=VALUE lines), or
  - **Base64(UTF-8 JSON):** Used by `ssm_bootstrap_video_worker.ps1` on Windows to avoid CLI quoting corruption. Entrypoint and verify scripts accept both.
- **Produced by:** the v1 deploy/resource scripts from `.env` or the approved release environment. No manual editing.

## Required keys

All Batch jobs (worker, netprobe, reconcile, scan_stuck) require these keys to be present and non-empty:

| Key | Description |
|-----|-------------|
| `AWS_DEFAULT_REGION` | e.g. `ap-northeast-2` |
| `DB_HOST` | RDS endpoint hostname |
| `DB_NAME` | Database name |
| `DB_USER` | Database user |
| `DB_PASSWORD` | Database password |
| `DB_PORT` | e.g. `5432` |
| `R2_ACCESS_KEY` | R2 access key |
| `R2_SECRET_KEY` | R2 secret key |
| `R2_ENDPOINT` | R2 endpoint URL |
| `R2_VIDEO_BUCKET` | R2 bucket for video |
| `API_BASE_URL` | API base URL (no trailing slash) |
| `INTERNAL_WORKER_TOKEN` | Shared secret for internal API |
| `REDIS_HOST` | Redis hostname |
| `REDIS_PORT` | e.g. `6379` |
| `DJANGO_SETTINGS_MODULE` | Must be `apps.api.config.settings.worker` for Batch |

## Optional keys

- `REDIS_PASSWORD`, `R2_PUBLIC_BASE_URL`, `R2_PREFIX`, `VIDEO_BATCH_JOB_QUEUE`, `VIDEO_BATCH_JOB_DEFINITION`

## Runtime contract

- **batch_entrypoint.py** reads this parameter, then parses as JSON (or base64-decode then JSON). Sets `os.environ`, validates required keys.
- If the value is not valid JSON (or valid base64(JSON)), or any required key is missing/empty, the entrypoint exits non-zero (no fallback).
- `DJANGO_SETTINGS_MODULE` must be exactly `apps.api.config.settings.worker` for Batch; dev/prod defaults are not allowed.
