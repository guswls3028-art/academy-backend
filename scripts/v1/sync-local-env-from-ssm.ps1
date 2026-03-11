# ==============================================================================
# 로컬 .env에 배포 DB/Redis 반영 (SSM /academy/api/env → 루트 .env)
# ==============================================================================
# 요청: "배포 DB를 로컬에서 사용하게 해달라" → 이 스크립트 실행 시 로컬 manage.py/shell이
# 배포(프로덕션) RDS/Redis에 연결됩니다.
#
# 동작:
#   - SSM /academy/api/env (JSON)에서 DB_*, REDIS_* 키를 읽어
#   - academy 루트 .env 에 해당 키만 덮어쓰기 (나머지 키는 유지)
#
# 사용:
#   pwsh scripts/v1/sync-local-env-from-ssm.ps1
#   pwsh scripts/v1/sync-local-env-from-ssm.ps1 -AwsProfile default
#
# 주의: 배포 DB를 쓰면 실제 데이터가 변경됩니다. migrate, fix_qna_orphan 등 실행 시 유의.
# ==============================================================================
[CmdletBinding()]
param(
    [string]$AwsProfile = "",
    [switch]$WhatIf = $false
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$EnvPath = Join-Path $RepoRoot ".env"

. (Join-Path $PSScriptRoot "core\env.ps1")
. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")

if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
}

$null = Load-SSOT -Env "prod"
$paramName = $script:SsmApiEnv
if (-not $paramName) {
    Write-Host "SsmApiEnv not set in SSOT." -ForegroundColor Red
    exit 1
}

# SSM에서 API env 가져오기
try {
    $existing = Invoke-AwsJson @("ssm", "get-parameter", "--name", $paramName, "--with-decryption", "--region", $script:Region, "--output", "json")
} catch {
    Write-Host "SSM $paramName not found or no access. AWS 프로파일/권한 확인." -ForegroundColor Red
    exit 1
}

$valueRaw = $existing.Parameter.Value
$jsonStr = $valueRaw
if ($valueRaw -match '^[A-Za-z0-9+/]+=*$') {
    try {
        $jsonStr = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($valueRaw))
    } catch { }
}
$obj = $jsonStr | ConvertFrom-Json

# 로컬 .env에 반영할 키 (배포 DB/Redis = manage.py에서 사용)
$SyncKeys = @(
    "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT",
    "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB"
)

$fromSsm = @{}
foreach ($k in $SyncKeys) {
    $v = $null
    if ($obj.PSObject.Properties[$k]) { $v = $obj.PSObject.Properties[$k].Value }
    if ($null -ne $v -and ($v -is [string]) -and $v.Trim() -ne "") {
        $fromSsm[$k] = $v.Trim()
    } elseif ($null -ne $v) {
        $fromSsm[$k] = [string]$v
    }
}

if ($fromSsm.Count -eq 0) {
    Write-Host "SSM에 DB_* / REDIS_* 키가 없습니다. 배포 env 구성을 확인하세요." -ForegroundColor Yellow
    exit 1
}

Write-Host "로컬 .env에 반영할 키 (SSM → $EnvPath):" -ForegroundColor Cyan
foreach ($k in ($fromSsm.Keys | Sort-Object)) {
    $display = if ($k -eq "DB_PASSWORD" -or $k -eq "REDIS_PASSWORD") { "***" } else { $fromSsm[$k] }
    Write-Host "  $k=$display" -ForegroundColor Gray
}

if (-not (Test-Path $EnvPath)) {
    Write-Host "기존 .env 없음. 새로 생성합니다." -ForegroundColor Yellow
    $lines = [System.Collections.ArrayList]::new()
    foreach ($k in ($fromSsm.Keys | Sort-Object)) {
        $val = $fromSsm[$k]
        if ($val -match '[\s#]') { $val = "`"$val`"" }
        [void]$lines.Add("$k=$val")
    }
} else {
    $lines = [System.Collections.ArrayList]::new()
    $done = @{}
    foreach ($line in (Get-Content -Path $EnvPath -Encoding UTF8 -Raw).Split("`n")) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $key = $matches[1].Trim()
            if ($fromSsm.ContainsKey($key)) {
                $val = $fromSsm[$key]
                if ($val -match '[\s#]') { $val = "`"$val`"" }
                [void]$lines.Add("$key=$val")
                $done[$key] = $true
            } else {
                [void]$lines.Add($line)
            }
        } else {
            [void]$lines.Add($line)
        }
    }
    foreach ($k in $fromSsm.Keys) {
        if (-not $done[$k]) {
            $val = $fromSsm[$k]
            if ($val -match '[\s#]') { $val = "`"$val`"" }
            [void]$lines.Add("$k=$val")
        }
    }
}

if (-not $WhatIf) {
    $content = $lines -join "`n"
    if (-not $content.EndsWith("`n")) { $content += "`n" }
    [System.IO.File]::WriteAllText($EnvPath, $content, [System.Text.UTF8Encoding]::new($false))
    Write-Host "`nOK. .env 업데이트됨. 이제 manage.py/shell은 배포 DB/Redis를 사용합니다." -ForegroundColor Green
} else {
    Write-Host "`n[WhatIf] .env 변경 없음. -WhatIf 제거 후 다시 실행하면 반영됩니다." -ForegroundColor Yellow
}
