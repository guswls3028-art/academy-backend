# ==============================================================================
# Recreate Batch in API/RDS VPC (Option A). Discovers API+RDS VPC, creates Batch SG, authorizes RDS/Redis, runs batch_video_setup.
# Usage: .\scripts\infra\recreate_batch_in_api_vpc.ps1 -Region ap-northeast-2 -EcrRepoUri <uri> [-ApiInstanceId i-xxx] [-DbIdentifier academy-db] [-CleanupOld] [-DryRun]
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$ApiInstanceId = "",
    [string]$DbIdentifier = "academy-db",
    [string]$ComputeEnvName = "academy-video-batch-ce",
    [string]$JobQueueName = "academy-video-batch-queue",
    [string]$WorkerJobDefName = "academy-video-batch-jobdef",
    [string]$OpsReconcileJobDefName = "academy-video-ops-reconcile",
    [string]$OpsScanstuckJobDefName = "academy-video-ops-scanstuck",
    [string[]]$SubnetIds = @(),
    [string]$SecurityGroupId = "",
    [Parameter(Mandatory=$true)][string]$EcrRepoUri,
    [switch]$CleanupOld,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$OutDir = Join-Path $RepoRoot "docs\deploy\actual_state"
$InfraPath = Join-Path $RepoRoot "scripts\infra"

function ExecJson($cmd) {
    $out = Invoke-Expression $cmd 2>&1
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

# --- 1) Discover API VPC/Subnet/SG ---
Write-Host "[1] Discover API instance network" -ForegroundColor Cyan
if ($ApiInstanceId) {
    $r = ExecJson "aws ec2 describe-instances --instance-ids $ApiInstanceId --region $Region --output json 2>&1"
    $apiInst = $r.Reservations.Instances | Where-Object { $_.InstanceId -eq $ApiInstanceId } | Select-Object -First 1
} else {
    & (Join-Path $ScriptRoot "discover_api_network.ps1") -Region $Region
    if ($LASTEXITCODE -ne 0) { exit 1 }
    $apiJson = Get-Content (Join-Path $OutDir "api_instance.json") -Raw | ConvertFrom-Json
    $apiInst = @{ VpcId = $apiJson.VpcId; SubnetId = $apiJson.SubnetId; SecurityGroupIds = $apiJson.SecurityGroupIds }
}
$apiVpcId = if ($apiInst.VpcId) { $apiInst.VpcId } else { $apiInst.VpcId }
$apiSubnetId = if ($apiInst.SubnetId) { $apiInst.SubnetId } else { $apiInst.SubnetId }
if (-not $apiVpcId) {
    Write-Host "FAIL: Could not get API VpcId." -ForegroundColor Red
    exit 1
}
Write-Host "  API VpcId=$apiVpcId SubnetId=$apiSubnetId" -ForegroundColor Gray

# --- 2) Discover RDS, assert same VPC ---
Write-Host "[2] Discover RDS and assert same VPC" -ForegroundColor Cyan
$rdsRaw = ExecJson "aws rds describe-db-instances --region $Region --output json 2>&1"
$db = $rdsRaw.DBInstances | Where-Object { $_.DBInstanceIdentifier -eq $DbIdentifier } | Select-Object -First 1
if (-not $db) {
    Write-Host "FAIL: RDS instance '$DbIdentifier' not found." -ForegroundColor Red
    exit 1
}
$rdsVpcId = $db.DBSubnetGroup.VpcId
$rdsSgIds = @($db.VpcSecurityGroups | ForEach-Object { $_.VpcSecurityGroupId })
if ($rdsVpcId -ne $apiVpcId) {
    Write-Host "FAIL: RDS VpcId=$rdsVpcId is not same as API VpcId=$apiVpcId." -ForegroundColor Red
    exit 1
}
Write-Host "  RDS VpcId=$rdsVpcId (same as API)" -ForegroundColor Gray
$rdsJson = @{ DBInstanceIdentifier = $db.DBInstanceIdentifier; VpcId = $rdsVpcId; VpcSecurityGroups = $rdsSgIds; Endpoint = $db.Endpoint.Address; Port = $db.Endpoint.Port } | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $OutDir "rds_instance.json"), $rdsJson, (New-Object System.Text.UTF8Encoding $false))

# --- 3) Subnets in VPC ---
if ($SubnetIds.Count -eq 0) {
    $subnetsResp = ExecJson "aws ec2 describe-subnets --filters Name=vpc-id,Values=$apiVpcId --region $Region --output json 2>&1"
    $SubnetIds = @($subnetsResp.Subnets | Select-Object -ExpandProperty SubnetId)
    if ($SubnetIds.Count -lt 2) {
        Write-Host "WARN: Only $($SubnetIds.Count) subnet(s) in VPC. Batch prefers 2+ for AZ spread." -ForegroundColor Yellow
    }
    if ($apiSubnetId -and ($SubnetIds -notcontains $apiSubnetId)) {
        $SubnetIds = @($apiSubnetId) + ($SubnetIds | Where-Object { $_ -ne $apiSubnetId })
    }
}
Write-Host "  SubnetIds: $($SubnetIds -join ', ')" -ForegroundColor Gray

# --- 4) Batch Security Group (academy-video-batch-sg) ---
if (-not $SecurityGroupId) {
    $existing = ExecJson "aws ec2 describe-security-groups --filters Name=vpc-id,Values=$apiVpcId Name=group-name,Values=academy-video-batch-sg --region $Region --output json 2>&1"
    if ($existing.SecurityGroups -and $existing.SecurityGroups.Count -gt 0) {
        $SecurityGroupId = $existing.SecurityGroups[0].GroupId
        Write-Host "[3] Using existing Batch SG: $SecurityGroupId" -ForegroundColor Cyan
    } else {
        if ($DryRun) { Write-Host "[3] DryRun: would create academy-video-batch-sg" -ForegroundColor Yellow; exit 0 }
        $sg = aws ec2 create-security-group --group-name academy-video-batch-sg --description "Batch compute (API VPC)" --vpc-id $apiVpcId --region $Region --output json | ConvertFrom-Json
        $SecurityGroupId = $sg.GroupId
        aws ec2 authorize-security-group-egress --group-id $SecurityGroupId --protocol all --cidr 0.0.0.0/0 --region $Region 2>$null | Out-Null
        Write-Host "[3] Created Batch SG: $SecurityGroupId" -ForegroundColor Cyan
    }
} else {
    Write-Host "[3] Using provided SecurityGroupId: $SecurityGroupId" -ForegroundColor Cyan
}

# --- 5) RDS SG: allow 5432 from Batch SG ---
foreach ($rdsSg in $rdsSgIds) {
    $inbound = ExecJson "aws ec2 describe-security-groups --group-ids $rdsSg --region $Region --query SecurityGroups[0].IpPermissions --output json 2>&1"
    $hasBatch = $false
    foreach ($perm in $inbound) {
        $fromSg = $perm.UserIdGroupPairs | Where-Object { $_.GroupId -eq $SecurityGroupId }
        if ($fromSg -and (($perm.FromPort -eq 5432) -or ($perm.FromPort -eq $null))) { $hasBatch = $true; break }
    }
    if (-not $hasBatch) {
        if ($DryRun) { Write-Host "DryRun: would authorize 5432 from $SecurityGroupId to RDS SG $rdsSg" -ForegroundColor Yellow } else {
            aws ec2 authorize-security-group-ingress --group-id $rdsSg --protocol tcp --port 5432 --source-group $SecurityGroupId --region $Region
            Write-Host "  Authorized 5432 from Batch SG to RDS SG $rdsSg" -ForegroundColor Green
        }
    } else { Write-Host "  RDS SG $rdsSg already allows 5432 from Batch SG" -ForegroundColor Gray }
}

# --- 6) Redis (ElastiCache): optional ---
$cacheList = ExecJson "aws elasticache describe-cache-clusters --region $Region --show-cache-node-info --output json 2>&1"
$redisSgId = $null
if ($cacheList.CacheClusters) {
    $vpcFilter = $cacheList.CacheClusters | Where-Object { $_.CacheSubnetGroupName } | Select-Object -First 1
    if ($vpcFilter) {
        $subnetGroup = ExecJson "aws elasticache describe-cache-subnet-groups --cache-subnet-group-name $($vpcFilter.CacheSubnetGroupName) --region $Region --output json 2>&1"
        if ($subnetGroup.CacheSubnetGroups -and $subnetGroup.CacheSubnetGroups[0].VpcId -eq $apiVpcId) {
            $redisSgId = $vpcFilter.SecurityGroups[0].SecurityGroupId
        }
    }
}
if ($redisSgId) {
    $redisInbound = ExecJson "aws ec2 describe-security-groups --group-ids $redisSgId --region $Region --query SecurityGroups[0].IpPermissions --output json 2>&1"
    $hasBatch = $false
    foreach ($perm in $redisInbound) {
        $fromSg = $perm.UserIdGroupPairs | Where-Object { $_.GroupId -eq $SecurityGroupId }
        if ($fromSg -and (($perm.FromPort -eq 6379) -or ($perm.FromPort -eq $null))) { $hasBatch = $true; break }
    }
    if (-not $hasBatch) {
        if (-not $DryRun) {
            aws ec2 authorize-security-group-ingress --group-id $redisSgId --protocol tcp --port 6379 --source-group $SecurityGroupId --region $Region
            Write-Host "  Authorized 6379 from Batch SG to Redis SG $redisSgId" -ForegroundColor Green
        }
    }
} else { Write-Host "  Redis (ElastiCache) in same VPC not found; skipping 6379" -ForegroundColor Gray }

# --- 7) CleanupOld: no running jobs -> delete queue -> delete old CE ---
if ($CleanupOld) {
    Write-Host "[4] CleanupOld: check running jobs" -ForegroundColor Cyan
    $running = ExecJson "aws batch list-jobs --job-queue $JobQueueName --job-status RUNNING --region $Region --output json 2>&1"
    $runnable = ExecJson "aws batch list-jobs --job-queue $JobQueueName --job-status RUNNABLE --region $Region --output json 2>&1"
    $nRun = ($running.jobSummaryList | Measure-Object).Count + ($runnable.jobSummaryList | Measure-Object).Count
    if ($nRun -gt 0) {
        Write-Host "FAIL: $nRun jobs still RUNNING/RUNNABLE on $JobQueueName. Drain before -CleanupOld." -ForegroundColor Red
        exit 1
    }
    aws batch update-job-queue --job-queue $JobQueueName --state DISABLED --region $Region | Out-Null
    Start-Sleep -Seconds 5
    aws batch delete-job-queue --job-queue $JobQueueName --region $Region
    Write-Host "  Deleted job queue $JobQueueName" -ForegroundColor Yellow
    $ceList = ExecJson "aws batch describe-compute-environments --region $Region --output json 2>&1"
    foreach ($ce in ($ceList.computeEnvironments | Where-Object { $_.computeEnvironmentName -match "academy-video-batch" })) {
        $ceVpcId = ""
        if ($ce.computeResources.subnets) {
            $subResp = ExecJson "aws ec2 describe-subnets --subnet-ids $($ce.computeResources.subnets[0]) --region $Region --output json 2>&1"
            if ($subResp.Subnets) { $ceVpcId = $subResp.Subnets[0].VpcId }
        }
        if ($ceVpcId -and $ceVpcId -ne $apiVpcId) {
            aws batch update-compute-environment --compute-environment $ce.computeEnvironmentName --state DISABLED --region $Region | Out-Null
            Write-Host "  Disabled old CE $($ce.computeEnvironmentName)" -ForegroundColor Yellow
            Start-Sleep -Seconds 15
            aws batch delete-compute-environment --compute-environment $ce.computeEnvironmentName --region $Region
            Write-Host "  Deleted old CE $($ce.computeEnvironmentName)" -ForegroundColor Yellow
        }
    }
}

if ($DryRun) {
    Write-Host "DryRun complete. Would call batch_video_setup with VpcId=$apiVpcId SubnetIds=$($SubnetIds -join ',') SecurityGroupId=$SecurityGroupId" -ForegroundColor Yellow
    exit 0
}

# --- 8) Call batch_video_setup ---
Write-Host "[5] Call batch_video_setup.ps1" -ForegroundColor Cyan
& (Join-Path $InfraPath "batch_video_setup.ps1") -Region $Region -VpcId $apiVpcId -SubnetIds $SubnetIds -SecurityGroupId $SecurityGroupId -EcrRepoUri $EcrRepoUri -ComputeEnvName $ComputeEnvName -JobQueueName $JobQueueName -JobDefName $WorkerJobDefName
if ($LASTEXITCODE -ne 0) { exit 1 }

# --- 9) Verify and save state ---
Write-Host "[6] Verify and save after_recreate_*.json" -ForegroundColor Cyan
$ceOut = aws batch describe-compute-environments --compute-environments $ComputeEnvName --region $Region --output json | ConvertFrom-Json
$jqOut = aws batch describe-job-queues --job-queues $JobQueueName --region $Region --output json | ConvertFrom-Json
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText((Join-Path $OutDir "after_recreate_ce.json"), ($ceOut | ConvertTo-Json -Depth 5), $utf8)
[System.IO.File]::WriteAllText((Join-Path $OutDir "after_recreate_queue.json"), ($jqOut | ConvertTo-Json -Depth 5), $utf8)
$ceObj = $ceOut.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvName } | Select-Object -First 1
$qObj = $jqOut.jobQueues | Where-Object { $_.jobQueueName -eq $JobQueueName } | Select-Object -First 1
Write-Host "  CE: $($ceObj.computeEnvironmentName) status=$($ceObj.status) state=$($ceObj.state)" -ForegroundColor Green
Write-Host "  Queue: $($qObj.jobQueueName) state=$($qObj.state)" -ForegroundColor Green
Write-Host "DONE. Batch recreated in API VPC." -ForegroundColor Green
