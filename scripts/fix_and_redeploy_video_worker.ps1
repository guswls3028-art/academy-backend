# ==============================================================================
# One-take: fix Batch worker (diagnosis + IAM + JobDef + post-verify).
# Run from repo root: .\scripts\fix_and_redeploy_video_worker.ps1
#
# Does NOT build/push image. Run first: .\scripts\build_and_push_ecr_remote.ps1 -VideoWorkerOnly
# ==============================================================================
$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$Region = $env:AWS_REGION; if (-not $Region) { $Region = $env:AWS_DEFAULT_REGION }; if (-not $Region) { $Region = "ap-northeast-2" }

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Ok($msg)  { Write-Host "  OK $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  WARN: $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "  FAIL: $msg" -ForegroundColor Red; exit 1 }

# 0) AWS identity and root check
$callerJson = aws sts get-caller-identity --output json 2>&1
if ($LASTEXITCODE -ne 0) { Fail "AWS identity check failed. Run aws configure or set AWS_PROFILE." }
$callerObj = $callerJson | ConvertFrom-Json
$AccountId = $callerObj.Account
$callerArn = $callerObj.Arn
if ($callerArn -match ":root") {
    Write-Host "ROOT CAUSE: Running with root credentials (unsafe, not representative of production roles)" -ForegroundColor Red
    exit 3
}
$EcrUri = "${AccountId}.dkr.ecr.${Region}.amazonaws.com/academy-video-worker:latest"

Write-Host "`n========== FIX AND REDEPLOY VIDEO WORKER (one-take) ==========" -ForegroundColor Cyan
Write-Host "  Region=$Region AccountId=$AccountId" -ForegroundColor Gray
Write-Host "  ECR URI=$EcrUri" -ForegroundColor Gray
Write-Host ""

# 1) Diagnosis
Step "1) Diagnosis"
& (Join-Path $ScriptRoot "diagnose_batch_worker.ps1")
if ($LASTEXITCODE -ne 0) { Fail "Diagnosis script failed." }
Write-Host ""

# 2) IAM – API role (TerminateJob + DescribeJobs for video delete)
Step "2) IAM – API role (batch:TerminateJob, DescribeJobs)"
$applyApi = Join-Path $ScriptRoot "apply_api_batch_submit_policy.ps1"
if (-not (Test-Path $applyApi)) { Fail "apply_api_batch_submit_policy.ps1 not found." }
& $applyApi
if ($LASTEXITCODE -ne 0) { Fail "apply_api_batch_submit_policy.ps1 failed." }
Ok "API role (academy-ec2-role) updated."
Write-Host ""

# 3) IAM – Batch CE instance role (ECR pull + CloudWatch logs on compute nodes)
Step "3) IAM – Batch CE instance role (ECR + logs)"
$batchAttach = Join-Path $RepoRoot "scripts\infra\batch_attach_ecs_instance_role_policies.ps1"
if (Test-Path $batchAttach) {
    & $batchAttach
    if ($LASTEXITCODE -ne 0) { Warn "batch_attach_ecs_instance_role_policies.ps1 failed; continue. Ensure academy-batch-ecs-instance-role has ECR + logs." }
    else { Ok "Batch CE instance role (academy-batch-ecs-instance-role) has ECR + logs." }
} else {
    Warn "batch_attach_ecs_instance_role_policies.ps1 not found. Ensure academy-batch-ecs-instance-role has ecr:GetAuthorizationToken, ecr:BatchGetImage, ecr:GetDownloadUrlForLayer, logs:CreateLogStream, logs:PutLogEvents."
}
Write-Host ""

# 4) IAM – Batch job role (reconcile: TerminateJob, DescribeJobs, SubmitJob)
Step "4) IAM – Batch job role (reconcile)"
$jobRoleName = "academy-video-batch-job-role"
$policyPath = Join-Path $RepoRoot "scripts\infra\iam\policy_video_job_role.json"
if (-not (Test-Path $policyPath)) { Fail "policy_video_job_role.json not found." }
$fileUri = "file://" + ((Resolve-Path -LiteralPath $policyPath).Path -replace '\\', '/')
aws iam put-role-policy --role-name $jobRoleName --policy-name "academy-video-batch-job-inline" --policy-document $fileUri 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Warn "put-role-policy for $jobRoleName failed (role may not exist yet). Continue." } else { Ok "Job role policy updated." }
Write-Host ""

# 5) Job definition – register new revision with current ECR URI + test job RUNNING
Step "5) Job definition – register revision and verify test job"
$verifyScript = Join-Path $RepoRoot "scripts\infra\batch_video_verify_and_register.ps1"
if (-not (Test-Path $verifyScript)) { Fail "batch_video_verify_and_register.ps1 not found." }
& $verifyScript -Region $Region -EcrRepoUri $EcrUri
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "========== NEXT ACTION ==========" -ForegroundColor Yellow
    Write-Host "  Job definition register or test job failed. Common causes:" -ForegroundColor Gray
    Write-Host "  - Image not pushed: run .\scripts\build_and_push_ecr_remote.ps1 -VideoWorkerOnly" -ForegroundColor Gray
    Write-Host "  - CE capacity/arch: ensure academy-video-batch-ce uses instanceTypes c6g.large (ARM64) to match image." -ForegroundColor Gray
    Write-Host "  - Instance role ECR/logs: run .\scripts\infra\batch_attach_ecs_instance_role_policies.ps1" -ForegroundColor Gray
    Write-Host "  - Queue/CE disabled: run scripts\infra\batch_video_setup.ps1 with your VpcId, SubnetIds, SecurityGroupId, EcrRepoUri." -ForegroundColor Gray
    exit 1
}
Ok "JobDef registered and test job reached RUNNING or completed."
Write-Host ""

# 6) Post-verify – optional TerminateJob probe (production principal)
Step "6) Post-verify"
Write-Host "  To verify TerminateJob from production API role, run from a host with that role (e.g. API server):" -ForegroundColor Gray
Write-Host "  python scripts\verify_batch_terminate.py" -ForegroundColor Gray
Write-Host "  Or delete a video that has a RUNNING/QUEUED job and check logs for VIDEO_DELETE_TERMINATE_OK." -ForegroundColor Gray
Ok "Done."
Write-Host ""
Write-Host "========== DONE ==========" -ForegroundColor Green
Write-Host "  - API role: batch:SubmitJob, TerminateJob, DescribeJobs" -ForegroundColor Gray
Write-Host "  - Batch CE instance role: ECR + CloudWatch Logs" -ForegroundColor Gray
Write-Host "  - Job role: batch:DescribeJobs, TerminateJob, SubmitJob (reconcile)" -ForegroundColor Gray
Write-Host "  - JobDef: new revision with $EcrUri" -ForegroundColor Gray
Write-Host "  - Test job: submitted and reached RUNNING or completed." -ForegroundColor Gray
Write-Host ""
