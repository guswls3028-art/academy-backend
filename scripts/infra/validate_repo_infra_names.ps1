# ==============================================================================
# Validate repo infra name consistency: CE name, queue JSON placeholder, jobdef names, log groups, event rules, SSM param.
# Usage: .\scripts\infra\validate_repo_infra_names.ps1 [-ComputeEnvName academy-video-batch-ce-v3]
# ==============================================================================

param(
    [string]$ComputeEnvName = "academy-video-batch-ce-v3",
    [string]$JobQueueName = "academy-video-batch-queue",
    [string]$WorkerJobDefName = "academy-video-batch-jobdef",
    [string]$OpsReconcileName = "academy-video-ops-reconcile",
    [string]$OpsScanstuckName = "academy-video-ops-scanstuck",
    [string]$LogGroupWorker = "/aws/batch/academy-video-worker",
    [string]$LogGroupOps = "/aws/batch/academy-video-ops",
    [string]$ReconcileRule = "academy-reconcile-video-jobs",
    [string]$ScanStuckRule = "academy-video-scan-stuck-rate",
    [string]$SsmParamName = "/academy/workers/env"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$fail = 0

# Queue JSON must contain placeholder (batch_video_setup replaces with ComputeEnvName)
$queuePath = Join-Path $InfraPath "batch\video_job_queue.json"
$queueContent = Get-Content $queuePath -Raw
if ($queueContent -notmatch "PLACEHOLDER_COMPUTE_ENV_NAME") {
    Write-Host "FAIL: $queuePath must contain PLACEHOLDER_COMPUTE_ENV_NAME for computeEnvironmentOrder." -ForegroundColor Red
    $fail = 1
} else {
    Write-Host "OK: queue JSON uses PLACEHOLDER_COMPUTE_ENV_NAME" -ForegroundColor Green
}
if ($queueContent -notmatch $JobQueueName) {
    Write-Host "FAIL: queue JSON jobQueueName is not $JobQueueName" -ForegroundColor Red
    $fail = 1
}

# CE JSON placeholder
$cePath = Join-Path $InfraPath "batch\video_compute_env.json"
$ceContent = Get-Content $cePath -Raw
if ($ceContent -notmatch "PLACEHOLDER_COMPUTE_ENV_NAME") {
    Write-Host "FAIL: $cePath must contain PLACEHOLDER_COMPUTE_ENV_NAME" -ForegroundColor Red
    $fail = 1
} else {
    Write-Host "OK: CE JSON uses PLACEHOLDER_COMPUTE_ENV_NAME" -ForegroundColor Green
}

# Worker jobdef JSON
$jdPath = Join-Path $InfraPath "batch\video_job_definition.json"
$jdContent = Get-Content $jdPath -Raw | ConvertFrom-Json
if ($jdContent.jobDefinitionName -ne $WorkerJobDefName) {
    Write-Host "FAIL: video_job_definition.json jobDefinitionName=$($jdContent.jobDefinitionName) expected $WorkerJobDefName" -ForegroundColor Red
    $fail = 1
}
if ($jdContent.containerProperties.logConfiguration.options."awslogs-group" -ne $LogGroupWorker) {
    Write-Host "FAIL: worker jobdef log group is not $LogGroupWorker" -ForegroundColor Red
    $fail = 1
}
Write-Host "OK: worker jobdef name and log group" -ForegroundColor Green

# Ops jobdef files
foreach ($name in @($OpsReconcileName, $OpsScanstuckName)) {
    $base = $name -replace "academy-video-ops-", ""
    $path = Join-Path $InfraPath "batch\video_ops_job_definition_$base.json"
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Host "FAIL: $path not found" -ForegroundColor Red
        $fail = 1
    } else {
    $opsJ = Get-Content $path -Raw | ConvertFrom-Json
    if ($opsJ.jobDefinitionName -ne $name) {
        Write-Host "FAIL: $path jobDefinitionName=$($opsJ.jobDefinitionName) expected $name" -ForegroundColor Red
        $fail = 1
    }
    if ($opsJ.containerProperties.logConfiguration.options."awslogs-group" -ne $LogGroupOps) {
        Write-Host "FAIL: $path log group is not $LogGroupOps" -ForegroundColor Red
        $fail = 1
    }
    }
}
Write-Host "OK: ops jobdef names and log group" -ForegroundColor Green

# EventBridge target JSON job definition names
$reconcileTargetPath = Join-Path $InfraPath "eventbridge\reconcile_to_batch_target.json"
$reconcileTarget = Get-Content $reconcileTargetPath -Raw | ConvertFrom-Json
if ($reconcileTarget[0].BatchParameters.JobDefinition -ne $OpsReconcileName) {
    Write-Host "FAIL: reconcile_to_batch_target.json JobDefinition is not $OpsReconcileName" -ForegroundColor Red
    $fail = 1
}
$scanstuckTargetPath = Join-Path $InfraPath "eventbridge\scan_stuck_to_batch_target.json"
$scanstuckTarget = Get-Content $scanstuckTargetPath -Raw | ConvertFrom-Json
if ($scanstuckTarget[0].BatchParameters.JobDefinition -ne $OpsScanstuckName) {
    Write-Host "FAIL: scan_stuck_to_batch_target.json JobDefinition is not $OpsScanstuckName" -ForegroundColor Red
    $fail = 1
}
Write-Host "OK: EventBridge target jobdef names" -ForegroundColor Green

# SSM param name (referenced in scripts)
Write-Host "OK: SSM param name $SsmParamName (script reference)" -ForegroundColor Green

if ($fail -ne 0) {
    Write-Host "validate_repo_infra_names: FAIL" -ForegroundColor Red
    exit 1
}
Write-Host "validate_repo_infra_names: PASS" -ForegroundColor Green
