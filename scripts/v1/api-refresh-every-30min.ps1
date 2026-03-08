# 30분 간격으로 API 서버만 정식 풀배포(instance refresh) 실행.
# Usage: pwsh scripts/v1/api-refresh-every-30min.ps1 [-AwsProfile default] [-RunOnce]
# -RunOnce: 1회만 실행 후 종료 (테스트 또는 수동 1회용).
# 없으면 30분마다 무한 반복. 중지하려면 Ctrl+C.
# 실행 시 .env 기반 인증 필요: pwsh -File scripts/v1/run-with-env.ps1 -- pwsh scripts/v1/api-refresh-every-30min.ps1
param(
    [string]$AwsProfile,
    [switch]$RunOnce
)

$ErrorActionPreference = "Stop"

$ScriptRoot = $PSScriptRoot
$RefreshScript = Join-Path $ScriptRoot "api-refresh-only.ps1"
$IntervalSec = 30 * 60  # 30 min

if (-not (Test-Path $RefreshScript)) {
    Write-Error "api-refresh-only.ps1 not found: $RefreshScript"
    exit 1
}

Write-Host "API refresh every 30 min (정식 풀배포 API만). RunOnce=$RunOnce" -ForegroundColor Cyan
$count = 0
do {
    $count++
    Write-Host "`n--- Run #$count at $(Get-Date -Format 'o') ---" -ForegroundColor Cyan
    try {
        & $RefreshScript -AwsProfile $AwsProfile
    } catch {
        Write-Host "Refresh failed: $_" -ForegroundColor Red
    }
    if ($RunOnce) { break }
    Write-Host "Next refresh in 30 minutes..." -ForegroundColor Gray
    Start-Sleep -Seconds $IntervalSec
} while ($true)

Write-Host "Done." -ForegroundColor Green
