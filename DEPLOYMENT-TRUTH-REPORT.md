# Deployment Truth Report (Executable Artifacts Only)

**Scope:** Backend repo `C:\academy\backend`. Only scripts, workflows, Dockerfiles, compose files, and runtime config paths. No docs as source of truth.
**최종 갱신:** 2026-03-11 (IAM 권한 적용, 프론트엔드 배포 정보 추가)

---

## 0. Frontend deploy path

| Fact | Supporting artifact |
|------|---------------------|
| **Frontend는 별도 git repo** (`frontend/`)이며 백엔드 배포와 완전 독립. | 프로젝트 구조: `C:\academy\backend`, `C:\academy\frontend` 각각 별도 `.git` |
| **배포 방식:** `git push origin main` → Cloudflare Pages 자동 빌드·배포. | Cloudflare Pages 설정 (콘솔) |
| **사용 금지:** `deploy-front.ps1`, `deploy.ps1 -DeployFront` — params.yaml SSOT 값이 의도적으로 비어 있음. | `docs/00-SSOT/v1/params.yaml` |

---

## 1. API deploy path

### Confirmed facts

| Fact | Supporting artifact |
|------|---------------------|
| **Two paths.** (1) **CI 자동:** main push → GitHub Actions build-and-push → `deploy-api-refresh` job → API ASG instance refresh. (2) **수동 정식:** `scripts/v1/deploy.ps1` → Ensure-API (Launch Template + ASG). LT UserData: SSM → `/opt/api.env`, ECR pull, `docker run` academy-api:8000. | `backend/.github/workflows/v1-build-and-push-latest.yml`, `backend/scripts/v1/resources/api.ps1` |
| **CI trigger (API only):** CI job `deploy-api-refresh` runs `aws autoscaling start-instance-refresh --auto-scaling-group-name academy-v1-api-asg --preferences '{"MinHealthyPercentage":100,"InstanceWarmup":300}'`. IAM 역할 `academy-gha-ecr-build`에 권한 적용 완료 (2026-03-11). | `backend/.github/workflows/v1-build-and-push-latest.yml` (deploy-api-refresh job) |
| **API container name:** `academy-api`. Port 8000. Image from ECR (account 809466760795, repo academy-api, tag latest in scripts). | `backend/scripts/v1/resources/api.ps1` (docker run --name academy-api -p 8000:8000), `backend/scripts/deploy_api_on_server.sh` (docker stop/rm academy-api, docker run --name academy-api) |
| **deploy.ps1 run context:** Must run from **backend repo root**. `$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path` with ScriptRoot = scripts/v1. | `backend/scripts/v1/deploy.ps1` (line 38), `backend/scripts/v1/core/ssot.ps1` (params path) |

### Assumptions

- **수동 정식 배포:** Running `deploy.ps1` locally (not in CI) is the only way to update Launch Template / UserData and then optionally trigger instance refresh. CI runs build-and-push + deploy-api-refresh (ASG instance refresh, IAM 권한 적용 완료 2026-03-11).

---

## 2. Worker deploy path

### Confirmed facts

| Fact | Supporting artifact |
|------|---------------------|
| **Workers are EC2 ASGs only (no Rapid).** Messaging: ASG academy-v1-messaging-worker-asg. AI: ASG academy-v1-ai-worker-asg. Video: AWS Batch (job def + queue), not EC2 ASG for long-running worker. | `backend/docs/00-SSOT/v1/params.yaml` (messagingWorker.asgName, aiWorker), `backend/scripts/v1/deploy.ps1` (Ensure-ASGMessaging, Ensure-ASGAi, Ensure-VideoCE/Queue/JobDef) |
| **Worker UserData:** Get-WorkerLaunchTemplateUserData: Docker install → ECR login → pull ImageUri → SSM param (SsmParam) → base64 decode → JSON to KEY=VALUE → `/opt/workers.env` → `docker run -d --restart unless-stopped --name $ContainerName -e DJANGO_SETTINGS_MODULE=apps.api.config.settings.worker --env-file /opt/workers.env $ImageUri`. | `backend/scripts/v1/resources/worker_userdata.ps1` |
| **Worker env SSM path:** `/academy/workers/env`. Value expected base64(JSON). | `backend/docs/00-SSOT/v1/params.yaml` (ssm.workersEnv), `backend/scripts/v1/resources/worker_userdata.ps1` (SsmParam) |
| **Worker images (ECR):** messaging: academy-messaging-worker:latest, AI CPU: academy-ai-worker-cpu:latest. Set in SSOT; UserData gets URI from Get-LatestWorkerImageUri (useLatestTag → latest). | `backend/docs/00-SSOT/v1/params.yaml` (ecr), `backend/scripts/v1/resources/worker_userdata.ps1` (Get-LatestWorkerImageUri) |
| **No worker “hot” deploy.** Worker code/config change = LT UserData change → deploy.ps1 Ensure-ASGMessaging/Ensure-ASGAi → LT version bump → instance refresh (if drift). No cron or remote script for workers. | `backend/scripts/v1/deploy.ps1` (Ensure-ASGAi, Ensure-ASGMessaging only; no api-auto-deploy equivalent) |

### Assumptions

- **Video “worker”:** Runs as Batch jobs (submit job, container runs, exits). Image academy-video-worker:latest. No long-lived EC2 worker for video; deploy path is Batch job def + CE/queue in deploy.ps1.

---

## 3. Image build / push path

### Confirmed facts

| Fact | Supporting artifact |
|------|---------------------|
| **Single build/push path: GitHub Actions.** Workflow `v1-build-and-push-latest.yml`. Trigger: push to main or workflow_dispatch. | `backend/.github/workflows/v1-build-and-push-latest.yml` |
| **Build order:** (1) academy-base (docker/Dockerfile.base), (2) academy-api (docker/api/Dockerfile, BASE_IMAGE=academy-base:latest), (3) academy-video-worker, (4) academy-messaging-worker, (5) academy-ai-worker-cpu. All context `.`, platforms linux/arm64, tag latest. | Same workflow file (Build and push steps) |
| **ECR registry:** 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com. Repos: academy-base, academy-api, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu. Ensure step creates repo if missing. | Same workflow (env ECR_REGISTRY, Ensure ECR repos loop) |
| **Auth:** OIDC only. `role-to-assume: ${{ secrets.AWS_ROLE_ARN_FOR_ECR_BUILD }}`. No access key in workflow. | Same workflow (Configure AWS credentials step) |
| **IAM 역할:** `academy-gha-ecr-build`. 인라인 정책 `EcrBuildPush`에 ECR 권한 + ASG instance refresh 권한 (`autoscaling:StartInstanceRefresh`, `DescribeInstanceRefreshes`, `DescribeAutoScalingGroups` on `academy-v1-api-asg`). 2026-03-11 적용. | IAM Console / `aws iam get-role-policy` |
| **Dockerfile paths (backend root context):** Base: `docker/Dockerfile.base`. API: `docker/api/Dockerfile`. Video worker: `docker/video-worker/Dockerfile`. Messaging: `docker/messaging-worker/Dockerfile`. AI CPU: `docker/ai-worker-cpu/Dockerfile`. | Workflow file/docker/* paths; repo root = backend |
| **deploy.ps1 does not build.** Default `-SkipBuild = $true`. Comment: “이미지 빌드·ECR 푸시는 GitHub Actions(OIDC)만”. | `backend/scripts/v1/deploy.ps1` (param SkipBuild = $true, comment) |
| **docker-compose.yml:** Local dev only. Build context `.`, dockerfile paths e.g. docker/api/Dockerfile, env from `.env`. Not used in production deploy. | `backend/docker-compose.yml` |

### Assumptions

- **CI is the only place that builds and pushes** production images. No local or EC2 build step in deploy scripts.

---

## 4. Env injection path

### Confirmed facts

| Component | Source | Runtime path | Supporting artifact |
|-----------|--------|--------------|----------------------|
| **API (Formal)** | SSM parameter `/academy/api/env` | JSON → KEY=VALUE file → `/opt/api.env` → `docker run --env-file /opt/api.env` | `backend/scripts/v1/resources/api.ps1` (Get-ApiLaunchTemplateUserData: SsmApiEnvParam, /opt/api.env) |
| **API (Rapid)** | Same SSM `/academy/api/env` | Same: JSON (or base64 JSON) → `/opt/api.env` → `docker run --env-file /opt/api.env` | `backend/scripts/deploy_api_on_server.sh` (SSM_API_ENV, API_ENV_FILE=/opt/api.env) |
| **Workers** | SSM parameter `/academy/workers/env` | base64(JSON) → decode → KEY=VALUE → `/opt/workers.env` → `docker run --env-file /opt/workers.env` | `backend/scripts/v1/resources/worker_userdata.ps1`; params ssm.workersEnv |
| **Params SSOT keys:** apiEnv: `/academy/api/env`, workersEnv: `/academy/workers/env`. | — | `backend/docs/00-SSOT/v1/params.yaml` (ssm section) |
| **Rapid deploy required keys (server script):** DB_HOST, DB_NAME, DB_USER, R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, REDIS_HOST. Script exits 1 if missing in `/opt/api.env`. | — | `backend/scripts/deploy_api_on_server.sh` (REQUIRED_KEYS) |

### Assumptions

- **API env value format:** Plain JSON or (for workers) base64(JSON). deploy_api_on_server.sh accepts raw JSON or base64-decoded JSON.
- **No .env on EC2 for API/workers.** Env comes only from SSM → /opt/*.env at boot (Formal) or at deploy run (Rapid).

---

## 5. Rollback path

### Confirmed facts

| Fact | Supporting artifact |
|------|---------------------|
| **No dedicated rollback script or job.** No file or workflow step named rollback/revert; no “previous version” or “last good” image tag in scripts. | Grep for rollback/revert in scripts; workflow and deploy.ps1 read |
| **API:** Rollback = (1) `git revert` + push → CI가 새 이미지 빌드·푸시 + instance refresh, 또는 (2) 이전 이미지를 academy-api:latest로 re-push 후 수동 instance refresh. | `backend/scripts/v1/resources/api.ps1`, params ecr.useLatestTag |
| **Workers:** Same as API: LT/UserData point to ECR latest. Rollback = ensure previous image is latest in ECR (or change params + deploy.ps1) and trigger instance refresh. | `backend/scripts/v1/resources/worker_userdata.ps1` (Get-LatestWorkerImageUri), deploy.ps1 Ensure-ASG* |

### Assumptions

- **Operational rollback** is manual: git revert + push (CI auto-deploy), or re-push old image as latest + refresh. No automated “last known good” tag or snapshot.

---

## 6. Risks

### Confirmed from code

| Risk | Evidence |
|------|----------|
| **No version pin.** ECR `academy-api:latest` 태그만 사용. SHA/커밋 기반 태그 없음. 롤백 시 이전 이미지 특정이 어려움. | workflow file, params ecr.useLatestTag |
| **Instance refresh 중 잠시 서비스 불안정.** MinHealthyPercentage=100이지만 새 인스턴스 기동 + health check 통과까지 지연 가능. | v1-build-and-push-latest.yml, api.ps1 |
| **SSM env 변경 시 자동 반영 안 됨.** SSM 변경만으로는 기존 인스턴스의 /opt/api.env가 갱신되지 않음. instance refresh 또는 deploy.ps1 필요. | api.ps1 UserData, deploy_api_on_server.sh |

---

## 7. Runtime configuration paths (summary)

| Purpose | Path | Set by |
|---------|------|--------|
| API env (prod) | `/opt/api.env` | UserData from SSM `/academy/api/env` |
| Workers env (prod) | `/opt/workers.env` | UserData from SSM `/academy/workers/env` (base64 JSON) |
| API userdata log | `/var/log/academy-api-userdata.log` | UserData in api.ps1 |
| Worker userdata log | `/var/log/academy-worker-userdata.log` | UserData in worker_userdata.ps1 |
| Local dev env | `.env` (backend root) | docker-compose.yml env_file; not used on EC2 |

---

## 8. Artifact index

| Artifact | Role |
|----------|------|
| `backend/.github/workflows/v1-build-and-push-latest.yml` | Build all 5 images (OIDC), push to ECR, then deploy-api-refresh (API ASG instance refresh) |
| `backend/scripts/v1/deploy.ps1` | 수동 정식 배포: SSOT load, Ensure API/workers/Batch/ALB/etc.; API LT UserData from api.ps1 |
| `backend/scripts/v1/resources/api.ps1` | API LT UserData (SSM→/opt/api.env, ECR pull, docker run), Ensure-API-ASG, instance refresh |
| `backend/scripts/v1/resources/worker_userdata.ps1` | Worker LT UserData (SSM→/opt/workers.env, ECR pull, docker run) |
| `backend/docs/00-SSOT/v1/params.yaml` | SSOT: ssm.apiEnv, ssm.workersEnv, ecr repos, API/worker ASG names, health path, etc. |
| `backend/docker/Dockerfile.base` | Base image (python:3.11-slim, deps; no app code) |
| `backend/docker/api/Dockerfile` | API image FROM base; COPY app; gunicorn + migrate on start |
| `backend/docker-compose.yml` | Local dev only; build + env_file .env; not used in prod deploy |

---

**Generated from executable artifacts only. Facts vs assumptions clearly separated above.**
