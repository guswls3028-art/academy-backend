# Video Batch Production Runbook

## Source of truth (no silent fallback)

- **Config:** `.env` at repo root is canonical. **SSM** `/academy/workers/env` is **derived only** from `.env` via `ssm_bootstrap_video_worker.ps1`. SSM value is **single-line JSON** (see `docs/deploy/SSM_JSON_SCHEMA.md`). Do not edit SSM in the console.
- **Runtime:** All Batch jobs (worker, netprobe, reconcile, scan_stuck) boot via **batch_entrypoint**: it reads SSM JSON, sets env, validates required keys, then runs the job command. `DJANGO_SETTINGS_MODULE` must be `apps.api.config.settings.worker`; dev/prod defaults are not used.

## Option A: Batch in SAME VPC as API/RDS

This runbook assumes Batch compute environment, queue, and job definitions are in the **same VPC** as the API and RDS. Use `recreate_batch_in_api_vpc.ps1` to create or recreate Batch in the API VPC.

**Windows cp949:** For scripts that touch SSM or JSON with non-ASCII values, set UTF-8 before running:
```powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
# For Python commands that print to console:
$env:PYTHONIOENCODING = "utf-8"
```

---

## 0. Environment Source of Truth

**.env is the source of truth; SSM is derived. No silent fallback.**

Canonical config: `.env` at repository root (see `.env.example`). All required variables for API server, Batch worker, and Video ops jobs are defined there.

**SSM:** Parameter `/academy/workers/env` is **derived only** from `.env` via `ssm_bootstrap_video_worker.ps1`. The value is stored as **single-line JSON** (SecureString). Schema: `docs/deploy/SSM_JSON_SCHEMA.md`. Batch entrypoint parses this JSON only; no KEY=VALUE fallback.

- **No manual SSM creation or editing.** Use the bootstrap script only.
- **Fail hard on missing config.** Scripts exit non-zero if required variables are missing.

---

## 1. Required environment variables

### API (Django)

| Variable | Required | Description |
|----------|----------|-------------|
| `AWS_DEFAULT_REGION` / `AWS_REGION` | Yes | e.g. `ap-northeast-2` |
| `VIDEO_BATCH_JOB_QUEUE` | No | Default: `academy-video-batch-queue` |
| `VIDEO_BATCH_JOB_DEFINITION` | No | Default: `academy-video-batch-jobdef` |
| `VIDEO_TENANT_MAX_CONCURRENT` | No | Default: `2` |
| `VIDEO_GLOBAL_MAX_CONCURRENT` | No | Default: `20` |
| `VIDEO_MAX_JOBS_PER_VIDEO` | No | Default: `10` |
| `VIDEO_CLOUDWATCH_NAMESPACE` | No | Default: `Academy/Video` |
| `LAMBDA_INTERNAL_API_KEY` | Yes (if internal API used) | Shared secret for `/api/v1/internal/*` |
| `DB_*`, `R2_*`, `SECRET_KEY`, etc. | Yes | Per existing API config |

### Batch worker (container)

Set in job definition container overrides or as job environment:

- `VIDEO_JOB_ID` (required at runtime)
- `VIDEO_PROGRESS_TTL_SECONDS` (optional, default 14400)
- `VIDEO_JOB_MAX_ATTEMPTS` (optional, default 5)
- `VIDEO_JOB_HEARTBEAT_SECONDS` (optional, default 60)
- DB/R2/Redis and `VIDEO_BATCH_JOB_QUEUE` as needed for API calls from worker

### Ops jobs (Batch: reconcile, scan_stuck, netprobe)

Scheduled via EventBridge → Batch SubmitJob to **academy-video-ops-queue** (Ops CE: t4g.micro/small, max 4 vCPU). Job definitions: `academy-video-ops-reconcile`, `academy-video-ops-scanstuck`, `academy-video-ops-netprobe`. Same image as video worker. **All run via batch_entrypoint:** container ENTRYPOINT is the entrypoint script; it loads SSM JSON into env, then runs the job command (`python manage.py reconcile_batch_video_jobs`, `python manage.py scan_stuck_video_jobs`, `python manage.py netprobe`). Do not invoke `python manage.py` directly from job definition without going through the entrypoint.

---

## 2. AWS resources and deploy scripts

| Resource | Script | Notes |
|----------|--------|--------|
| IAM roles (Batch service, ECS instance, ECS execution, job role) | `scripts/infra/batch_video_setup.ps1` | Creates/updates roles; attaches inline policies from `scripts/infra/iam/*.json` |
| Video CE, video queue, job definitions | `scripts/infra/batch_video_setup.ps1` | Pass `-Region`, `-VpcId`, `-SubnetIds`, `-SecurityGroupId`, `-EcrRepoUri` (and optional overrides) |
| **Ops CE + Ops queue** | `scripts/infra/batch_ops_setup.ps1` | **academy-video-ops-ce** (t4g.micro/small, max 4 vCPU), **academy-video-ops-queue**. Same VPC and **same Security Group as academy-video-batch-ce**; run after video Batch. |
| CloudWatch Log Group | `scripts/infra/batch_video_setup.ps1` | `/aws/batch/academy-video-worker` |
| EventBridge rule (reconcile, rate 5 min) | `scripts/infra/eventbridge_deploy_video_scheduler.ps1` | Target: **academy-video-ops-queue**. Script runs **aws events put-targets** → **actual AWS EventBridge targets** updated to Ops queue. |
| EventBridge rule (scan-stuck) | `scripts/infra/eventbridge_deploy_video_scheduler.ps1` | Target: **academy-video-ops-queue** (actual AWS target updated by same script). |
| CloudWatch alarms | `scripts/infra/cloudwatch_deploy_video_alarms.ps1` | Pass `-Region`, `-JobQueueName` (video queue); optional `-SnsTopicArn` |

### One-shot execution (copy-paste PowerShell)

Run from repository root. Account ID is auto-detected; EcrRepoUri placeholder `<acct>` is replaced automatically in `recreate_batch_in_api_vpc.ps1`. Ensure `.env` exists and is filled.

```powershell
# UTF-8
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

# Account auto-detect
$acctId = (aws sts get-caller-identity --query Account --output text)

# 1) .env -> SSM (JSON, SecureString)
.\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region ap-northeast-2 -EnvFile .env -Overwrite

# 2) Docker build & push (Video Worker only)
.\scripts\build_and_push_ecr_remote.ps1 -VideoWorkerOnly

# 3–4) Batch CE/Queue 정합 확인 및 Job Definitions 재등록 (video only)
$ecrUri = "${acctId}.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest"
.\scripts\infra\recreate_batch_in_api_vpc.ps1 -Region ap-northeast-2 -EcrRepoUri $ecrUri

# 4b) Ops CE + Ops queue (reconcile/scan_stuck/netprobe -> t4g, no c6g)
.\scripts\infra\batch_ops_setup.ps1 -Region ap-northeast-2

# 5) EventBridge wiring (reconcile/scan_stuck -> academy-video-ops-queue)
.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region ap-northeast-2 -OpsJobQueueName academy-video-ops-queue

# 6) CloudWatch alarms
.\scripts\infra\cloudwatch_deploy_video_alarms.ps1 -Region ap-northeast-2 -JobQueueName $q

# 7) Netprobe SUCCESS
.\scripts\infra\run_netprobe_job.ps1 -Region ap-northeast-2 -JobQueueName academy-video-ops-queue

# 8) production_done_check PASS
.\scripts\infra\production_done_check.ps1 -Region ap-northeast-2
```

Success: final lines show `PRODUCTION DONE CHECK: PASS` and `VIDEO WORKER PRODUCTION READY`.

### Deploy order (exact sequence — Option A, copy/paste runnable)

Run in a **fresh PowerShell session** from the repository root. Ensure `.env` exists (copy from `.env.example` and fill required keys including `AWS_DEFAULT_REGION=ap-northeast-2`).

**Step 1 — SSM bootstrap**
```powershell
.\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region ap-northeast-2 -EnvFile .env -Overwrite
```
Expected: Script exits 0; no "Required variables missing"; `OK: /academy/workers/env written successfully` or similar.

**Step 2 — Recreate Batch in API VPC**

Set `$acctId` to your AWS account ID (e.g. `809466760795`). Do not pass a string containing literal `<acct>` — it produces invalid image URI in job definitions.

```powershell
$acctId = "809466760795"
$ecrUri = "${acctId}.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest"
.\scripts\infra\recreate_batch_in_api_vpc.ps1 -Region ap-northeast-2 -EcrRepoUri $ecrUri -CleanupOld:$false
```
Expected: Exit 0; `DONE. Batch recreated in API VPC. JobQueueName=<final>`.

**Important:** The **final job queue name** is either `academy-video-batch-queue` (if the existing queue was updated to CE `academy-video-batch-ce`) or `academy-video-batch-queue-ce` (if update failed and a new queue was created). Use the name printed at the end of Step 2 for Steps 3–5, or read `docs/deploy/actual_state/batch_final_state.json` → `FinalJobQueueName`.

**Step 2b — Ops CE + Ops queue** (reconcile/scan_stuck/netprobe on t4g; no c6g scaling for ops)
```powershell
.\scripts\infra\batch_ops_setup.ps1 -Region ap-northeast-2
```
Uses the **same Security Group as academy-video-batch-ce** (discovered from the video CE).  
Expected: Exit 0; `DONE. Ops CE and queue ready.` Creates `academy-video-ops-ce` and `academy-video-ops-queue`; state in `docs/deploy/actual_state/batch_ops_state.json`.

**Step 3 — EventBridge** (reconcile/scan_stuck submit to Ops queue)
```powershell
.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region ap-northeast-2 -OpsJobQueueName academy-video-ops-queue
```
This updates the **actual AWS EventBridge targets** (put-targets) for both rules to **academy-video-ops-queue**; not only the repo JSON templates.  
Or if you created Ops queue with a different name, pass that. Expected: Exit 0; `Done. EventBridge: actual AWS targets updated; reconcile/scan_stuck -> academy-video-ops-queue; targets verified.`

**Step 4 — CloudWatch alarms** (use same final queue name)
```powershell
.\scripts\infra\cloudwatch_deploy_video_alarms.ps1 -Region ap-northeast-2 -JobQueueName $q
```
Expected: Exit 0; `Done. Video Batch CloudWatch alarms deployed.`

**Step 5 — Netprobe** (use same final queue name)
```powershell
.\scripts\infra\run_netprobe_job.ps1 -Region ap-northeast-2 -JobQueueName academy-video-ops-queue
```
Expected: Exit 0; `SUCCEEDED` and job log lines.

**Step 6 — Production done check**
```powershell
.\scripts\infra\production_done_check.ps1 -Region ap-northeast-2
```
Uses `batch_final_state.json` to resolve the queue name automatically. Expected: Exit 0; `PRODUCTION DONE CHECK: PASS`.

### Final resource names and v2 queue

| Resource | Expected name(s) |
|----------|-------------------|
| Video compute environment | `academy-video-batch-ce` |
| Video job queue (primary) | `academy-video-batch-queue` |
| Video job queue (fallback) | `academy-video-batch-queue-ce` (created only when update of existing queue to CE fails) |
| **Ops compute environment** | **`academy-video-ops-ce`** (t4g.micro, t4g.small, max 4 vCPU). **Same Security Group as academy-video-batch-ce.** |
| **Ops job queue** | **`academy-video-ops-queue`** (reconcile, scan_stuck, netprobe) |
| Worker job definition | `academy-video-batch-jobdef` |
| Ops job definitions | `academy-video-ops-reconcile`, `academy-video-ops-scanstuck`, `academy-video-ops-netprobe` |

**How the scripts decide queue name:**  
`batch_video_setup.ps1` first tries to point the existing queue `academy-video-batch-queue` to CE `academy-video-batch-ce` (by ARN). If that update fails (e.g. AWS rejects the change), it creates a **new** queue `academy-video-batch-queue-ce` linked to the CE and writes `FinalJobQueueName` and `FinalJobQueueArn` to `docs/deploy/actual_state/batch_final_state.json`.  
`recreate_batch_in_api_vpc.ps1` then calls EventBridge with this final queue name.  
`production_done_check.ps1` and `run_netprobe_job.ps1` should be called with the same queue name (or rely on `batch_final_state.json` for the done check).

---

**a) Fill .env from .env.example**  
Copy `.env.example` to `.env` and set all required keys (see section 0). No silent fallback.

**b) SSM bootstrap**  
```powershell
.\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region ap-northeast-2 -EnvFile .env -Overwrite
```
Expected: `OK: /academy/workers/env written successfully` and `ParameterVersion: <N>`.

**c) Recreate Batch in API VPC (Option A)**  
```powershell
$ecrUri = "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest"
.\scripts\infra\recreate_batch_in_api_vpc.ps1 -Region ap-northeast-2 -EcrRepoUri $ecrUri
```
If replacing existing Batch in another VPC: add `-CleanupOld` (only when no RUNNING/RUNNABLE jobs).  
Expected: `DONE. Batch recreated in API VPC.`

**d) EventBridge** (updates actual AWS targets to Ops queue)
```powershell
.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region ap-northeast-2 -OpsJobQueueName academy-video-ops-queue
```

**e) CloudWatch alarms**  
```powershell
.\scripts\infra\cloudwatch_deploy_video_alarms.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue
```
Optional: `-SnsTopicArn "arn:aws:sns:ap-northeast-2:809466760795:topic"`

**f) Netprobe and production done check**  
```powershell
.\scripts\infra\run_netprobe_job.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue
.\scripts\infra\production_done_check.ps1 -Region ap-northeast-2
```
Expected: `PRODUCTION DONE CHECK: PASS`.

---

### Deploy order (alternative: manual)

1. **ECR:** `.\scripts\infra\ecr_bootstrap.ps1 -Region ap-northeast-2` — use output ECR URI.
2. **Optional network:** `.\scripts\infra\network_minimal_bootstrap.ps1 -Region ap-northeast-2` or provide VpcId, SubnetIds, SecurityGroupId.
3. **SSM:** `.\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region ap-northeast-2 -EnvFile .env -Overwrite`
4. **Batch:** `.\scripts\infra\batch_video_setup.ps1 -Region ap-northeast-2 -VpcId vpc-xxx -SubnetIds @("subnet-a","subnet-b") -SecurityGroupId sg-xxx -EcrRepoUri <uri>`
5. **EventBridge:** `.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region ap-northeast-2 -OpsJobQueueName academy-video-ops-queue` (updates actual AWS targets to Ops queue)
6. **CloudWatch alarms:** `.\scripts\infra\cloudwatch_deploy_video_alarms.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue` — optional `-SnsTopicArn`

---

## 3. Validation commands

- **SSM shape (no value print):**  
  `.\scripts\infra\verify_ssm_env_shape.ps1 -Region ap-northeast-2`  
  Expected: `OK: SSM parameter JSON valid, all required keys present.`

- **EventBridge wiring:**  
  `.\scripts\infra\verify_eventbridge_wiring.ps1 -Region ap-northeast-2 -OpsJobQueueName academy-video-ops-queue`

- **Repo infra names:**  
  `.\scripts\infra\validate_repo_infra_names.ps1`

- **Network connectivity (and netprobe if no live instance):**  
  `.\scripts\infra\verify_batch_network_connectivity.ps1 -Region ap-northeast-2`  
  Validates CE `academy-video-batch-ce` (API VPC) by default.

- **Discover Batch state (CE vs target VPC):**  
  `.\scripts\infra\discover_api_network.ps1 -Region ap-northeast-2` to get API VpcId, then:  
  `.\scripts\infra\discover_batch_state.ps1 -Region ap-northeast-2 -TargetVpcId vpc-0831a2484f9b114c2`  
  Use the **actual** VPC ID (e.g. from the first command's output). Do not use a literal placeholder like `<api_vpc_id>` — PowerShell treats `<` as redirection.

- **Production done (all checks + netprobe):**  
  `.\scripts\infra\production_done_check.ps1 -Region ap-northeast-2`

Run from repo root (Django app) with AWS credentials and env configured:

- **System (Batch queue, job definition, DB/Redis):**  
  `python manage.py validate_video_system`

- **IAM expectations (static list of required actions per role):**  
  `python manage.py validate_video_iam_expectations`

- **Network (Batch CE subnets; private subnet NAT/VPC endpoints):**  
  `python manage.py validate_video_network_requirements`  
  If something cannot be determined via SDK, the command prints **DEPENDS ON MANUAL AWS CONSOLE CONFIG** and lists required endpoints/routes (ECR api/dkr, logs, S3).

- **Architecture (no Lambda):**  
  `python manage.py validate_video_architecture_mode`

- **Production readiness (all deps):**  
  `python manage.py validate_video_production_readiness`

- **Storage integrity (READY videos, optional):**  
  `python manage.py verify_video_storage_integrity [--min-segments 1]`

---

## 3b. 원테이크 운영 점검 (One-take full audit)

Video/Ops Queue·CE 분리 상태, EventBridge 스케줄·타깃, IAM(DescribeJobs), JobDefinition을 한 번에 점검한다.  
**ReadOnly** 실행 시 실제 변경 없이 결과만 출력하며, **-FixMode** 시 Ops CE/Queue 생성, IAM 정책 부착, EventBridge rule/target 정렬을 자동 수행한다.

**실행 예 (저장소 루트에서):**

```powershell
# UTF-8 (cp949/aws json 디코딩 문제 회피)
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

# ReadOnly 감사만
.\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2

# 상세 로그
.\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2 -Verbose

# 실제 수정 적용 (Ops CE/Queue 없으면 생성, IAM 부착, EventBridge rate(5분)·OpsQueue 타깃 정렬)
.\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2 -FixMode

# FixMode + RUNNING reconcile 1개 초과 시 나머지 terminate
.\scripts\infra\infra_one_take_full_audit.ps1 -Region ap-northeast-2 -FixMode -FixModeWithCleanup
```

**선택 파라미터:**

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `-ExpectedVideoQueueName` | `batch_final_state.json`의 FinalJobQueueName | Video 작업용 큐 이름 |
| `-ExpectedOpsQueueName` | `academy-video-ops-queue` | Ops(reconcile/scan_stuck)용 큐 |
| `-ExpectedVideoCEName` | `academy-video-batch-ce` | Video CE 이름 |
| `-ExpectedOpsCEName` | `academy-video-ops-ce` | Ops CE 이름 |
| `-ReconcileRuleName` | 자동 탐색(이름에 reconcile 포함) | EventBridge 규칙 이름 |
| `-ScanStuckRuleName` | 자동 탐색(이름에 scan-stuck 포함) | EventBridge 규칙 이름 |

**출력:**  
콘솔에 `Category | Check | Expected | Actual | Status(PASS/WARN/FAIL) | FixAction` 테이블이 출력되고, 마지막에 `Summary: PASS=n WARN=n FAIL=n` 및 `Result: PASS` / `NEEDS_ACTION` / `FAIL`이 표시된다.  
`-FixMode` 사용 시 적용된 변경 사항 목록이 함께 출력된다.

**운영 검증 체크:** Reconcile 안정화 배포 후 RUNNING reconcile 1개 이하, AccessDenied 없음, SUCCEEDED→READY 전이, Ops CE scale down 등은 [Reconcile 안정화 — 운영 검증 체크](video/RECONCILE_STABILIZATION_VERIFICATION_COMMANDS.md) 문서의 커맨드로 확인한다.

---

## 4. Rollback steps

- **Batch job definition:** Register a new revision in `scripts/infra/batch/video_job_definition.json` and point the app to the new revision via `VIDEO_BATCH_JOB_DEFINITION` (with revision) or rely on “ACTIVE” latest. To revert, register the previous revision and update env or job submission to use it.
- **Compute environment / job queue:** Do not delete if jobs are running. To stop new jobs, disable the job queue:  
  `aws batch update-job-queue --job-queue academy-video-batch-queue --state DISABLED --region <region>`  
  Re-enable: `--state ENABLED`.
- **EventBridge:** Disable the rule to stop reconcile triggers:  
  `aws events disable-rule --name academy-reconcile-video-jobs --region <region>`  
  Re-enable: `aws events enable-rule --name academy-reconcile-video-jobs --region <region>`.  
  Reconcile/scan_stuck submit to **academy-video-ops-queue** (Ops CE); video jobs stay on **academy-video-batch-queue**.
- **CloudWatch alarms:** Delete or adjust threshold via console/CLI; alarm names are in `scripts/infra/cloudwatch/*.json` and in `cloudwatch_deploy_video_alarms.ps1`.
- **IAM:** Detach or remove inline policies from roles via console/CLI; policy names are in `batch_video_setup.ps1` (e.g. `academy-batch-service-inline`, `academy-video-batch-job-inline`). Avoid removing roles while Batch CE/job definition reference them.
