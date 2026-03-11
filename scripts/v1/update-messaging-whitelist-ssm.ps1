# ==============================================================================
# SSM /academy/api/env, /academy/workers/env 에 MESSAGING_TEST_RECIPIENT_WHITELIST 제거 (빈 문자열 설정)
# ==============================================================================
# 테스트용 화이트리스트를 비활성화(빈 값)하여 모든 수신번호로 발송 가능하게 함.
# 사용: pwsh scripts/v1/clear-messaging-whitelist-ssm.ps1 [-AwsProfile default]
# ==============================================================================
param([string]$AwsProfile = "")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\env.ps1")
if ($AwsProfile) { $env:AWS_PROFILE = $AwsProfile; if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" } }

. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")
$null = Load-SSOT -Env "prod"

$whitelistValue = ""

function Set-WhitelistInParam {
    param([string]$ParamName, [string]$Description)
    $existing = $null
    try {
        $existing = Invoke-AwsJson @("ssm", "get-parameter", "--name", $ParamName, "--with-decryption", "--region", $script:Region, "--output", "json")
    } catch {
        Write-Host "$Description not found or no access." -ForegroundColor Red
        return $false
    }
    $valueRaw = $existing.Parameter.Value
    $jsonStr = $valueRaw
    if ($valueRaw -match '^[A-Za-z0-9+/]+=*$') {
        try {
            $jsonStr = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($valueRaw))
        } catch { }
    }
    $obj = $jsonStr | ConvertFrom-Json
    $obj | Add-Member -NotePropertyName "MESSAGING_TEST_RECIPIENT_WHITELIST" -NotePropertyValue $whitelistValue -Force

    $newJson = $obj | ConvertTo-Json -Compress -Depth 10
    $newValue = $newJson
    if ($valueRaw -match '^[A-Za-z0-9+/]+=*$') {
        $newBytes = [System.Text.Encoding]::UTF8.GetBytes($newJson)
        $newValue = [Convert]::ToBase64String($newBytes)
    }
    Invoke-Aws @("ssm", "put-parameter", "--name", $ParamName, "--type", "SecureString", "--value", $newValue, "--overwrite", "--region", $script:Region) -ErrorMessage "put-parameter $ParamName" | Out-Null
    Write-Host "  $ParamName -> MESSAGING_TEST_RECIPIENT_WHITELIST cleared" -ForegroundColor Green
    return $true
}

Write-Host "Clearing MESSAGING_TEST_RECIPIENT_WHITELIST in SSM (production messaging)..." -ForegroundColor Cyan
$apiOk = Set-WhitelistInParam -ParamName $script:SsmApiEnv -Description "SSM API env"
$workersOk = Set-WhitelistInParam -ParamName $script:SsmWorkersEnv -Description "SSM Workers env"
if ($apiOk -or $workersOk) {
    Write-Host "`nAPI 인스턴스: refresh-api-env.ps1 실행 또는 instance-refresh 후 적용." -ForegroundColor Cyan
    Write-Host "워커 인스턴스: instance-refresh 후 적용." -ForegroundColor Cyan
} else {
    Write-Host "No parameter updated." -ForegroundColor Yellow
}
