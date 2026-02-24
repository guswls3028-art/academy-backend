# ==============================================================================
# EventBridge rules for video Batch: reconcile + scan-stuck (rate 2 min). Target: AWS Batch SubmitJob only.
# Usage: .\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$JobQueueName = "academy-video-batch-queue"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$EventBridgePath = Join-Path $InfraPath "eventbridge"

function ExecJson($cmd) {
    $out = Invoke-Expression $cmd 2>&1
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

$AccountId = (aws sts get-caller-identity --query Account --output text 2>&1)
if ($LASTEXITCODE -ne 0) { Write-Host "AWS identity check failed" -ForegroundColor Red; exit 1 }

# EventBridge role for Batch SubmitJob
$EventsRoleName = "academy-eventbridge-batch-video-role"
$trustEvents = Join-Path $InfraPath "iam\trust_events.json"
$policyEventsBatch = Join-Path $InfraPath "iam\policy_eventbridge_batch_submit.json"
$role = $null
try { $role = ExecJson "aws iam get-role --role-name $EventsRoleName --output json 2>&1" } catch {}
if (-not $role) {
    Write-Host "[0] Creating IAM role: $EventsRoleName" -ForegroundColor Cyan
    aws iam create-role --role-name $EventsRoleName --assume-role-policy-document "file://$($trustEvents -replace '\\','/')" | Out-Null
}
if (Test-Path $policyEventsBatch) {
    aws iam put-role-policy --role-name $EventsRoleName --policy-name "academy-eventbridge-batch-inline" --policy-document "file://$($policyEventsBatch -replace '\\','/')" | Out-Null
}
$EventsRoleArn = (ExecJson "aws iam get-role --role-name $EventsRoleName --output json").Role.Arn
$JobQueueArn = (ExecJson "aws batch describe-job-queues --job-queues $JobQueueName --region $Region --output json").jobQueues[0].jobQueueArn
if (-not $JobQueueArn) { Write-Host "Job queue $JobQueueName not found." -ForegroundColor Red; exit 1 }

# Reconcile rule + Batch target
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

Write-Host "Done. EventBridge video scheduler (Batch only) deployed." -ForegroundColor Green
