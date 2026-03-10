# ==============================================================================
# API 컨테이너 재시작 (Redis 연결 갱신 등)
# AWS 프로필: 반드시 default. (-AwsProfile default)
# 주의: 이 스크립트는 SSM을 다시 읽지 않음. /opt/api.env 는 부팅 시점 그대로.
# env(VIDEO_BATCH_*, REDIS_HOST 등) 갱신이 필요하면 refresh-api-env.ps1 사용.
# 사용: pwsh scripts/v1/restart-api.ps1 -AwsProfile default
# ==============================================================================
param([string]$AwsProfile = "default")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\env.ps1")
if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
}

. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")
. (Join-Path $PSScriptRoot "resources\api.ps1")
$null = Load-SSOT -Env "prod"

$ids = @(Get-APIASGInstanceIds)
if (-not $ids -or $ids.Count -eq 0) {
    Write-Host "API ASG 인스턴스 없음." -ForegroundColor Yellow
    exit 1
}

$restartScript = 'docker restart academy-api'
$params = @{ commands = @($restartScript) }
$paramsJson = $params | ConvertTo-Json -Compress

Write-Host "API 인스턴스 $($ids -join ', ') 컨테이너 재시작 중..." -ForegroundColor Cyan

foreach ($instId in $ids) {
    try {
        $sendOut = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $instId, "--document-name", "AWS-RunShellScript", "--parameters", $paramsJson, "--region", $script:Region, "--output", "json") 2>$null
        $cmdId = $sendOut.Command.CommandId
        if (-not $cmdId) { Write-Host "  $instId : send-command failed" -ForegroundColor Red; continue }
        $wait = 0
        while ($wait -lt 60) {
            Start-Sleep -Seconds 3
            $wait += 3
            $inv = Invoke-AwsJson @("ssm", "get-command-invocation", "--command-id", $cmdId, "--instance-id", $instId, "--region", $script:Region, "--output", "json") 2>$null
            if ($inv.Status -eq "Success") {
                Write-Host "  $instId : OK" -ForegroundColor Green
                if ($inv.StandardOutputContent) { Write-Host "    $($inv.StandardOutputContent.Trim())" -ForegroundColor Gray }
                break
            }
            if ($inv.Status -eq "Failed" -or $inv.Status -eq "Cancelled") {
                Write-Host "  $instId : $($inv.Status)" -ForegroundColor Red
                if ($inv.StandardErrorContent) { Write-Host "    $($inv.StandardErrorContent)" -ForegroundColor Red }
                break
            }
        }
    } catch {
        Write-Host "  $instId : $_" -ForegroundColor Red
    }
}

Write-Host "`nhealthz 확인 후 프로그래스바 테스트 권장." -ForegroundColor Cyan
