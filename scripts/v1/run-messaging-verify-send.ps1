# ==============================================================================
# API 인스턴스에서 메시징 검증: 공용 owner 알림톡 1건 발송 + NotificationLog/provider 결과 확인
# ==============================================================================
# 사용: pwsh scripts/v1/run-messaging-verify-send.ps1 [-AwsProfile default]
# 배포 완료 후 실행. 통제번호 01031217466으로만 검증 알림톡이 발송됨.
# ==============================================================================
param([string]$AwsProfile = "")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\env.ps1")
if ($AwsProfile) { $env:AWS_PROFILE = $AwsProfile; if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" } }

. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")
. (Join-Path $PSScriptRoot "resources\api.ps1")
$null = Load-SSOT -Env "prod"

$ids = @(Get-APIASGInstanceIds)
if (-not $ids -or $ids.Count -eq 0) {
    Write-Host "API ASG 인스턴스 없음." -ForegroundColor Yellow
    exit 1
}

$region = $script:Region
$script = "docker exec academy-api python manage.py messaging_verify_common_alimtalk --source-tenant=3 --phone=01031217466 --trigger=password_reset_student --wait-seconds=120 2>&1"
$params = @{ commands = @($script) }
$paramsJson = $params | ConvertTo-Json -Compress

Write-Host "API 인스턴스에서 공용 알림톡 검증 실행 (01031217466 only + provider log 확인)..." -ForegroundColor Cyan
foreach ($instId in $ids) {
    try {
        $sendOut = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $instId, "--document-name", "AWS-RunShellScript", "--parameters", $paramsJson, "--region", $region, "--output", "json") 2>$null
        $cmdId = $sendOut.Command.CommandId
        if (-not $cmdId) { Write-Host "  $instId : send-command failed" -ForegroundColor Red; continue }
        $wait = 0
        while ($wait -lt 180) {
            Start-Sleep -Seconds 4
            $wait += 4
            $inv = Invoke-AwsJson @("ssm", "get-command-invocation", "--command-id", $cmdId, "--instance-id", $instId, "--region", $region, "--output", "json") 2>$null
            if ($inv.Status -eq "Success") {
                Write-Host "  $instId : OK" -ForegroundColor Green
                if ($inv.StandardOutputContent) { Write-Host $inv.StandardOutputContent -ForegroundColor Gray }
                if ($inv.StandardErrorContent) { Write-Host $inv.StandardErrorContent -ForegroundColor Yellow }
                break
            }
            if ($inv.Status -eq "Failed" -or $inv.Status -eq "Cancelled") {
                Write-Host "  $instId : $($inv.Status)" -ForegroundColor Red
                if ($inv.StandardOutputContent) { Write-Host $inv.StandardOutputContent -ForegroundColor Gray }
                if ($inv.StandardErrorContent) { Write-Host $inv.StandardErrorContent -ForegroundColor Red }
                break
            }
        }
    } catch {
        Write-Host "  $instId : $_" -ForegroundColor Red
    }
}
Write-Host "`n01031217466 수신 단말에서 알림톡 수신 여부 확인." -ForegroundColor Cyan
