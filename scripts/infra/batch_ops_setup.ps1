# ==============================================================================
# Create Ops-only Batch CE and Queue (default_arm64, max 2 vCPU). Same VPC as video CE.
# Security Group: Uses the SAME security group as academy-video-batch-ce (no new SG).
# Ops jobs (reconcile, scan_stuck, netprobe) submit to academy-video-ops-queue only.
# Usage: .\scripts\infra\batch_ops_setup.ps1 -Region ap-northeast-2
#        .\scripts\infra\batch_ops_setup.ps1 -Region ap-northeast-2 -Verbose
# If VpcId/SubnetIds/SecurityGroupId omitted, discovers from existing academy-video-batch-ce.
# ==============================================================================

[CmdletBinding()]
param(
    [string]$Region = "ap-northeast-2",
    [string]$VpcId = "",
    [string[]]$SubnetIds = @(),
    [string]$SecurityGroupId = "",
    [string]$ComputeEnvName = "academy-video-ops-ce",
    [string]$JobQueueName = "academy-video-ops-queue"
)
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$OutDir = Join-Path $RepoRoot "docs\deploy\actual_state"

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

function Get-ComputeEnvironmentArn {
    param([string]$Name)
    $ce = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $Name, "--region", $Region, "--output", "json")
    if (-not $ce -or -not $ce.computeEnvironments) { return $null }
    $obj = $ce.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $Name } | Select-Object -First 1
    if (-not $obj) { return $null }
    return $obj.computeEnvironmentArn
}

function Get-JobQueueArn {
    param([string]$Name)
    $jq = ExecJson @("batch", "describe-job-queues", "--job-queues", $Name, "--region", $Region, "--output", "json")
    if (-not $jq -or -not $jq.jobQueues) { return $null }
    $q = $jq.jobQueues | Where-Object { $_.jobQueueName -eq $Name } | Select-Object -First 1
    if (-not $q) { return $null }
    return $q.jobQueueArn
}

Write-Host "== Ops Batch Setup (academy-video-ops-ce / academy-video-ops-queue) ==" -ForegroundColor Cyan

# Resolve VpcId, SubnetIds, SecurityGroupId from existing video CE if not provided
# Security Group: Ops CE uses the SAME security group as academy-video-batch-ce (e.g. academy-video-batch-sg).
if (-not $VpcId -or $SubnetIds.Count -eq 0 -or -not $SecurityGroupId) {
    $videoCe = ExecJson @("batch", "describe-compute-environments", "--compute-environments", "academy-video-batch-ce", "--region", $Region, "--output", "json")
    $videoCeObj = $videoCe.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq "academy-video-batch-ce" } | Select-Object -First 1
    if (-not $videoCeObj -or $videoCeObj.status -ne "VALID") {
        Write-Host "FAIL: academy-video-batch-ce not found or not VALID. Create video Batch first or pass -VpcId -SubnetIds -SecurityGroupId." -ForegroundColor Red
        exit 1
    }
    $cr = $videoCeObj.computeResources
    if (-not $SecurityGroupId -and $cr.securityGroupIds -and $cr.securityGroupIds.Count -gt 0) { $SecurityGroupId = $cr.securityGroupIds[0] }
    if ($SubnetIds.Count -eq 0 -and $cr.subnets) { $SubnetIds = @($cr.subnets) }
    if (-not $VpcId -and $SubnetIds.Count -gt 0) {
        $subResp = ExecJson @("ec2", "describe-subnets", "--subnet-ids", $SubnetIds[0], "--region", $Region, "--output", "json")
        if ($subResp.Subnets) { $VpcId = $subResp.Subnets[0].VpcId }
    }
}
if (-not $VpcId -or $SubnetIds.Count -eq 0 -or -not $SecurityGroupId) {
    Write-Host "FAIL: Could not resolve VpcId, SubnetIds, SecurityGroupId. Pass explicitly or ensure academy-video-batch-ce exists." -ForegroundColor Red
    exit 1
}
Write-Host "  VpcId=$VpcId SubnetIds=$($SubnetIds -join ',')" -ForegroundColor Gray
Write-Host "  SecurityGroupId=$SecurityGroupId (same as academy-video-batch-ce)" -ForegroundColor Gray

# IAM (same as video CE)
$BatchServiceRoleName = "academy-batch-service-role"
$InstanceProfileName = "academy-batch-ecs-instance-profile"
$serviceRoleArn = (ExecJson @("iam", "get-role", "--role-name", $BatchServiceRoleName, "--output", "json")).Role.Arn
$instanceProfileArn = (ExecJson @("iam", "get-instance-profile", "--instance-profile-name", $InstanceProfileName, "--output", "json")).InstanceProfile.Arn
if (-not $serviceRoleArn -or -not $instanceProfileArn) {
    Write-Host "FAIL: IAM role $BatchServiceRoleName or instance profile $InstanceProfileName not found. Run batch_video_setup first." -ForegroundColor Red
    exit 1
}

# Create Ops CE
Write-Host "`n[1] Compute Environment: $ComputeEnvName" -ForegroundColor Cyan
Write-Host "  (t4g.small, max 2 vCPU, On-Demand)" -ForegroundColor Gray
$ceJsonPath = Join-Path $InfraPath "batch\ops_compute_env.json"
$ceContent = Get-Content $ceJsonPath -Raw
$ceContent = $ceContent -replace "PLACEHOLDER_SERVICE_ROLE_ARN", $serviceRoleArn
$ceContent = $ceContent -replace "PLACEHOLDER_INSTANCE_PROFILE_ARN", $instanceProfileArn
$ceContent = $ceContent -replace "PLACEHOLDER_SECURITY_GROUP_ID", $SecurityGroupId
$subnetArr = ($SubnetIds | ForEach-Object { "`"$_`"" }) -join ","
$ceContent = $ceContent -replace '"PLACEHOLDER_SUBNET_1"', $subnetArr
$ceFile = Join-Path $RepoRoot "batch_ops_ce_temp.json"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($ceFile, $ceContent, $utf8NoBom)
$ceFileUri = "file://" + ($ceFile -replace '\\', '/')

$ce = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $ComputeEnvName, "--region", $Region, "--output", "json")
$ceObj = $ce.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvName } | Select-Object -First 1
if (-not $ceObj) {
    Write-Host "  Creating compute environment (t4g.small, max 2 vCPU)" -ForegroundColor Yellow
    aws batch create-compute-environment --cli-input-json $ceFileUri --region $Region
    if ($LASTEXITCODE -ne 0) { Write-Host "  FAIL: create-compute-environment failed." -ForegroundColor Red; Remove-Item $ceFile -Force -ErrorAction SilentlyContinue; exit 1 }
} else {
    Write-Host "  Compute environment exists; skipping create." -ForegroundColor Gray
}
Remove-Item $ceFile -Force -ErrorAction SilentlyContinue

Write-Host "  Waiting for compute environment VALID..." -ForegroundColor Gray
$wait = 0
while ($wait -lt 120) {
    $ce2 = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $ComputeEnvName, "--region", $Region, "--output", "json")
    $obj = $ce2.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvName } | Select-Object -First 1
    $status = $obj.status
    if ($status -eq "VALID") { break }
    if ($status -eq "INVALID") { Write-Host "  FAIL: CE state INVALID." -ForegroundColor Red; exit 1 }
    Start-Sleep -Seconds 5
    $wait += 5
}
$ceArn = Get-ComputeEnvironmentArn -Name $ComputeEnvName
if (-not $ceArn) { Write-Host "  FAIL: Could not get CE ARN." -ForegroundColor Red; exit 1 }
Write-Host "  CE ARN: $ceArn" -ForegroundColor Green

# Create Ops Queue
Write-Host "`n[2] Job Queue: $JobQueueName" -ForegroundColor Cyan
$jqPath = Join-Path $InfraPath "batch\ops_job_queue.json"
$jqContent = Get-Content $jqPath -Raw
$jqContent = $jqContent -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $ceArn
$jqTempFile = Join-Path $RepoRoot "batch_ops_jq_temp.json"
[System.IO.File]::WriteAllText($jqTempFile, $jqContent, $utf8NoBom)
$jqTempUri = "file://" + ($jqTempFile -replace '\\', '/')

$jq = ExecJson @("batch", "describe-job-queues", "--job-queues", $JobQueueName, "--region", $Region, "--output", "json")
$queueExists = $jq -and ($jq.jobQueues | Where-Object { $_.jobQueueName -eq $JobQueueName })
if (-not $queueExists) {
    Write-Host "  Creating job queue" -ForegroundColor Yellow
    aws batch create-job-queue --cli-input-json $jqTempUri --region $Region
    if ($LASTEXITCODE -ne 0) { Write-Host "  FAIL: create-job-queue failed." -ForegroundColor Red; Remove-Item $jqTempFile -Force -ErrorAction SilentlyContinue; exit 1 }
} else {
    Write-Host "  Job queue exists." -ForegroundColor Gray
}
Remove-Item $jqTempFile -Force -ErrorAction SilentlyContinue

$queueArn = Get-JobQueueArn -Name $JobQueueName
if (-not $queueArn) { Write-Host "  FAIL: Job queue not found after create." -ForegroundColor Red; exit 1 }
Write-Host "  Queue ARN: $queueArn" -ForegroundColor Green

# Write state
if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
$opsState = @{
    OpsComputeEnvName = $ComputeEnvName
    OpsComputeEnvArn  = $ceArn
    OpsJobQueueName  = $JobQueueName
    OpsJobQueueArn   = $queueArn
}
$opsStatePath = Join-Path $OutDir "batch_ops_state.json"
$opsState | ConvertTo-Json | Set-Content -Path $opsStatePath -Encoding UTF8
Write-Host "`n  Wrote $opsStatePath" -ForegroundColor Gray
Write-Host "`nDONE. Ops CE and queue ready. Use -OpsJobQueueName $JobQueueName when deploying EventBridge." -ForegroundColor Green
