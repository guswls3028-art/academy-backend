# V1.1.0 Deployment Architecture

**Version:** V1.1.0
**Date:** 2026-03-14 (checked 2026-07-13)
**SSOT Status:** Active

## 1. Service Decomposition

| Service | ECR Repository | ASG | Container Name | Purpose |
|---------|---------------|-----|----------------|---------|
| API | academy-api | academy-v1-api-asg | academy-api | Django REST API (Gunicorn) |
| Messaging Worker | academy-messaging-worker | academy-v1-messaging-worker-asg | academy-messaging-worker | SQS message processing |
| AI Worker | academy-ai-worker-cpu | academy-v1-ai-worker-asg | academy-ai-worker-cpu | AI task processing |
| Tools Worker | academy-tools-worker | academy-v1-tools-worker-asg | academy-tools-worker | deterministic document/PDF/PPT/spreadsheet conversion jobs |
| Video Worker | academy-video-worker | AWS Batch CE (`academy-v1-video-batch-ce-200gb`, c6g.4xlarge primary) | — | 영상 인코딩. 1 video = 1 Batch job. VCPU=8 / MEM=16GB / timeout=6h |
| Base | academy-base | — | — | Shared base image for all services |

**Note (2026-05-10, checked 2026-06-23):** Daemon mode 폐기. 모든 영상 인코딩은 AWS Batch standard queue/jobdef(`academy-v1-video-batch-queue`, `academy-v1-video-batch-jobdef`)로 1-shot 처리한다. long path는 폐기되었고, 실패/중단 복구용 ops 작업은 별도 ops queue/jobdefs(`academy-v1-video-ops-*`)로 관리한다. 현재 jobdef timeout은 6h이며, 실패/중단 케이스는 recover/reconcile/scan_stuck 계열이 재시도한다. ffmpeg는 `c6g.4xlarge` VCPU=8 + R2 병렬 업로드로 처리한다.

## 1.1 Public API Edge

- `api.hakwonplus.com`은 Cloudflare 프록시가 아니라 DNS-only CNAME으로 `academy-v1-api-alb`에 직접 연결한다.
- Public HTTPS는 ALB 443 listener가 ACM 인증서 `api.hakwonplus.com`으로 종료하고, listener 기본 action은 `academy-v1-api-tg` forward다.
- ALB 80 listener는 `HTTPS:443`으로 redirect한다. 운영 사용자/테스트 기준 API URL은 `https://api.hakwonplus.com`이며, plain HTTP가 Django까지 도달하면 drift로 본다.
- Cloudflare zone SSL mode는 Strict로 유지한다. API 레코드를 다시 proxied로 돌릴 때는 ALB HTTPS 443과 origin 검증을 먼저 확인한다.
- 운영 Django는 `172.30.0.0/16`만 신뢰 프록시로 인정한다. ALB 기본 append 형식의 X-Forwarded-For를 오른쪽부터 검사해 외부 요청이 넣은 선행 값을 무시하며, 감사 로그·공개 폼·내부 API IP 정책·로그인 제한이 같은 resolver를 사용한다.
- 로그인 제한은 LocMemCache가 아니라 RDS의 HMAC 버킷을 사용한다. 실제 IP는 분당 60회, tenant+로그인 계정은 5분당 10회로 API 인스턴스와 배포 재시작을 가로질러 공유하며 계정/IP 원문은 저장하지 않는다.
- SSOT 및 재현 스크립트: `docs/ssot/params.yaml`의 `api.acmCertificateArn`/`api.httpsSslPolicy`, `scripts/v1/resources/alb.ps1`의 `Ensure-Listener`/`Ensure-HttpsListener`.

## 2. CI/CD Pipeline Architecture

```
git push main
    |
    v
[detect-changes] ─── analyze git diff ──> outputs: build_api, build_video,
    |                                               build_messaging, build_ai,
    |                                               build_tools, force_full
    v
[run-lint] ─── ruff + submission lifecycle + refactor boundary deploy gates
    |
    v
[run-tests] ─── smoke tests deploy gate
    |
    v
[build-and-push] ─── build changed images ──> ECR (compat :latest + immutable :sha-XXXXXXXX)
    |
    |── (if API changed) ──> [run-migrations] ─── resolve SHA to digest ──> docker run manage.py migrate
    |                              |
    |                              v
    |── (if API changed) ──> [deploy-api] ─── pin LT to digest ──> ASG instance refresh
    |
    |── (if messaging changed) ──> [deploy-messaging] ─── pin LT to digest ──> ASG refresh
    |
    |── (if AI changed) ──> [deploy-ai] ─── pin LT to digest ──> ASG refresh
    |
    |── (if tools changed) ──> [deploy-tools] ─── pin LT to digest ──> ASG refresh
    |
    |── (if video changed) ──> [deploy-video] ─── Batch job definition revisions with SHA image
    |
    v
[verify-deployment] ─── healthz 200 + health 200 + ASG healthy instances
    |                    + tenant maintenance flag guard
    |                    + API-change student video playback chain smoke ──> PASS/FAIL
    |
    v
[notify-on-failure] ─── failure-only notification
```

## 3. Selective Build Logic

### Change Detection Rules

| Trigger Files | Builds |
|--------------|--------|
| `.dockerignore`, `docker/Dockerfile.base`, `requirements/{constraints,common,requirements}.txt`, `libs/`, `academy/`, `manage.py` | ALL images (force_full) |
| Worker 공통 import: `apps/{shared,support,core,infrastructure}/`, `apps/api/common/`, `apps/api/config/settings/worker.py` | ALL images (force_full) |
| Python package import roots: `apps/__init__.py`, `apps/{api,domains,worker}/__init__.py`, `apps/api/config[/settings]/__init__.py` | ALL images (force_full) |
| Django startup import: `apps/domains/*/{models.py,models/,apps.py,signals.py,signals/,__init__.py}` | ALL images (force_full) |
| `apps/`, `scripts/`, `docker/api/`, `requirements/api.txt` | API |
| `apps/worker/video_worker/`, `apps/support/video/`, `apps/domains/video/`, `apps/api/config/settings/worker.py`, `docker/video-worker/`, `requirements/worker-video.txt` | Video Worker |
| `apps/worker/messaging_worker/`, `apps/support/messaging/`, `apps/domains/messaging/`, `apps/api/config/settings/worker.py`, `docker/messaging-worker/`, `requirements/worker-messaging.txt` | Messaging Worker |
| `apps/worker/ai_worker/`, `apps/worker/omr/`, `apps/domains/`, `apps/support/ai/`, `apps/api/config/settings/(worker|base).py`, `models/`, `scripts/`, `academy/`, `libs/queue/`, `docker/ai-worker*`, `requirements/worker-ai*` | AI Worker |
| `apps/worker/tools_worker/`, `apps/domains/tools/`, `apps/domains/ai/queueing/`, `apps/support/ai/services/sqs_queue.py`, `academy/(application/use_cases/tools|domain/tools|adapters/tools|framework/workers|adapters/queue/sqs)/`, `docker/tools-worker/`, `requirements/worker-tools.txt` | Tools Worker |

`force_full` is a correctness boundary for code imported by more than one runtime. It builds all six images, including `academy-base`; service-specific paths retain selective builds. `workflow_dispatch` always performs a full build/deploy.
Change predicates use the `changed_matches` here-string helper instead of `echo | grep -q`; this avoids a `pipefail`/SIGPIPE false negative on large multi-commit push ranges.

### Build Output

Each image is tagged with:
- `:latest` — compatibility alias only; never deployment evidence
- `:sha-XXXXXXXX` — immutable source identity, first 8 chars of git commit SHA

Service builds resolve `academy-base` to a digest before `FROM`. Migration, API/Messaging/AI/Tools runtime, and all Video Batch job definitions resolve the run-unique SHA tag to `repo@sha256:...`. `deploy-api-and-verify-workers.ps1` verifies the last complete successful release manifest, waits for terminal refresh success, then compares its digests with Launch Template userdata, actual InService containers, and every active Video Batch job definition.

## 4. Selective Deploy Logic

Deploy jobs only run if the corresponding service was built:

```
deploy-api:       if build_api == 'true' || force_full == 'true'
deploy-messaging: if build_messaging == 'true' || force_full == 'true'
deploy-ai:        if build_ai == 'true' || force_full == 'true'
deploy-tools:     if build_tools == 'true' || force_full == 'true'
deploy-video:     if build_video == 'true' || force_full == 'true'
```

Dependencies:
- `deploy-api` waits for `run-migrations` to succeed (or be skipped)
- `deploy-messaging`, `deploy-ai`, `deploy-tools`, and `deploy-video` also wait for `run-migrations` success or an explicit skip; a failed migration blocks every runtime deploy
- `verify-deployment` waits for all deploy jobs
- `deploy-video` is included in the same workflow and runs when the video worker image changes

## 5. Zero-Downtime API Strategy

### ASG Instance Refresh

- **MinHealthyPercentage: 100%** (API) — 새 인스턴스가 healthy가 될 때까지 기존 인스턴스 유지. 502 gap 0건 보장.
- **MinHealthyPercentage: 0%** (workers) — workers tolerate brief downtime during replacement (no HTTP traffic)
- **SkipMatching: false** (API) — launch template 변경 없어도 실제 인스턴스 교체 수행
- **InstanceWarmup: 300s** (API), **120s** (workers) — API는 ECR pull/컨테이너 기동 편차를 흡수
- **HealthCheckType: ELB** (API) — 앱 크래시 시 ALB가 감지 → ASG 자동 교체. **EC2** (workers) — ALB 없음.
- **HealthCheckGracePeriod: 300s** (API) / **60s** (workers) — 새 인스턴스 부팅 중 조기 종료 방지
- **ALB deregistration delay: 30s** — in-flight 연결 drain 후 즉시 정리
- Scale-up 후 **ALB target health 실측 확인** (고정 대기 아닌 실제 healthy 2개 확인, max 5min)
- Old instances are drained and terminated only after new ones pass ALB health checks
- 평상시 API capacity는 SSOT `min=1 desired=1 max=3`이다. CI deploy는 refresh 직전에 일시적으로 `desired>=2` headroom을 만들고, refresh 성공 후 기존 desired baseline으로 되돌린다.
- API runtime scale-out/scale-in은 ASG target tracking(`ASGAverageCPUUtilization`, target 55%)이 담당한다.

### Deployment Sequence

1. The deploy job resolves its `sha-*` tag to an ECR digest and creates a new Launch Template version containing that digest. On the one-time legacy cutover, it first snapshots the actual running container digest into an immutable baseline version.
2. The ASG tracks `$Latest` after that guarded cutover and launches a new EC2 instance from the candidate version.
3. UserData installs Docker, logs in to ECR, pulls `repo@sha256:...`, fetches SSM env, and starts the container.
4. ALB health check passes on the new instance.
5. The old instance is drained and terminated.

## 6. Worker Deployment Strategy

Workers use the same ASG instance refresh mechanism as API but with:
- Shorter warmup (120s vs 300s) — workers don't serve HTTP traffic
- No ALB health check — workers are background processors
- **MinHealthyPercentage=0%** — workers tolerate brief downtime during replacement. Message loss is prevented by SQS visibility timeout (messages return to queue if not acknowledged)

Runtime scaling is split by worker:

- **AI** uses AWS/SQS CloudWatch scale-out alarms (`ai-worker-queue-high`, `ai-worker-queue-age-high`) plus API wake-up. Idle scale-in is worker-owned after live SQS depth is empty; `ai-worker-queue-low` is observability-only. SSOT min/desired is 0/0.
- Problem Studio image transcription defaults to the `global.amazon.nova-2-lite-v1:0` Bedrock inference profile when no OpenAI key is configured. The shared EC2 instance role receives only `bedrock:InvokeModel` on that profile and its exact foundation model through `policy_workers_bedrock_problem_transcription.json`; `Ensure-EC2InstanceProfileSSM` converges the inline policy. Unit count, tenant quota, output tokens, and request time remain bounded in the application layer.
- **Messaging** runs with ASG min/desired=1 warm baseline and AWS/SQS CloudWatch alarms for StepScaling up to SSOT max capacity. Account recovery and Alimtalk delivery are user-facing wait paths, so the worker is not allowed to cold-start from zero during normal operation. Scale-in requires visible+in-flight+delayed backlog to stay 0 and then returns only to the warm baseline.
- **Tools** runs with ASG min/desired=0 baseline and AWS/SQS CloudWatch alarms for deterministic conversion queues. Any visible queue message wakes the worker; scale-in uses the same visible+in-flight+delayed backlog guard.
- **Video** is not an ASG worker. It is AWS Batch only.

### Worker UserData Flow

The worker launch templates contain UserData that executes on each new instance boot:

```bash
#!/bin/bash
# 1. Wait for network/IMDS
# 2. Install Docker (dnf/yum)
# 3. ECR login + pull digest-pinned image (5 retries, 15s apart)
# 4. Fetch SSM /academy/workers/env (base64 JSON -> KEY=VALUE env file)
# 5. docker run -d --restart unless-stopped --name <worker> -e DJANGO_SETTINGS_MODULE=... --env-file /opt/workers.env <image>
```

This UserData is already correctly implemented in `scripts/v1/resources/worker_userdata.ps1` and is embedded in the launch templates by `asg_ai.ps1` and `asg_messaging.ps1`.

## 7. Migration Strategy

### Execution

- Migrations run in GitHub Actions before API instance refresh.
- Immediately before migration, the workflow atomically refreshes `/opt/api.env` from SSM `/academy/api/env`; it does not reuse a stale instance env file.
- The workflow resolves the newly built SHA tag to a digest, pulls that exact image, and runs `python manage.py migrate --no-input` in a one-shot Docker container using that refreshed production env.
- Only runs when API or shared code changed
- Must succeed before API ASG refresh starts

### Backward Compatibility Requirement

Since migrations can succeed before every old API instance has drained:
- **Allowed:** Add nullable/default columns, add tables, add indexes
- **Not allowed in single release:** Drop columns, rename columns, remove tables, change column types
- For breaking schema changes, use a two-release process:
  1. Release N: Add new column (both old and new code work)
  2. Release N+1: Drop old column (old code no longer in production)

### Failure Handling

If migration fails:
- The SSM command returns non-zero exit code
- `run-migrations` job fails
- `deploy-api` is skipped (depends on migration success)
- API and all worker deploy jobs are blocked; no Launch Template, Batch job definition, or instance refresh mutation proceeds
- Fix the migration and push again

## 8. Rollback Strategy

### Image-Based Rollback

Every build produces immutable SHA-tagged images. To rollback:

1. **Identify the last good SHA tag:**
   ```bash
   aws ecr describe-images --repository-name academy-api \
     --query 'sort_by(imageDetails,&imagePushedAt)[*].{tags:imageTags,pushed:imagePushedAt}' \
     --output table
   ```

2. **Choose the recovery path:**
   ```powershell
   # Stateful services fail closed; rebuild desired source as a new release.
   pwsh scripts/v1/rollback-api.ps1 -Sha sha-XXXXXXXX
   pwsh scripts/v1/rollback-messaging.ps1 -Sha sha-XXXXXXXX

   # Runtime-isolated services support digest rollback.
   pwsh scripts/v1/rollback-ai.ps1 -Sha sha-XXXXXXXX
   pwsh scripts/v1/rollback-tools.ps1 -Sha sha-XXXXXXXX
   ```

API and Messaging persist state-machine values that an older image may not understand. A point-in-time DB/queue preflight cannot prevent live writers from creating such a value while old and new instances overlap. Until releases publish a machine-verifiable compatibility epoch and deployment can quiesce every writer, their wrappers stop before AWS mutation with `STATEFUL_IMAGE_ROLLBACK_BLOCKED`; recovery is a new immutable roll-forward build from the desired reverted/cherry-picked source.

For supported runtime-isolated services, the rollback scripts resolve the SHA tag to its digest, capture the prior Launch Template/default/actual runtime state, create and verify the `$Latest` Launch Template version, and only then start the ASG instance refresh. A pin, refresh, or digest-verification failure creates a compensating version from the captured prior version and verifies the restored runtime. Re-tagging `:latest` is compatibility-only and does not change a digest-pinned runtime.

With `-Sha` omitted, ASG rollback derives the current digest from the Launch Template rather than the mutable alias, then selects the newest image pushed before that runtime. It waits for terminal `Successful`, treats `RollbackSuccessful` as deployment failure, and reads every healthy InService container `RepoDigests`; desired-zero groups are proven against the candidate Launch Template digest. Tools uses `rollback-tools.ps1`; Batch video uses `rollback-video.ps1`, which updates and reads back all eight required job definitions, preserves durable job-definition options, and requires both compute environments to remain `VALID/ENABLED`.

### Successful release manifest

`docs/reports/ci-build.latest.md` is build evidence only. The build job also produces a six-image candidate from exact run-unique SHA digests plus unchanged digests from the preceding successful release. Only after ASG health, actual container digest, all Video Batch job definitions, and compute environment gates pass does CI promote `docs/reports/release-manifest.latest.json` with `complete=true` and `status=successful`. Manual `deploy.ps1` resolves images exclusively from that manifest, so a partially pushed failed build cannot be mixed into a later manual release.

All production mutation entrypoints share one atomic DynamoDB lock in the SSOT table `academy-v1-video-job-lock`: CI build/deploy, weekly ECR/Batch cleanup, manual deploy, and rollback. The fixed `__deployment_control__` item is acquired conditionally, renewed only by its current unexpired owner, and released only by that owner. ECR cleanup additionally protects every digest in the last complete/successful six-image manifest (including `academy-base`) and fails nonzero on incomplete Video job-definition inventory, partial deletions, or verification warnings.

On a fresh environment, the lock table itself is the sole allowed pre-lock bootstrap mutation. `deploy.ps1` and `converge-release-prerequisites.ps1` idempotently create/read it and validate the exact `videoId` string HASH schema, PAY_PER_REQUEST billing, ACTIVE state, and TTL before normal lock acquisition. Default/strict manual deploy also exits nonzero when post-deploy ASG, ALB, Batch CE, or queue verification fails; only an explicit `-RelaxedValidation` diagnostic run may finish with verification warnings.

On the first immutable-release cutover, manual deploy intentionally fails until that manifest exists. With all four existing runtime Launch Templates present, run `pwsh scripts/v1/converge-release-prerequisites.ps1 -AwsProfile default`; it converges and reads back only GitHub Actions IAM and ECR mutability, without changing LT, ASG, or Batch runtime state. The role can create versions only on those four templates; its `RunInstances` dry-run resources are derived from their actual AMI, security groups, ASG subnets, and instance profile, while PassRole stays restricted to the exact EC2/Batch roles. Then run one full `workflow_dispatch`; its verified six-image rollout bootstraps the first complete successful manifest. Selective builds are allowed only after that bootstrap.

All six ECR repositories use `IMMUTABLE_WITH_EXCLUSION` with one `WILDCARD=latest` exclusion. CI and bootstrap both configure and read back that exact policy. Weekly cleanup inventories every ASG-level and running-instance Launch Template version, every desired InService container's actual `RepoDigests` through SSM, and every ACTIVE Batch job definition before deletion. It protects referenced parent and child manifests even when they fall outside the newest-ten retention window, and aborts all deletion if any required runtime cannot be inventoried exactly.

Structural drift checks compare the API ASG's effective `$Latest` Launch Template version with the successful release manifest. The legacy `$Default` version is intentionally retained as historical state during the immutable cutover and is not runtime drift when the ASG is correctly pinned to `$Latest`.

### Migration Rollback

Migration reversal is prohibited as a generic incident action. Syntactic Django
reversibility does not prove that a previous binary understands backfilled data or
new state-machine values. Use a corrective migration and immutable roll-forward.
Reverse migration is allowed only when a migration-specific runbook proves the
reverse contract, all writers are quiesced, an RDS snapshot exists, and restore
verification has been rehearsed; there is intentionally no generic command here.

## 9. ECR Lifecycle Policy

ECR repositories should have lifecycle policies to prevent unbounded image accumulation:

```json
{
  "rules": [
    {
      "rulePriority": 1,
      "description": "Remove untagged images after 1 day",
      "selection": {
        "tagStatus": "untagged",
        "countType": "sinceImagePushed",
        "countUnit": "days",
        "countNumber": 1
      },
      "action": { "type": "expire" }
    },
    {
      "rulePriority": 2,
      "description": "Keep last 10 sha-tagged images",
      "selection": {
        "tagStatus": "tagged",
        "tagPrefixList": ["sha-"],
        "countType": "imageCountMoreThan",
        "countNumber": 10
      },
      "action": { "type": "expire" }
    },
    {
      "rulePriority": 3,
      "description": "Keep last 5 release/deploy tags",
      "selection": {
        "tagStatus": "tagged",
        "tagPrefixList": ["latest", "v", "prod", "main", "deploy"],
        "countType": "imageCountMoreThan",
        "countNumber": 5
      },
      "action": { "type": "expire" }
    }
  ]
}
```

This keeps 10 rollback points and aggressively cleans untagged manifests. See `INFRASTRUCTURE-OPTIMIZATION.md` Section 4 for full ECR operational safety design including manifest-aware cleanup strategy.

## 10. Health Check Design

| Endpoint | Purpose | Checks | Used By |
|----------|---------|--------|---------|
| `/healthz` | Liveness probe | App is running, can respond | ALB health check, Docker HEALTHCHECK, deploy verification |
| `/health` | Readiness probe | App + database connection | Deploy verification, smoke tests |
| `/readyz` | Readiness check (same as /health) | App + database connection | Registered at `urls.py` lines 16-18 |

- ALB and Docker container health use `/healthz` for liveness decisions (lightweight, no DB)
- Deploy verification checks BOTH endpoints
- `/health` failure with `/healthz` success indicates DB connectivity issue (not an app crash)

## 11. Workflow File Location

`backend/.github/workflows/v1-build-and-push-latest.yml`

## 12. Related Files

| File | Purpose |
|------|---------|
| `.github/workflows/v1-build-and-push-latest.yml` | CI build, migration, API/messaging/AI/tools/video deploy, verification |
| `scripts/v1/resources/worker_userdata.ps1` | Worker UserData generation (Docker + ECR + SSM) |
| `scripts/v1/resources/asg_ai.ps1` | AI ASG + launch template management |
| `scripts/v1/resources/asg_messaging.ps1` | Messaging ASG + launch template management |
| `scripts/v1/resources/asg_tools.ps1` | Tools ASG + launch template management |
| `scripts/v1/resources/batch.ps1` | Video Batch CE/queue/job definition management |
| `scripts/v1/resources/api.ps1` | API ASG + launch template management |
| `scripts/v1/deploy.ps1` | Manual/bootstrap deployment (not used in CI/CD) |
