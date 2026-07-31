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

Production settings fail closed when the canonical CDN URL or signing secret is
missing. `Sync-ApiEnvFromSSOT` validates the same contract before writing the
parameter or refreshing API instances, so a deployment cannot silently fall
back to an unsigned R2 URL.

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
