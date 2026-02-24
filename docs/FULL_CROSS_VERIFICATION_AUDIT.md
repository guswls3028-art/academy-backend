# Full Cross-Verification Audit: Repo vs Deployed AWS

**Audit date:** 2025-02-24  
**Region:** ap-northeast-2 (from repo/default)  
**AWS CLI credential status:** INVALID — `UnrecognizedClientException: The security token included in the request is invalid`. All AWS actual-state checks yield **CANNOT VERIFY VIA CLI**.

---

## 1) REPO INFRA EXPECTATION TABLE

| RESOURCE_TYPE | EXPECTED_NAME (from repo) | FILE:LINE |
|---------------|---------------------------|-----------|
| Batch compute environment | academy-video-batch-ce | scripts/infra/batch_video_setup.ps1:16 (param default); scripts/infra/batch/video_compute_env.json:1 |
| Batch job queue | academy-video-batch-queue | scripts/infra/batch_video_setup.ps1:17; scripts/infra/batch/video_job_queue.json:1 |
| Batch job definition (worker) | academy-video-batch-jobdef | scripts/infra/batch_video_setup.ps1:18; scripts/infra/batch/video_job_definition.json:1 |
| Batch job definition (ops) | academy-video-ops-reconcile | scripts/infra/batch_video_setup.ps1:219; scripts/infra/eventbridge/reconcile_to_batch_target.json:1 |
| Batch job definition (ops) | academy-video-ops-scanstuck | scripts/infra/eventbridge/scan_stuck_to_batch_target.json:1 |
| IAM role | academy-batch-service-role | scripts/infra/batch_video_setup.ps1:59 |
| IAM role | academy-batch-ecs-instance-role | scripts/infra/batch_video_setup.ps1:60 |
| IAM instance profile | academy-batch-ecs-instance-profile | scripts/infra/batch_video_setup.ps1:61 |
| IAM role | academy-video-batch-job-role | scripts/infra/batch_video_setup.ps1:63 |
| IAM role | academy-batch-ecs-task-execution-role | scripts/infra/batch_video_setup.ps1:64 |
| IAM role | academy-eventbridge-batch-video-role | scripts/infra/eventbridge_deploy_video_scheduler.ps1:27 |
| Log group | /aws/batch/academy-video-worker | scripts/infra/batch_video_setup.ps1:19; scripts/infra/batch/video_job_definition.json (logConfiguration) |
| Log group | /aws/batch/academy-video-ops | scripts/infra/batch_video_setup.ps1:53; scripts/infra/batch/video_ops_job_definition.json (logConfiguration) |
| EventBridge rule | academy-reconcile-video-jobs | scripts/infra/eventbridge_deploy_video_scheduler.ps1:44 |
| EventBridge rule | academy-video-scan-stuck-rate | scripts/infra/eventbridge_deploy_video_scheduler.ps1:60 |
| SSM parameter | /academy/workers/env | scripts/infra/ssm_bootstrap_video_worker.ps1:18 |
| ECR repository | academy-video-worker | scripts/infra/ecr_bootstrap.ps1:9; .github/workflows/video_batch_deploy.yml:22 |
| CloudWatch alarm | academy-video-DeadJobs | scripts/infra/cloudwatch_deploy_video_alarms.ps1:31; scripts/infra/cloudwatch/alarm_video_dead_jobs.json:2 |
| CloudWatch alarm | academy-video-UploadFailures | scripts/infra/cloudwatch_deploy_video_alarms.ps1:35; scripts/infra/cloudwatch/alarm_video_upload_failures.json:2 |
| CloudWatch alarm | academy-video-FailedJobs | scripts/infra/cloudwatch_deploy_video_alarms.ps1:39; scripts/infra/cloudwatch/alarm_video_failed_jobs.json:2 |
| CloudWatch alarm | academy-video-BatchJobFailures | scripts/infra/cloudwatch_deploy_video_alarms.ps1:45 |
| CloudWatch alarm | academy-video-QueueRunnable | scripts/infra/cloudwatch_deploy_video_alarms.ps1:48; scripts/infra/cloudwatch/alarm_batch_runnable.json:2 |
| EventBridge target (reconcile) | JobDefinition academy-video-ops-reconcile, JobName reconcile-video-jobs | scripts/infra/eventbridge/reconcile_to_batch_target.json:1 |
| EventBridge target (scan-stuck) | JobDefinition academy-video-ops-scanstuck, JobName scanstuck-video-jobs | scripts/infra/eventbridge/scan_stuck_to_batch_target.json:1 |

**SNS:** Repo references optional `-SnsTopicArn` in cloudwatch_deploy_video_alarms.ps1 (scripts/infra/cloudwatch_deploy_video_alarms.ps1:9). No fixed topic name in repo.

---

## 2) AWS ACTUAL STATE TABLE

**CANNOT VERIFY VIA CLI.** All commands below were attempted; each returned:

```
An error occurred (UnrecognizedClientException) when calling the <Operation> operation: The security token included in the request is invalid.
```

| RESOURCE_TYPE | COMMAND ATTEMPTED | RESULT |
|---------------|-------------------|--------|
| Batch compute environments | `aws batch describe-compute-environments --region ap-northeast-2` | CANNOT VERIFY VIA CLI |
| Batch job queues | `aws batch describe-job-queues --region ap-northeast-2` | CANNOT VERIFY VIA CLI |
| Batch job definitions | `aws batch describe-job-definitions --status ACTIVE --region ap-northeast-2` | CANNOT VERIFY VIA CLI |
| EventBridge rules | (not run; auth failed first) | CANNOT VERIFY VIA CLI |
| EventBridge targets | (not run) | CANNOT VERIFY VIA CLI |
| IAM roles | (not run) | CANNOT VERIFY VIA CLI |
| SSM parameter | (not run) | CANNOT VERIFY VIA CLI |
| ECR repositories | (not run) | CANNOT VERIFY VIA CLI |
| CloudWatch log groups | (not run) | CANNOT VERIFY VIA CLI |
| CloudWatch alarms | (not run) | CANNOT VERIFY VIA CLI |
| SNS topics | (not run) | CANNOT VERIFY VIA CLI |

---

## 3) MATCH STATUS MATRIX

Because AWS CLI could not authenticate, no match/mismatch can be determined for any resource.

| EXPECTED_NAME | STATUS |
|---------------|--------|
| academy-video-batch-ce | CANNOT VERIFY VIA CLI |
| academy-video-batch-queue | CANNOT VERIFY VIA CLI |
| academy-video-batch-jobdef | CANNOT VERIFY VIA CLI |
| academy-video-ops-reconcile | CANNOT VERIFY VIA CLI |
| academy-video-ops-scanstuck | CANNOT VERIFY VIA CLI |
| academy-batch-service-role | CANNOT VERIFY VIA CLI |
| academy-batch-ecs-instance-role | CANNOT VERIFY VIA CLI |
| academy-batch-ecs-instance-profile | CANNOT VERIFY VIA CLI |
| academy-video-batch-job-role | CANNOT VERIFY VIA CLI |
| academy-batch-ecs-task-execution-role | CANNOT VERIFY VIA CLI |
| academy-eventbridge-batch-video-role | CANNOT VERIFY VIA CLI |
| /aws/batch/academy-video-worker | CANNOT VERIFY VIA CLI |
| /aws/batch/academy-video-ops | CANNOT VERIFY VIA CLI |
| academy-reconcile-video-jobs | CANNOT VERIFY VIA CLI |
| academy-video-scan-stuck-rate | CANNOT VERIFY VIA CLI |
| /academy/workers/env | CANNOT VERIFY VIA CLI |
| academy-video-worker (ECR) | CANNOT VERIFY VIA CLI |
| academy-video-DeadJobs | CANNOT VERIFY VIA CLI |
| academy-video-UploadFailures | CANNOT VERIFY VIA CLI |
| academy-video-FailedJobs | CANNOT VERIFY VIA CLI |
| academy-video-BatchJobFailures | CANNOT VERIFY VIA CLI |
| academy-video-QueueRunnable | CANNOT VERIFY VIA CLI |

---

## 4) CONFIG DRIFT LIST

**CANNOT VERIFY VIA CLI.** No drift can be asserted without AWS actual state.

---

## 5) NOT DEPLOYED LIST

**CANNOT VERIFY VIA CLI.** Cannot determine which repo-defined resources are not deployed.

---

## 6) DEPLOYED BUT UNUSED LIST (DRIFT)

**CANNOT VERIFY VIA CLI.** Cannot determine resources that exist in AWS but are not in repo.

---

## 7) CRITICAL BREAKAGE LIST

- **CLI credential failure:** The environment used for this audit does not have valid AWS credentials (or token expired). No verification of deployed state was possible. Re-run the audit with valid `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (or equivalent) for the target account and region.

---

## 8) SCHEDULER VALIDATION (STEP 4)

**CANNOT VERIFY VIA CLI.** Required commands:

- `aws events list-rules --region ap-northeast-2`
- `aws events list-targets-by-rule --rule academy-reconcile-video-jobs --region ap-northeast-2`
- `aws events list-targets-by-rule --rule academy-video-scan-stuck-rate --region ap-northeast-2`

Could not be executed due to invalid security token.

---

## 9) BATCH WORKER VALIDATION (STEP 5)

**CANNOT VERIFY VIA CLI.** Test job submit and lifecycle checks require valid credentials. Not attempted.

---

## 10) ENVIRONMENT INJECTION VALIDATION (STEP 6)

**CANNOT VERIFY VIA CLI.** SSM get-parameter and worker log inspection require valid credentials. Not attempted.

---

## 11) FINAL STATUS

**PRODUCTION READY:** **CANNOT DETERMINE**

**Reason:** AWS CLI authentication failed. No factual verification of deployed resources was possible. To complete this audit:

1. Configure valid AWS credentials for the target account and region (ap-northeast-2).
2. Re-run the same CLI commands as in section 2.
3. Fill sections 2–7 and 8–10 with actual output and then set PRODUCTION READY to YES or NO based on match/mismatch and breakage list.

---

## APPENDIX: Commands to run for full verification (after fixing credentials)

```bash
export AWS_DEFAULT_REGION=ap-northeast-2

aws batch describe-compute-environments --region ap-northeast-2 --output json
aws batch describe-job-queues --region ap-northeast-2 --output json
aws batch describe-job-definitions --status ACTIVE --region ap-northeast-2 --output json
aws events list-rules --region ap-northeast-2 --output json
aws events list-targets-by-rule --rule academy-reconcile-video-jobs --region ap-northeast-2 --output json
aws events list-targets-by-rule --rule academy-video-scan-stuck-rate --region ap-northeast-2 --output json
aws iam list-roles --output json
aws ssm get-parameter --name /academy/workers/env --region ap-northeast-2 --output json
aws ecr describe-repositories --region ap-northeast-2 --output json
aws logs describe-log-groups --log-group-name-prefix /aws/batch/academy-video --region ap-northeast-2 --output json
aws cloudwatch describe-alarms --alarm-names academy-video-DeadJobs academy-video-UploadFailures academy-video-FailedJobs academy-video-BatchJobFailures academy-video-QueueRunnable --region ap-northeast-2 --output json
aws sns list-topics --region ap-northeast-2 --output json
```
