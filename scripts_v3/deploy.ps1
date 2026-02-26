# ==============================================================================
# SSOT v3 — 단일 진입점 배포
# 사용: .\scripts_v3\deploy.ps1 -EcrRepoUri "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest"
#      .\scripts_v3\deploy.ps1 -Mode Audit  (변경 없이 Preflight + Evidence만)
# CI: pwsh -File scripts_v3/deploy.ps1 -EcrRepoUri "${{ needs.build-and-push.outputs.ecr_uri }}"
# ==============================================================================

param(
    [Parameter(Mandatory=$false)]
    [string]$EcrRepoUri = "",
    [ValidateSet("Deploy", "Audit")]
    [string]$Mode = "Deploy"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..")).Path

# env + core
. (Join-Path $ScriptRoot "env\prod.ps1")
. (Join-Path $ScriptRoot "core\logging.ps1")
. (Join-Path $ScriptRoot "core\preflight.ps1")
. (Join-Path $ScriptRoot "core\wait.ps1")
. (Join-Path $ScriptRoot "core\evidence.ps1")

Write-Host "=== ONE-TAKE DEPLOY (SSOT v3) Mode=$Mode ===" -ForegroundColor Cyan
Write-Host "Region=$script:Region" -ForegroundColor Gray

Invoke-PreflightCheck -Region $script:Region

if ($Mode -eq "Audit") {
    Show-Evidence -Region $script:Region
    Write-Host "`n=== AUDIT DONE ===" -ForegroundColor Green
    exit 0
}

# Deploy 모드: EcrRepoUri 필수
if (-not $EcrRepoUri) {
    $EcrRepoUri = $env:ECR_URI
}
if (-not $EcrRepoUri -or $EcrRepoUri -match '[<>]') {
    Write-Fail "EcrRepoUri required for Deploy. Example: 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest"
    exit 1
}

$InfraPath = Join-Path $RepoRoot "scripts\infra"
$SubnetList = $script:SubnetIds

# 레거시 스크립트 호출 허용 (단일 진입점에서만)
$env:ALLOW_LEGACY_IMPORT = "1"
try {
    # 1) Batch Video (CE, Queue, JobDef)
    Write-Step "Ensure Batch Video (CE, Queue, JobDef)"
    & (Join-Path $InfraPath "batch_video_setup.ps1") `
        -Region $script:Region `
        -VpcId $script:VpcId `
        -SubnetIds $SubnetList `
        -SecurityGroupId $script:SecurityGroupId `
        -EcrRepoUri $EcrRepoUri `
        -ComputeEnvName $script:VideoCEName `
        -JobQueueName $script:VideoQueueName `
        -JobDefName $script:VideoJobDefName
    if ($LASTEXITCODE -ne 0) { throw "batch_video_setup failed" }

    # 2) Batch Ops (Ops CE, Ops Queue)
    Write-Step "Ensure Batch Ops"
    & (Join-Path $InfraPath "batch_ops_setup.ps1") `
        -Region $script:Region `
        -VideoCeNameForDiscovery $script:VideoCEName
    if ($LASTEXITCODE -ne 0) { throw "batch_ops_setup failed" }

    # 3) EventBridge (reconcile, scan_stuck targets)
    Write-Step "Ensure EventBridge"
    & (Join-Path $InfraPath "eventbridge_deploy_video_scheduler.ps1") `
        -Region $script:Region `
        -OpsJobQueueName $script:OpsQueueName
    if ($LASTEXITCODE -ne 0) { throw "eventbridge_deploy_video_scheduler failed" }

    # 4) Video CE/Queue 가 DISABLED 이면 ENABLED 로 복구 (SSOT: 항상 ENABLED)
    Write-Step "Ensure Video CE and Queue ENABLED"
    $ceOut = aws batch describe-compute-environments --compute-environments $script:VideoCEName --region $script:Region --output json 2>&1 | ConvertFrom-Json
    $ceObj = $ceOut.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $script:VideoCEName } | Select-Object -First 1
    if ($ceObj -and $ceObj.state -eq "DISABLED") {
        Write-Host "  Enabling Video CE $script:VideoCEName" -ForegroundColor Yellow
        aws batch update-compute-environment --compute-environment $script:VideoCEName --state ENABLED --region $script:Region 2>&1 | Out-Null
    }
    $jqOut = aws batch describe-job-queues --job-queues $script:VideoQueueName --region $script:Region --output json 2>&1 | ConvertFrom-Json
    $jqObj = $jqOut.jobQueues | Where-Object { $_.jobQueueName -eq $script:VideoQueueName } | Select-Object -First 1
    if ($jqObj -and $jqObj.state -eq "DISABLED") {
        Write-Host "  Enabling Video Queue $script:VideoQueueName" -ForegroundColor Yellow
        aws batch update-job-queue --job-queue $script:VideoQueueName --state ENABLED --region $script:Region 2>&1 | Out-Null
    }
    Write-Ok "Video CE/Queue state check done"

    # 5) Netprobe
    Write-Step "Netprobe"
    . (Join-Path $ScriptRoot "netprobe\batch.ps1") -Region $script:Region -JobQueueName $script:OpsQueueName -JobDefName $script:OpsNetprobeJobDef

    # 6) Evidence
    Show-Evidence -Region $script:Region
}
finally {
    $env:ALLOW_LEGACY_IMPORT = ""
}

Write-Host "`n=== ONE-TAKE DEPLOY COMPLETE ===" -ForegroundColor Green
