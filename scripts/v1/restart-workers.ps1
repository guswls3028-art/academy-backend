# ==============================================================================
# Messaging/AI/Tools 워커 ASG instance-refresh (UserData·IAM 반영용)
# ==============================================================================
# SSM /academy/workers/env 갱신 후, 워커 인스턴스를 새 LT(UserData+IAM)로 롤링 교체.
# 사용: pwsh scripts/v1/restart-workers.ps1 [-AwsProfile default] [-UpdateSsm]
# -UpdateSsm: 먼저 update-workers-env-sqs.ps1 실행 후 instance-refresh
# ==============================================================================
param(
    [string]$AwsProfile = "",
    [switch]$UpdateSsm
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\env.ps1")
if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
}

. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")
$null = Load-SSOT -Env "prod"
. (Join-Path $PSScriptRoot "core\runtime-env-lock.ps1")
Enter-AcademyRuntimeEnvMutationLock `
    -Region $script:Region `
    -OwnerPrefix "restart-workers"

try {
$asgs = @(
    Assert-AcademyWorkerRefreshTargets -Names @(
        $script:MessagingASGName,
        $script:AiASGName,
        $script:ToolsASGName
    )
)

if ($UpdateSsm) {
    Write-Host "SSM /academy/workers/env 갱신 중..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "update-workers-env-sqs.ps1") -AwsProfile $AwsProfile
    if ($LASTEXITCODE -ne 0) {
        Write-Host "update-workers-env-sqs 실패." -ForegroundColor Red
        exit 1
    }
}

foreach ($asgName in $asgs) {
    Write-Host "`nASG $asgName instance-refresh 시작..." -ForegroundColor Cyan
    $refreshId = Start-AcademyInstanceRefresh `
        -AutoScalingGroupName $asgName `
        -Region $script:Region
    Write-Host "  InstanceRefreshId: $refreshId" -ForegroundColor Green
    Wait-AcademyInstanceRefresh `
        -AutoScalingGroupName $asgName `
        -InstanceRefreshId $refreshId `
        -Region $script:Region
    Write-Host "  terminal status: Successful" -ForegroundColor Green
}
Complete-AcademyRuntimeRefreshBoundary -Region $script:Region

Write-Host "`n완료 후 확인:" -ForegroundColor Cyan
Write-Host "  1. AWS Console > EC2 > Auto Scaling Groups > 해당 ASG > Instance refresh" -ForegroundColor Gray
Write-Host "  2. 새 인스턴스의 UserData 로그: /var/log/academy-worker-userdata.log" -ForegroundColor Gray
Write-Host "  3. SQS 대기 메시지: academy-v1-messaging-queue / academy-v1-ai-queue / academy-v1-tools-queue" -ForegroundColor Gray
} finally {
    Exit-AcademyRuntimeEnvMutationLock -Region $script:Region
}
