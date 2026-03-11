# ==============================================================================
# API 인스턴스에 최신 SSM env 적용 후 컨테이너 재시작
# AWS 프로필: 반드시 default. (-AwsProfile default)
# 사용: pwsh scripts/v1/refresh-api-env.ps1 -AwsProfile default
# ==============================================================================
param([string]$AwsProfile = "default")

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

$ssmParam = $script:SsmApiEnv
$region = $script:Region
$inlinePath = Join-Path $PSScriptRoot "inline\refresh-api-env.sh"
if (-not (Test-Path $inlinePath)) {
    Write-Host "Inline script not found: $inlinePath" -ForegroundColor Red
    exit 1
}
# base64로 인코딩 후 원격에서 디코딩 실행 (escaping 이슈 회피)
$scriptContent = [System.IO.File]::ReadAllText($inlinePath) -replace "`r`n", "`n" -replace "`r", "`n"
$scriptContent = $scriptContent -replace 'SSM_PARAM="\$\{1:-/academy/api/env\}"', "SSM_PARAM=`"$ssmParam`"" -replace 'REGION="\$\{2:-ap-northeast-2\}"', "REGION=`"$region`""
$scriptB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($scriptContent))
$script = "echo $scriptB64 | base64 -d | bash"

$params = @{ commands = @($script) }
$paramsJson = $params | ConvertTo-Json -Compress

Write-Host "API 인스턴스 $($ids -join ', ') 에 SSM env 적용 및 컨테이너 재시작 중..." -ForegroundColor Cyan
Write-Host "SSM param: $ssmParam" -ForegroundColor Gray

foreach ($instId in $ids) {
    try {
        $sendOut = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $instId, "--document-name", "AWS-RunShellScript", "--parameters", $paramsJson, "--region", $region, "--output", "json") 2>$null
        $cmdId = $sendOut.Command.CommandId
        if (-not $cmdId) { Write-Host "  $instId : send-command failed" -ForegroundColor Red; continue }
        $wait = 0
        while ($wait -lt 60) {
            Start-Sleep -Seconds 3
            $wait += 3
            $inv = Invoke-AwsJson @("ssm", "get-command-invocation", "--command-id", $cmdId, "--instance-id", $instId, "--region", $region, "--output", "json") 2>$null
            if ($inv.Status -eq "Success") {
                Write-Host "  $instId : OK" -ForegroundColor Green
                if ($inv.StandardOutputContent) { Write-Host $inv.StandardOutputContent -ForegroundColor Gray }
                break
            }
            if ($inv.Status -eq "Failed" -or $inv.Status -eq "Cancelled") {
                Write-Host "  $instId : $($inv.Status)" -ForegroundColor Red
                if ($inv.StandardErrorContent) { Write-Host $inv.StandardErrorContent -ForegroundColor Red }
                break
            }
        }
    } catch {
        Write-Host "  $instId : $_" -ForegroundColor Red
    }
}

Write-Host "`nhealthz 확인 후 업로드 테스트 진행 권장." -ForegroundColor Cyan
