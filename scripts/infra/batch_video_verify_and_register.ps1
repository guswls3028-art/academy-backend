# ==============================================================================
# AWS Batch Video Job Definition - Verify and Register (idempotent)
#
# STRICT: retryStrategy.attempts MUST be 1.
# Retry logic is handled by Django (scan_stuck_video_jobs), NOT Batch.
#
# Usage: .\scripts\infra\batch_video_verify_and_register.ps1 -Region ap-northeast-2 -EcrRepoUri 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
# ==============================================================================

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [Parameter(Mandatory=$true)][string]$EcrRepoUri,
    [string]$JobDefName = "academy-video-batch-jobdef"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$JdPath = Join-Path $InfraPath "batch\video_job_definition.json"

function Invoke-AwsJson {
    param([string[]]$Arguments)
    $prevErr = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & aws @Arguments 2>&1
        $text = ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($text)) { return $null }
        return $text | ConvertFrom-Json
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $prevErr
    }
}

function Fail($msg) {
    Write-Host "FAIL: $msg" -ForegroundColor Red
    exit 1
}

Write-Host "== Batch Video JobDefinition Verify and Register ==" -ForegroundColor Cyan
Write-Host "Region=$Region JobDef=$JobDefName" -ForegroundColor Gray

# 1) Verify source JSON has retryStrategy.attempts == 1
Write-Host ""
Write-Host "[1] Verify source video_job_definition.json" -ForegroundColor Cyan
$jdSource = Get-Content -LiteralPath $JdPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $jdSource) { Fail "Cannot parse $JdPath" }
if (-not $jdSource.retryStrategy) { Fail "retryStrategy is missing in $JdPath" }
$attempts = [int]$jdSource.retryStrategy.attempts
if ($attempts -ne 1) { Fail "retryStrategy.attempts must be 1 (got $attempts)" }
Write-Host "  OK retryStrategy.attempts=$attempts" -ForegroundColor Green

# 2) Get IAM role ARNs
Write-Host ""
Write-Host "[2] Get IAM role ARNs" -ForegroundColor Cyan
$JobRoleName = "academy-video-batch-job-role"
$ExecutionRoleName = "academy-batch-ecs-task-execution-role"
$jobRole = Invoke-AwsJson @("iam", "get-role", "--role-name", $JobRoleName, "--output", "json")
if (-not $jobRole) { Fail "IAM role $JobRoleName not found or AWS error (check credentials)" }
$execRole = Invoke-AwsJson @("iam", "get-role", "--role-name", $ExecutionRoleName, "--output", "json")
if (-not $execRole) { Fail "IAM role $ExecutionRoleName not found or AWS error (check credentials)" }
$jobRoleArn = $jobRole.Role.Arn
$executionRoleArn = $execRole.Role.Arn
Write-Host "  JobRole=$jobRoleArn" -ForegroundColor Gray
Write-Host "  ExecutionRole=$executionRoleArn" -ForegroundColor Gray

# 3) Substitute placeholders and register
Write-Host ""
Write-Host "[3] Register Job Definition revision" -ForegroundColor Cyan
$jdContent = Get-Content -LiteralPath $JdPath -Raw -Encoding UTF8
$jdContent = $jdContent -replace "PLACEHOLDER_ECR_URI", $EcrRepoUri
$jdContent = $jdContent -replace "PLACEHOLDER_JOB_ROLE_ARN", $jobRoleArn
$jdContent = $jdContent -replace "PLACEHOLDER_EXECUTION_ROLE_ARN", $executionRoleArn
$jdContent = $jdContent -replace "PLACEHOLDER_REGION", $Region
$jdFile = Join-Path $RepoRoot "batch_jd_temp.json"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($jdFile, $jdContent, $utf8NoBom)
$fileUri = "file:///" + ($jdFile -replace '\\', '/')
$regOut = Invoke-AwsJson @("batch", "register-job-definition", "--cli-input-json", $fileUri, "--region", $Region, "--output", "json")
if (-not $regOut) { Fail "register-job-definition failed (check AWS credentials and region)" }
Remove-Item $jdFile -Force -ErrorAction SilentlyContinue
$newRevision = $regOut.revision
Write-Host "  Registered revision $newRevision" -ForegroundColor Green

# 4) Verify deployed Job Definition has retryStrategy.attempts == 1
Write-Host ""
Write-Host "[4] Verify deployed retryStrategy" -ForegroundColor Cyan
$defs = ExecJson "aws batch describe-job-definitions --job-definition-name $JobDefName --status ACTIVE --region $Region --output json"
$latest = $defs.jobDefinitions | Sort-Object -Property revision -Descending | Select-Object -First 1
if (-not $latest -or $latest.revision -ne $newRevision) {
    Fail "Could not retrieve newly registered revision $newRevision"
}
$retry = $latest.retryStrategy
if (-not $retry) { Fail "retryStrategy is null in deployed JobDefinition" }
$deployedAttempts = [int]$retry.attempts
if ($deployedAttempts -ne 1) { Fail "retryStrategy.attempts must be 1 (got $deployedAttempts)" }
Write-Host "  OK retryStrategy.attempts=$deployedAttempts" -ForegroundColor Green

# 5) Output result
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "PASS" -ForegroundColor Green
Write-Host ("JobDefinition: " + $JobDefName + ":" + $newRevision) -ForegroundColor Gray
Write-Host "retryStrategy.attempts: 1" -ForegroundColor Gray
Write-Host "========================================" -ForegroundColor Cyan
exit 0
