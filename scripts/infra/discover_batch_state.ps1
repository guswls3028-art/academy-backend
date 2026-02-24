# ==============================================================================
# Discover Batch state: compute environments, job queues, job definitions (worker + ops).
# Saves docs/deploy/actual_state/batch_state.json. Shows current CE VpcId vs desired target VpcId.
# Usage: .\scripts\infra\discover_batch_state.ps1 -Region ap-northeast-2 [-TargetVpcId vpc-0831a2484f9b114c2]
#   -TargetVpcId: actual VPC ID (e.g. from discover_api_network.ps1), not a literal like <api_vpc_id> (PowerShell treats < as redirection).
# ==============================================================================

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [string]$TargetVpcId = ""
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$OutDir = Join-Path $RepoRoot "docs\deploy\actual_state"
$OutFile = Join-Path $OutDir "batch_state.json"

function ExecJson($cmd) {
    $out = Invoke-Expression $cmd 2>&1
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

$ceList = ExecJson "aws batch describe-compute-environments --region $Region --output json 2>&1"
$jqList = ExecJson "aws batch describe-job-queues --region $Region --output json 2>&1"
$jdWorker = ExecJson "aws batch describe-job-definitions --job-definition-name academy-video-batch-jobdef --status ACTIVE --region $Region --output json 2>&1"
$jdReconcile = ExecJson "aws batch describe-job-definitions --job-definition-name academy-video-ops-reconcile --status ACTIVE --region $Region --output json 2>&1"
$jdScanstuck = ExecJson "aws batch describe-job-definitions --job-definition-name academy-video-ops-scanstuck --status ACTIVE --region $Region --output json 2>&1"

$ceVpcId = ""
$ceSummary = @()
foreach ($ce in ($ceList.computeEnvironments | Where-Object { $_.computeEnvironmentName -match "academy-video-batch" })) {
    $subnets = $ce.computeResources.subnets -join ","
    $sgIds = $ce.computeResources.securityGroupIds -join ","
    $ceSummary += @{ name = $ce.computeEnvironmentName; status = $ce.status; state = $ce.state; subnets = $subnets; securityGroupIds = $sgIds }
    if ($ce.computeResources.subnets) {
        $subResp = ExecJson "aws ec2 describe-subnets --subnet-ids $($ce.computeResources.subnets[0]) --region $Region --output json 2>&1"
        if ($subResp.Subnets) { $ceVpcId = $subResp.Subnets[0].VpcId }
    }
}

$result = @{
    Region          = $Region
    TargetVpcId     = $TargetVpcId
    CurrentCeVpcId  = $ceVpcId
    InTargetVpc     = if ($TargetVpcId -and $ceVpcId) { $ceVpcId -eq $TargetVpcId } else { $null }
    ComputeEnvironments = $ceSummary
    JobQueues       = @($jqList.jobQueues | Where-Object { $_.jobQueueName -match "academy" } | ForEach-Object { @{ name = $_.jobQueueName; state = $_.state; status = $_.status } })
    WorkerJobDef    = if ($jdWorker.jobDefinitions) { @{ name = $jdWorker.jobDefinitions[0].jobDefinitionName; revision = $jdWorker.jobDefinitions[0].revision } } else { $null }
    OpsReconcileJobDef = if ($jdReconcile.jobDefinitions) { @{ name = $jdReconcile.jobDefinitions[0].jobDefinitionName; revision = $jdReconcile.jobDefinitions[0].revision } } else { $null }
    OpsScanstuckJobDef = if ($jdScanstuck.jobDefinitions) { @{ name = $jdScanstuck.jobDefinitions[0].jobDefinitionName; revision = $jdScanstuck.jobDefinitions[0].revision } } else { $null }
}
$json = $result | ConvertTo-Json -Depth 5
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($OutFile, $json, $utf8)
Write-Host "Current CE VpcId: $ceVpcId | Target VpcId: $TargetVpcId | InTargetVpc: $($result.InTargetVpc)" -ForegroundColor Cyan
Write-Host "Saved: $OutFile" -ForegroundColor Gray
