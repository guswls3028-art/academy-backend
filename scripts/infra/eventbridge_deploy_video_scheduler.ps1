# ==============================================================================
# EventBridge rules for video Batch: reconcile + scan-stuck (rate 2 min). Target: AWS Batch SubmitJob only.
# Ops jobs (reconcile, scan_stuck) submit to academy-video-ops-queue. Video jobs stay on academy-video-batch-queue.
# Usage: .\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region ap-northeast-2 -OpsJobQueueName academy-video-ops-queue
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$OpsJobQueueName = "academy-video-ops-queue"
)
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$EventBridgePath = Join-Path $InfraPath "eventbridge"

$RequiredOpsJobDefs = @("academy-video-ops-reconcile", "academy-video-ops-scanstuck")

function ExecJson($argsArray) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @argsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

$AccountId = (aws sts get-caller-identity --query Account --output text 2>&1)
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: AWS identity check failed" -ForegroundColor Red; exit 1 }

# Resolve Ops Job Queue ARN (reconcile + scan_stuck submit here)
$jqResp = ExecJson @("batch", "describe-job-queues", "--job-queues", $OpsJobQueueName, "--region", $Region, "--output", "json")
if (-not $jqResp -or -not $jqResp.jobQueues -or $jqResp.jobQueues.Count -eq 0) {
    Write-Host "FAIL: Ops job queue $OpsJobQueueName not found. Run batch_ops_setup.ps1 first." -ForegroundColor Red
    exit 1
}
$JobQueueArn = $jqResp.jobQueues[0].jobQueueArn
if (-not $JobQueueArn) { Write-Host "FAIL: Ops job queue ARN empty." -ForegroundColor Red; exit 1 }
Write-Host "Ops Job Queue: $OpsJobQueueName -> $JobQueueArn" -ForegroundColor Gray

# Validate job definitions ACTIVE before wiring
foreach ($jdName in $RequiredOpsJobDefs) {
    $jd = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $jdName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
    if (-not $jd -or -not $jd.jobDefinitions -or $jd.jobDefinitions.Count -eq 0) {
        Write-Host "FAIL: Job definition $jdName is not ACTIVE. Register ops job defs first (batch_video_setup)." -ForegroundColor Red
        exit 1
    }
}

# EventBridge role for Batch SubmitJob
$EventsRoleName = "academy-eventbridge-batch-video-role"
$trustEvents = Join-Path $InfraPath "iam\trust_events.json"
$policyEventsBatch = Join-Path $InfraPath "iam\policy_eventbridge_batch_submit.json"
$role = $null
$role = ExecJson @("iam", "get-role", "--role-name", $EventsRoleName, "--output", "json")
if (-not $role) {
    Write-Host "[0] Creating IAM role: $EventsRoleName" -ForegroundColor Cyan
    aws iam create-role --role-name $EventsRoleName --assume-role-policy-document "file://$($trustEvents -replace '\\','/')" | Out-Null
}
if (Test-Path $policyEventsBatch) {
    aws iam put-role-policy --role-name $EventsRoleName --policy-name "academy-eventbridge-batch-inline" --policy-document "file://$($policyEventsBatch -replace '\\','/')" | Out-Null
}
$EventsRoleArn = (ExecJson @("iam", "get-role", "--role-name", $EventsRoleName, "--output", "json")).Role.Arn
if (-not $EventsRoleArn) { Write-Host "FAIL: EventBridge role $EventsRoleName not found." -ForegroundColor Red; exit 1 }

# Reconcile rule + Batch target (Targets must be LIST)
$ReconcileRuleName = "academy-reconcile-video-jobs"
Write-Host "[1] EventBridge rule: $ReconcileRuleName (rate 2 minutes) -> Batch" -ForegroundColor Cyan
aws events put-rule --name $ReconcileRuleName --schedule-expression "rate(2 minutes)" --state ENABLED --description "Trigger reconcile_batch_video_jobs via Batch SubmitJob" --region $Region | Out-Null
$reconcileTargetPath = Join-Path $EventBridgePath "reconcile_to_batch_target.json"
$reconcileTargetJson = Get-Content $reconcileTargetPath -Raw
$reconcileTargetJson = $reconcileTargetJson -replace "PLACEHOLDER_JOB_QUEUE_ARN", $JobQueueArn
$reconcileTargetJson = $reconcileTargetJson -replace "PLACEHOLDER_EVENTBRIDGE_BATCH_ROLE_ARN", $EventsRoleArn
$reconcileTargetFile = Join-Path $RepoRoot "eventbridge_reconcile_target_temp.json"
$utf8 = New-Object System.Text.UTF8Encoding $false
$reconcileTargetObj = $reconcileTargetJson | ConvertFrom-Json
$reconcileTargetsArray = @($reconcileTargetObj)
$reconcileInput = @{ Rule = $ReconcileRuleName; Targets = $reconcileTargetsArray } | ConvertTo-Json -Depth 10 -Compress
[System.IO.File]::WriteAllText($reconcileTargetFile, $reconcileInput, $utf8)
$prevEv = $ErrorActionPreference
$ErrorActionPreference = "Continue"
aws events put-targets --cli-input-json "file://$($reconcileTargetFile -replace '\\','/')" --region $Region 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: put-targets for $ReconcileRuleName failed." -ForegroundColor Red; Remove-Item $reconcileTargetFile -Force -ErrorAction SilentlyContinue; $ErrorActionPreference = $prevEv; exit 1 }
$ErrorActionPreference = $prevEv
Remove-Item $reconcileTargetFile -Force -ErrorAction SilentlyContinue

# Verify reconcile targets
$tgtReconcile = ExecJson @("events", "list-targets-by-rule", "--rule", $ReconcileRuleName, "--region", $Region, "--output", "json")
if (-not $tgtReconcile -or -not $tgtReconcile.Targets -or $tgtReconcile.Targets.Count -eq 0) {
    Write-Host "FAIL: Rule $ReconcileRuleName has no targets after put-targets." -ForegroundColor Red
    exit 1
}
$t0 = $tgtReconcile.Targets[0]
if (-not $t0.BatchParameters -or $t0.BatchParameters.JobDefinition -ne "academy-video-ops-reconcile") {
    Write-Host "FAIL: Reconcile target JobDefinition mismatch." -ForegroundColor Red
    exit 1
}
if ($t0.Arn -ne $JobQueueArn) {
    Write-Host "FAIL: Reconcile target Arn does not match JobQueueArn." -ForegroundColor Red
    exit 1
}

# Scan-stuck rule + Batch target
$ScanStuckRuleName = "academy-video-scan-stuck-rate"
Write-Host "[2] EventBridge rule: $ScanStuckRuleName (rate 2 minutes) -> Batch" -ForegroundColor Cyan
aws events put-rule --name $ScanStuckRuleName --schedule-expression "rate(2 minutes)" --state ENABLED --description "Trigger scan_stuck_video_jobs via Batch SubmitJob" --region $Region | Out-Null
$scanstuckTargetPath = Join-Path $EventBridgePath "scan_stuck_to_batch_target.json"
$scanstuckTargetJson = Get-Content $scanstuckTargetPath -Raw
$scanstuckTargetJson = $scanstuckTargetJson -replace "PLACEHOLDER_JOB_QUEUE_ARN", $JobQueueArn
$scanstuckTargetJson = $scanstuckTargetJson -replace "PLACEHOLDER_EVENTBRIDGE_BATCH_ROLE_ARN", $EventsRoleArn
$scanstuckTargetFile = Join-Path $RepoRoot "eventbridge_scanstuck_target_temp.json"
$scanstuckTargetObj = $scanstuckTargetJson | ConvertFrom-Json
$scanstuckTargetsArray = @($scanstuckTargetObj)
$scanstuckInput = @{ Rule = $ScanStuckRuleName; Targets = $scanstuckTargetsArray } | ConvertTo-Json -Depth 10 -Compress
[System.IO.File]::WriteAllText($scanstuckTargetFile, $scanstuckInput, $utf8)
$ErrorActionPreference = "Continue"
aws events put-targets --cli-input-json "file://$($scanstuckTargetFile -replace '\\','/')" --region $Region 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: put-targets for $ScanStuckRuleName failed." -ForegroundColor Red; Remove-Item $scanstuckTargetFile -Force -ErrorAction SilentlyContinue; $ErrorActionPreference = $prevEv; exit 1 }
$ErrorActionPreference = $prevEv
Remove-Item $scanstuckTargetFile -Force -ErrorAction SilentlyContinue

# Verify scan-stuck targets
$tgtScan = ExecJson @("events", "list-targets-by-rule", "--rule", $ScanStuckRuleName, "--region", $Region, "--output", "json")
if (-not $tgtScan -or -not $tgtScan.Targets -or $tgtScan.Targets.Count -eq 0) {
    Write-Host "FAIL: Rule $ScanStuckRuleName has no targets after put-targets." -ForegroundColor Red
    exit 1
}
$t1 = $tgtScan.Targets[0]
if (-not $t1.BatchParameters -or $t1.BatchParameters.JobDefinition -ne "academy-video-ops-scanstuck") {
    Write-Host "FAIL: Scan-stuck target JobDefinition mismatch." -ForegroundColor Red
    exit 1
}
if ($t1.Arn -ne $JobQueueArn) {
    Write-Host "FAIL: Scan-stuck target Arn does not match JobQueueArn." -ForegroundColor Red
    exit 1
}

Write-Host "Done. EventBridge video scheduler (Batch only) deployed; targets verified." -ForegroundColor Green
