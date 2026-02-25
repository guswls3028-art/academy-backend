# ==============================================================================
# Video 워커 인프라 원테이크: 한 번에 전체 설정 완료.
# Usage: .\scripts\infra\video_worker_infra_one_take.ps1 -Region ap-northeast-2
#        .\scripts\infra\video_worker_infra_one_take.ps1 -Region ap-northeast-2 -BuildPush -FixMode
# ==============================================================================
param(
    [string]$Region = "ap-northeast-2",
    [switch]$BuildPush = $false,
    [switch]$FixMode = $true
)
# 0) UTF-8
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$OutDir = Join-Path $RepoRoot "docs\deploy\actual_state"

function ExecJson($a) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @a 2>&1
    $ErrorActionPreference = $prev
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    $s = ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($s)) { return $null }
    try { return $s | ConvertFrom-Json } catch { return $null }
}

function Invoke-Step { param([string]$Name, [scriptblock]$Block)
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "Step failed: $Name" }
}

# 1) API Private IP
Invoke-Step "1) API Private IP (discover_api_network)" {
    & (Join-Path $ScriptRoot "discover_api_network.ps1") -Region $Region
}

# 2) SSM bootstrap
Invoke-Step "2) SSM bootstrap" {
    & (Join-Path $ScriptRoot "ssm_bootstrap_video_worker.ps1") -Region $Region -EnvFile (Join-Path $RepoRoot ".env") -Overwrite -UsePrivateApiIp
}

# 3) (옵션) 이미지 빌드/푸시
if ($BuildPush) {
    Invoke-Step "3) ECR build/push (VideoWorkerOnly)" {
        & (Join-Path $RepoRoot "scripts\build_and_push_ecr_remote.ps1") -VideoWorkerOnly -Region $Region
    }
} else {
    Write-Host "`n=== 3) ECR build/push SKIPPED (-BuildPush not set) ===" -ForegroundColor Gray
}

# 4) Video Batch in API VPC
Invoke-Step "4) Video Batch in API VPC (recreate_batch_in_api_vpc)" {
    $acctId = (aws sts get-caller-identity --query Account --output text 2>&1).Trim()
    if (-not $acctId) { throw "Could not get Account ID" }
    $ecrUri = "${acctId}.dkr.ecr.${Region}.amazonaws.com/academy-video-worker:latest"
    & (Join-Path $ScriptRoot "recreate_batch_in_api_vpc.ps1") -Region $Region -EcrRepoUri $ecrUri -ComputeEnvName "academy-video-batch-ce-final" -JobQueueName "academy-video-batch-queue"
}
$batchStatePath = Join-Path $OutDir "batch_final_state.json"
if (-not (Test-Path -LiteralPath $batchStatePath)) { throw "batch_final_state.json not found after step 4" }

# 5) Ops CE + Ops Queue + IAM
$videoCeForOps = ExecJson @("batch", "describe-compute-environments", "--compute-environments", "academy-video-batch-ce-final", "--region", $Region, "--output", "json")
$videoCeObj = $videoCeForOps.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq "academy-video-batch-ce-final" } | Select-Object -First 1
$opsVpcId = ""; $opsSubnetIds = @(); $opsSgId = ""
if ($videoCeObj -and $videoCeObj.computeResources) {
    $cr = $videoCeObj.computeResources
    if ($cr.securityGroupIds -and $cr.securityGroupIds.Count -gt 0) { $opsSgId = $cr.securityGroupIds[0] }
    if ($cr.subnets) { $opsSubnetIds = @($cr.subnets) }
    if ($opsSubnetIds.Count -gt 0) {
        $subResp = ExecJson @("ec2", "describe-subnets", "--subnet-ids", $opsSubnetIds[0], "--region", $Region, "--output", "json")
        if ($subResp -and $subResp.Subnets -and $subResp.Subnets.Count -gt 0) { $opsVpcId = $subResp.Subnets[0].VpcId }
    }
}
Invoke-Step "5) Ops CE + Ops Queue" {
    if ($opsVpcId -and $opsSubnetIds.Count -gt 0 -and $opsSgId) {
        & (Join-Path $ScriptRoot "batch_ops_setup.ps1") -Region $Region -VpcId $opsVpcId -SubnetIds $opsSubnetIds -SecurityGroupId $opsSgId
    } else {
        & (Join-Path $ScriptRoot "batch_ops_setup.ps1") -Region $Region
    }
}
Invoke-Step "5b) IAM attach Batch DescribeJobs" {
    & (Join-Path $ScriptRoot "iam_attach_batch_describe_jobs.ps1") -Region $Region
}

# 6) EventBridge (Ops 큐 타깃, reconcile rate(15 minutes) 유지)
Invoke-Step "6) EventBridge (Ops queue, reconcile 15min)" {
    & (Join-Path $ScriptRoot "eventbridge_deploy_video_scheduler.ps1") -Region $Region -OpsJobQueueName "academy-video-ops-queue"
}
# Reconcile Redis lock evidence
$reconcilePy = Join-Path $RepoRoot "apps\support\video\management\commands\reconcile_batch_video_jobs.py"
if (Test-Path -LiteralPath $reconcilePy) {
    $lockKey = (Select-String -Path $reconcilePy -Pattern 'RECONCILE_LOCK_KEY\s*=\s*"([^"]+)"' -AllMatches).Matches.Groups[1].Value
    $lockTtl = (Select-String -Path $reconcilePy -Pattern 'RECONCILE_LOCK_TTL_SECONDS\s*=\s*(\d+)' -AllMatches).Matches.Groups[1].Value
    Write-Host "  [Evidence] reconcile_batch_video_jobs.py Redis lock: key=$lockKey TTL=${lockTtl}s" -ForegroundColor Gray
} else {
    Write-Host "  [Evidence] reconcile_batch_video_jobs.py not found; lock key/TTL not printed." -ForegroundColor Yellow
}

# 7) CloudWatch 알람 (Video 큐 기준)
Invoke-Step "7) CloudWatch alarms (Video queue)" {
    $state = Get-Content $batchStatePath -Raw | ConvertFrom-Json
    $q = $state.FinalJobQueueName
    if (-not $q) { $q = "academy-video-batch-queue" }
    & (Join-Path $ScriptRoot "cloudwatch_deploy_video_alarms.ps1") -Region $Region -JobQueueName $q
}

# 8) Netprobe + production done check
Invoke-Step "8) Netprobe (Ops queue)" {
    & (Join-Path $ScriptRoot "run_netprobe_job.ps1") -Region $Region -JobQueueName "academy-video-ops-queue"
}
$state = Get-Content $batchStatePath -Raw | ConvertFrom-Json
$videoCeName = $state.FinalComputeEnvName
if (-not $videoCeName) { $videoCeName = "academy-video-batch-ce-final" }
Invoke-Step "8b) Production done check" {
    & (Join-Path $ScriptRoot "production_done_check.ps1") -Region $Region -ComputeEnvName $videoCeName -JobQueueName $state.FinalJobQueueName -OpsJobQueueName "academy-video-ops-queue"
}

# 9) 정합성 감사 (FixMode 기본 ON)
if ($FixMode) {
    Invoke-Step "9) Audit + FixMode" {
        & (Join-Path $ScriptRoot "infra_one_take_full_audit.ps1") -Region $Region -FixMode -ExpectedVideoCEName "academy-video-batch-ce-final" -ExpectedVideoQueueName "academy-video-batch-queue" -ExpectedOpsQueueName "academy-video-ops-queue" -ExpectedOpsCEName "academy-video-ops-ce"
    }
} else {
    Invoke-Step "9) Audit (no FixMode)" {
        & (Join-Path $ScriptRoot "infra_one_take_full_audit.ps1") -Region $Region -ExpectedVideoCEName "academy-video-batch-ce-final" -ExpectedVideoQueueName "academy-video-batch-queue" -ExpectedOpsQueueName "academy-video-ops-queue" -ExpectedOpsCEName "academy-video-ops-ce"
    }
}

# Evidence (B): CE instanceTypes, Queue computeEnvironmentOrder, JobDef latest vcpus/memory, submit 경로
Write-Host "`n=== Evidence (production fix) ===" -ForegroundColor Cyan
$ceOut = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $videoCeName, "--region", $Region, "--output", "json")
if ($ceOut -and $ceOut.computeEnvironments -and $ceOut.computeEnvironments.Count -gt 0) {
    $ce = $ceOut.computeEnvironments[0]
    Write-Host "  Video CE $videoCeName instanceTypes=$($ce.computeResources.instanceTypes -join ',')" -ForegroundColor Gray
}
$vq = $state.FinalJobQueueName
if (-not $vq) { $vq = "academy-video-batch-queue" }
$qOut = ExecJson @("batch", "describe-job-queues", "--job-queues", $vq, "--region", $Region, "--output", "json")
if ($qOut -and $qOut.jobQueues -and $qOut.jobQueues.Count -gt 0) {
    $qo = $qOut.jobQueues[0].computeEnvironmentOrder
    if ($qo -and $qo.Count -gt 0) { Write-Host "  Video Queue $vq computeEnvironmentOrder[0]=$($qo[0].computeEnvironment)" -ForegroundColor Gray }
}
$jdOut = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", "academy-video-batch-jobdef", "--status", "ACTIVE", "--region", $Region, "--output", "json")
if ($jdOut -and $jdOut.jobDefinitions -and $jdOut.jobDefinitions.Count -gt 0) {
    $latest = $jdOut.jobDefinitions | Sort-Object { [int]$_.revision } -Descending | Select-Object -First 1
    Write-Host "  JobDef academy-video-batch-jobdef latest: revision=$($latest.revision) vcpus=$($latest.containerProperties.vcpus) memory=$($latest.containerProperties.memory)" -ForegroundColor Gray
}
$batchSubmitPy = Join-Path $RepoRoot "apps\support\video\services\batch_submit.py"
if (Test-Path -LiteralPath $batchSubmitPy) {
    $hasRevision = Select-String -Path $batchSubmitPy -Pattern "jobDefinition.*:.*revision|revision.*job_def" -Quiet
    Write-Host "  Submit path (batch_submit.py): jobDefinition name only (revision hardcode=$hasRevision)" -ForegroundColor Gray
}

Write-Host "`nVIDEO WORKER INFRA ONE-TAKE: DONE" -ForegroundColor Green
