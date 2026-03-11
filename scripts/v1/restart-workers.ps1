# ==============================================================================
# Messaging/AI 워커 ASG instance-refresh (UserData·IAM 반영용)
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

if ($UpdateSsm) {
    Write-Host "SSM /academy/workers/env 갱신 중..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "update-workers-env-sqs.ps1") -AwsProfile $AwsProfile
    if ($LASTEXITCODE -ne 0) {
        Write-Host "update-workers-env-sqs 실패." -ForegroundColor Red
        exit 1
    }
}

$asgs = @($script:MessagingASGName, $script:AiASGName)
foreach ($asgName in $asgs) {
    if (-not $asgName) { continue }
    Write-Host "`nASG $asgName instance-refresh 시작..." -ForegroundColor Cyan
    try {
        $out = Invoke-AwsJson @("autoscaling", "start-instance-refresh",
            "--auto-scaling-group-name", $asgName,
            "--region", $script:Region,
            "--output", "json") 2>&1
        if ($out -and $out.InstanceRefreshId) {
            Write-Host "  InstanceRefreshId: $($out.InstanceRefreshId)" -ForegroundColor Green
        } else {
            Write-Host "  $out" -ForegroundColor Yellow
        }
    } catch {
        if ($_.Exception.Message -match "InstanceRefreshInProgress") {
            Write-Host "  이미 instance-refresh 진행 중. 완료까지 10~15분 소요 가능 (scale-in protection)." -ForegroundColor Yellow
        } else {
            Write-Host "  $_" -ForegroundColor Red
        }
    }
}

Write-Host "`n완료 후 확인:" -ForegroundColor Cyan
Write-Host "  1. AWS Console > EC2 > Auto Scaling Groups > 해당 ASG > Instance refresh" -ForegroundColor Gray
Write-Host "  2. 새 인스턴스의 UserData 로그: /var/log/academy-worker-userdata.log" -ForegroundColor Gray
Write-Host "  3. SQS 대기 메시지: academy-v1-messaging-queue / academy-v1-ai-queue" -ForegroundColor Gray
