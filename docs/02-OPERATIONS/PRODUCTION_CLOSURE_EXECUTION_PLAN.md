# Production Closure Execution Plan (Fact-Based)

**Region:** ap-northeast-2  
**Account:** 809466760795  
**Constraints:** 1 video = 1 AWS Batch job = 1 EC2 instance; minvCpus=0, desiredvCpus=0; tenant isolation via prefix.

---

## 1. What is implemented already vs added/changed now

| Item | Status | Evidence (repo path or AWS fact) |
|------|--------|-----------------------------------|
| Batch CE default name | **CHANGED** | `batch_video_setup.ps1` default `ComputeEnvName` → `academy-video-batch-ce-v3` (was `academy-video-batch-ce`) |
| CE JSON computeEnvironmentName | **CHANGED** | `batch/video_compute_env.json` → `PLACEHOLDER_COMPUTE_ENV_NAME`; script replaces with param |
| Queue JSON | **ALREADY** | `batch/video_job_queue.json` uses `PLACEHOLDER_COMPUTE_ENV_NAME`; script replaces (no v3 mismatch) |
| Ops job definitions (reconcile, scanstuck) | **ADDED** | New files: `batch/video_ops_job_definition_reconcile.json`, `batch/video_ops_job_definition_scanstuck.json`; `batch_video_setup.ps1` [5b] registers both from these files |
| Batch setup registering ops jobdefs | **CHANGED** | [5b] now uses two JSON files instead of single template with placeholders |
| EventBridge rule + target JSON | **ALREADY** | `eventbridge/reconcile_to_batch_target.json`, `eventbridge/scan_stuck_to_batch_target.json`; JobDefinition names academy-video-ops-reconcile, academy-video-ops-scanstuck |
| EventBridge deploy script | **ALREADY** | `eventbridge_deploy_video_scheduler.ps1` deploys rules + targets + IAM role |
| EventBridge validation (PowerShell) | **ADDED** | `validate_video_eventbridge.ps1` — rules exist, ENABLED, targets Batch, jobQueue/jobDefinition match |
| EventBridge validation (Python) | **CHANGED** | `validate_video_system.py` — target JobDefinition name check (reconcile/scanstuck); `validate_video_production_readiness.py` — ops job defs ACTIVE |
| CloudWatch ops log group | **ALREADY** | `batch_video_setup.ps1` [1] creates `/aws/batch/academy-video-ops` if missing |
| CloudWatch alarms deploy | **ALREADY** | `cloudwatch_deploy_video_alarms.ps1` deploys from JSON |
| CloudWatch alarms verification | **ADDED** | `validate_video_alarms.ps1` — prints EXISTS/MISSING per alarm, exit non-zero if any missing |
| SSM dump/verify (cp949 safe) | **ADDED** | `ssm_dump_video_worker_env.ps1` — get parameter, write Value to UTF-8 file, validate JSON, print key list, required keys = runbook/ssm_bootstrap |
| Python CE default | **CHANGED** | `validate_video_production_readiness.py`, `validate_video_network_requirements.py` CE_NAME default → `academy-video-batch-ce-v3` |
| Full verification script | **ADDED** | `validate_video_production_full.ps1` — preflight, Batch CE/queue/jobdefs, EventBridge, alarms, SSM dump |

---

## 2. File changes summary (repo paths)

- **scripts/infra/batch_video_setup.ps1**  
  - Default `ComputeEnvName` = `academy-video-batch-ce-v3`.  
  - CE JSON: replace `PLACEHOLDER_COMPUTE_ENV_NAME` with `$ComputeEnvName`.  
  - [5b] Register ops job defs from `video_ops_job_definition_reconcile.json` and `video_ops_job_definition_scanstuck.json` (same image/roles/region, no name/command placeholder).

- **scripts/infra/batch/video_compute_env.json**  
  - `computeEnvironmentName` set to `PLACEHOLDER_COMPUTE_ENV_NAME` (replaced at deploy).

- **scripts/infra/batch/video_ops_job_definition_reconcile.json** (NEW)  
  - jobDefinitionName `academy-video-ops-reconcile`, command `python manage.py reconcile_batch_video_jobs`, log group `/aws/batch/academy-video-ops`, timeout 900s, placeholders: PLACEHOLDER_ECR_URI, PLACEHOLDER_JOB_ROLE_ARN, PLACEHOLDER_EXECUTION_ROLE_ARN, PLACEHOLDER_REGION.

- **scripts/infra/batch/video_ops_job_definition_scanstuck.json** (NEW)  
  - jobDefinitionName `academy-video-ops-scanstuck`, command `python manage.py scan_stuck_video_jobs`, same log group/timeout/placeholders.

- **apps/support/video/management/commands/validate_video_production_readiness.py**  
  - CE_NAME default `academy-video-batch-ce-v3`.  
  - Check ops job definitions `academy-video-ops-reconcile`, `academy-video-ops-scanstuck` ACTIVE.

- **apps/support/video/management/commands/validate_video_system.py**  
  - EventBridge target check: BatchParameters.JobDefinition base name must match `academy-video-ops-reconcile` / `academy-video-ops-scanstuck`.

- **apps/support/video/management/commands/validate_video_network_requirements.py**  
  - COMPUTE_ENV_NAME default `academy-video-batch-ce-v3`.

- **scripts/infra/verify_batch_network_connectivity.ps1**  
  - DRIFT line: only report when CE name ≠ `academy-video-batch-ce-v3`.

- **scripts/infra/validate_video_eventbridge.ps1** (NEW)  
  - Checks rules exist, ENABLED, targets Batch SubmitJob, jobQueue ARN and JobDefinition name match.

- **scripts/infra/validate_video_alarms.ps1** (NEW)  
  - describe-alarms for five alarm names; print EXISTS/MISSING; exit 1 if any missing.

- **scripts/infra/ssm_dump_video_worker_env.ps1** (NEW)  
  - get-parameter (no Value to console), write to UTF-8 file, JSON parse, print keys, required-keys validation, exit non-zero if invalid/missing.

- **scripts/infra/validate_video_production_full.ps1** (NEW)  
  - Runs preflight, Batch CE/queue/jobdefs, EventBridge, alarms, SSM dump (optional).

---

## 3. Execution order (copy/paste runnable from repo root)

All commands from **PowerShell**, repo root `C:\academy`. Use actual values for VpcId, SubnetIds, SecurityGroupId from your AWS (e.g. from `verify_batch_network_connectivity.ps1` output or existing deployment).

**Known AWS facts (ap-northeast-2, 809466760795):**  
VPC `vpc-0b89e02241aae4b0e`, Subnets `subnet-01c026861ea3cdecb`, `subnet-0e887178ed8cd65fa`, `subnet-0f576f190bcfbdfff`, `subnet-013323294fee4889e`, Batch SG `sg-061700a84decdc148`.

```powershell
# 0. Preflight
aws sts get-caller-identity --region ap-northeast-2
# Expected: Account 809466760795

# 1. (Optional) ECR bootstrap — create repo if missing, output ECR URI
$ecrUri = .\scripts\infra\ecr_bootstrap.ps1 -Region ap-northeast-2
# Expected: ECR_URI=809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest

# 2. Push image (if building locally; else use CI push or existing :latest)
# docker build -f docker/video-worker/Dockerfile -t 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest .
# aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com
# docker push 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest

# 3. Batch setup (CE/queue/jobdefs including ops) — default CE name academy-video-batch-ce-v3
$vpcId   = "vpc-0b89e02241aae4b0e"
$subnets = @("subnet-01c026861ea3cdecb","subnet-0e887178ed8cd65fa","subnet-0f576f190bcfbdfff","subnet-013323294fee4889e")
$sgId    = "sg-061700a84decdc148"
$ecrUri  = "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest"
.\scripts\infra\batch_video_setup.ps1 -Region ap-northeast-2 -VpcId $vpcId -SubnetIds $subnets -SecurityGroupId $sgId -EcrRepoUri $ecrUri
# Expected: CE VALID, queue ENABLED, worker + academy-video-ops-reconcile + academy-video-ops-scanstuck registered

# 4. SSM bootstrap — upload /academy/workers/env from .env
.\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region ap-northeast-2 -EnvFile .env -Overwrite
# Expected: OK: /academy/workers/env written successfully; ParameterVersion: <N>

# 5. EventBridge deploy (rules + targets)
.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue
# Expected: Done. EventBridge video scheduler (Batch only) deployed.

# 6. CloudWatch alarms deploy
.\scripts\infra\cloudwatch_deploy_video_alarms.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue
# Expected: Done. Video Batch CloudWatch alarms deployed.

# 7. Final verification (all resources)
.\scripts\infra\validate_video_production_full.ps1 -Region ap-northeast-2
# Expected: PRODUCTION VERIFICATION: PASS
# If SSM decrypt not desired: -SkipSsmDump

# 7b. Python production readiness (optional, same checks + DB/Redis)
python manage.py validate_video_production_readiness
# Expected: PRODUCTION READY: YES

# 7c. EventBridge only (PowerShell)
.\scripts\infra\validate_video_eventbridge.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue

# 7d. Alarms only
.\scripts\infra\validate_video_alarms.ps1 -Region ap-northeast-2

# 7e. SSM dump/verify (no console Value print, UTF-8 file + key list)
.\scripts\infra\ssm_dump_video_worker_env.ps1 -Region ap-northeast-2 -OutFile .env.ssm.verify
# Expected: OK: JSON valid, all required keys present.
```

---

## 4. Smoke test (CLI checks only; no console manual steps)

**4.1 Ops reconcile job — submit once, expect SUCCEEDED**

```powershell
$job = aws batch submit-job --job-name "smoke-reconcile-$(Get-Date -Format 'yyyyMMddHHmmss')" --job-queue academy-video-batch-queue --job-definition academy-video-ops-reconcile --region ap-northeast-2 --output json | ConvertFrom-Json
$jobId = $job.jobId
# Poll until terminal state
aws batch describe-jobs --jobs $jobId --region ap-northeast-2 --query "jobs[0].status" --output text
# Expected final: SUCCEEDED (or FAILED only if e.g. DB/Redis unreachable; then fix env and retry)
```

**4.2 Encode job — submit one encode job, verify READY**

- Use API or Django shell to create a video and submit a Batch job (e.g. `submit_batch_job(job_id)`), then:
  - `aws batch describe-jobs --jobs <aws_job_id> --region ap-northeast-2 --query "jobs[0].status" --output text` → expect SUCCEEDED.
  - In DB, video status becomes READY and has `hls_path` set (check via Django shell or API).

CLI checks: `aws batch list-jobs --job-queue academy-video-batch-queue --job-status SUCCEEDED --region ap-northeast-2` shows the job; `aws batch describe-jobs --jobs <id> --region ap-northeast-2` shows status SUCCEEDED and container exitCode 0.

---

## 5. Expected success outputs (CLI queries)

| Step | CLI query | Expected |
|------|-----------|----------|
| Preflight | `aws sts get-caller-identity --region ap-northeast-2` | Account 809466760795 |
| CE | `aws batch describe-compute-environments --compute-environments academy-video-batch-ce-v3 --region ap-northeast-2 --query "computeEnvironments[0].{status:status,state:state}"` | status=VALID, state=ENABLED |
| Queue | `aws batch describe-job-queues --job-queues academy-video-batch-queue --region ap-northeast-2 --query "jobQueues[0].state"` | ENABLED |
| Worker jobdef | `aws batch describe-job-definitions --job-definition-name academy-video-batch-jobdef --status ACTIVE --region ap-northeast-2 --query "jobDefinitions[0].jobDefinitionName"` | academy-video-batch-jobdef |
| Ops reconcile | `aws batch describe-job-definitions --job-definition-name academy-video-ops-reconcile --status ACTIVE --region ap-northeast-2 --query "jobDefinitions[0].jobDefinitionName"` | academy-video-ops-reconcile |
| Ops scanstuck | `aws batch describe-job-definitions --job-definition-name academy-video-ops-scanstuck --status ACTIVE --region ap-northeast-2 --query "jobDefinitions[0].jobDefinitionName"` | academy-video-ops-scanstuck |
| EventBridge reconcile | `aws events describe-rule --name academy-reconcile-video-jobs --region ap-northeast-2 --query "State"` | ENABLED |
| EventBridge scanstuck | `aws events describe-rule --name academy-video-scan-stuck-rate --region ap-northeast-2 --query "State"` | ENABLED |
| Alarms | `aws cloudwatch describe-alarms --alarm-names academy-video-DeadJobs academy-video-UploadFailures academy-video-FailedJobs academy-video-BatchJobFailures academy-video-QueueRunnable --region ap-northeast-2 --query "MetricAlarms[*].AlarmName"` | 5 alarm names |
| SSM | `aws ssm get-parameter --name /academy/workers/env --region ap-northeast-2 --query "Parameter.Version"` | Version number |

---

## 6. Manual console work (minimized)

- **None** for the execution plan above if you use the provided scripts and the same account/region/VPC.
- If ECR repo or VPC/subnets/SG do not exist: create ECR repo (ecr_bootstrap), or create VPC/subnets/SG (e.g. `network_minimal_bootstrap.ps1`) and pass outputs into step 3.
- Optional: SNS topic for alarms — set `-SnsTopicArn` in step 6 and ensure the role can publish to SNS.
