# ==============================================================================
# SSM Parameter bootstrap for video worker: .env (or -EnvFile) → /academy/workers/env.
# Full source-of-truth mode: .env is canonical; SSM is derived. No manual SSM editing.
# Usage:
#   .\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region ap-northeast-2
#   .\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region ap-northeast-2 -EnvFile .env -Overwrite
#   .\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region ap-northeast-2 -Interactive
# ==============================================================================

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [string]$EnvFile = ".env",
    [switch]$Interactive,
    [switch]$Overwrite
)

# Windows cp949: prefer UTF-8 for SSM value handling
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$ErrorActionPreference = "Stop"
$ParamName = "/academy/workers/env"

# Required keys for SSM payload (Batch worker + Video ops jobs)
$RequiredKeys = @(
    "AWS_DEFAULT_REGION",
    "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT",
    "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_ENDPOINT", "R2_VIDEO_BUCKET",
    "API_BASE_URL", "INTERNAL_WORKER_TOKEN",
    "REDIS_HOST", "REDIS_PORT"
)
$OptionalKeys = @("REDIS_PASSWORD", "R2_PUBLIC_BASE_URL", "R2_PREFIX", "VIDEO_BATCH_JOB_QUEUE", "VIDEO_BATCH_JOB_DEFINITION")

function Parse-EnvFile {
    param([string]$Path)
    $hash = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $hash }
    $content = [System.IO.File]::ReadAllText($Path, [System.Text.UTF8Encoding]::new($false))
    if ($content.Length -ge 1 -and $content[0] -eq [char]0xFEFF) { $content = $content.Substring(1) }
    foreach ($line in ($content -split "`r?`n")) {
        $line = $line.Trim()
        if ($line -match '^\s*#' -or $line -eq '') { continue }
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $key = $matches[1]
            $val = $matches[2].Trim()
            if ($val -match '^["''](.*)["'']$') { $val = $matches[1] }
            $hash[$key] = $val
        }
    }
    return $hash
}

function Get-ValueOrPrompt {
    param([hashtable]$Hash, [string]$Key, [string]$Prompt, [bool]$Interactive)
    $v = $Hash[$Key]
    if ($null -ne $v) {
        if ($v -is [string]) { $vStr = $v.Trim() } else { $vStr = [string]$v }
        if ($vStr -ne '') { return $vStr }
    }
    if ($Interactive -and $Prompt) {
        $secure = $Key -match 'PASSWORD|SECRET|TOKEN'
        if ($secure) {
            $sec = Read-Host -Prompt $Prompt -AsSecureString
            $BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
            try { return [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR) } finally { [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR) }
        }
        return Read-Host -Prompt $Prompt
    }
    return $null
}

# Resolve EnvFile path (repo root if relative)
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$EnvPath = $EnvFile
if (-not [System.IO.Path]::IsPathRooted($EnvPath)) {
    $EnvPath = Join-Path $RepoRoot $EnvPath
}

$envHash = Parse-EnvFile -Path $EnvPath
$missing = @()
$collected = @{}

foreach ($k in $RequiredKeys) {
    $prompt = "Enter $k"
    if ($k -eq "DB_PORT") {
        if ($envHash["DB_PORT"]) { $collected[$k] = $envHash["DB_PORT"] } else { $collected[$k] = "5432" }
        continue
    }
    if ($k -eq "REDIS_PORT") {
        if ($envHash["REDIS_PORT"]) { $collected[$k] = $envHash["REDIS_PORT"] } else { $collected[$k] = "6379" }
        continue
    }
    # AWS_DEFAULT_REGION: accept AWS_REGION from .env as fallback
    if ($k -eq "AWS_DEFAULT_REGION") {
        $v = $envHash["AWS_DEFAULT_REGION"]
        if ($null -eq $v -or (($v -is [string]) -and $v.Trim() -eq '')) { $v = $envHash["AWS_REGION"] }
        if ($null -ne $v -and ($v -is [string])) { $vStr = $v.Trim() } elseif ($null -ne $v) { $vStr = [string]$v } else { $vStr = "" }
        if ($vStr -ne '') { $collected[$k] = $vStr } else { $missing += $k }
        continue
    }
    $v = Get-ValueOrPrompt -Hash $envHash -Key $k -Prompt $prompt -Interactive ($Interactive -or -not (Test-Path -LiteralPath $EnvPath))
    if ($null -ne $v -and $v -is [string]) { $vStr = $v.Trim() } elseif ($null -ne $v) { $vStr = [string]$v } else { $vStr = "" }
    if ($null -eq $v -or $vStr -eq '') {
        $missing += $k
    } else {
        $collected[$k] = $vStr
    }
}

if ($missing.Count -gt 0) {
    Write-Host "FAIL: Required variables missing (no silent fallback): $($missing -join ', ')" -ForegroundColor Red
    Write-Host "  File: $EnvPath" -ForegroundColor Red
    Write-Host "  Missing keys: $($missing -join ', ')" -ForegroundColor Red
    Write-Host "  Set values in .env or run with -Interactive to prompt." -ForegroundColor Red
    exit 1
}

# AWS_DEFAULT_REGION must match -Region (hard fail)
$envRegionRaw = $collected["AWS_DEFAULT_REGION"]
if (-not $envRegionRaw) { $envRegionRaw = $envHash["AWS_DEFAULT_REGION"] }
if (-not $envRegionRaw) { $envRegionRaw = $envHash["AWS_REGION"] }
if (-not $envRegionRaw) { $envRegionRaw = "" }
if ($envRegionRaw -is [string]) { $envRegion = $envRegionRaw.Trim() } else { $envRegion = [string]$envRegionRaw }
if ([string]::IsNullOrWhiteSpace($envRegion)) {
    Write-Host "FAIL: AWS_DEFAULT_REGION is missing in $EnvPath. Add AWS_DEFAULT_REGION=ap-northeast-2 (or -Region value)." -ForegroundColor Red
    exit 1
}
if ($envRegion -ne $Region) {
    Write-Host "FAIL: AWS_DEFAULT_REGION mismatch. File $EnvPath has '$envRegion', -Region is '$Region'." -ForegroundColor Red
    exit 1
}
if (-not $collected["AWS_DEFAULT_REGION"]) { $collected["AWS_DEFAULT_REGION"] = $Region }

# Optional keys (merge from env file)
foreach ($k in $OptionalKeys) {
    $optVal = $envHash[$k]
    if ($null -ne $optVal -and ($optVal -is [string]) -and $optVal.Trim() -ne '') {
        $collected[$k] = $optVal.Trim()
    }
}
if ($envHash["R2_VIDEO_BUCKET"] -and ($envHash["R2_VIDEO_BUCKET"] -is [string]) -and $envHash["R2_VIDEO_BUCKET"].Trim() -ne '') {
    $collected["R2_VIDEO_BUCKET"] = $envHash["R2_VIDEO_BUCKET"].Trim()
}
# Batch/ops jobs always use worker settings (no debug_toolbar, minimal INSTALLED_APPS).
$collected["DJANGO_SETTINGS_MODULE"] = "apps.api.config.settings.worker"
$apiVal = $collected["API_BASE_URL"]
if ($null -ne $apiVal -and ($apiVal -is [string])) { $collected["API_BASE_URL"] = $apiVal.TrimEnd('/') } else { $collected["API_BASE_URL"] = [string]$apiVal }

# Parameter exists and no -Overwrite
$exists = $false
try {
    $null = aws ssm get-parameter --name $ParamName --region $Region 2>&1
    if ($LASTEXITCODE -eq 0) { $exists = $true }
} catch {}
if ($exists -and -not $Overwrite) {
    Write-Host "FAIL: Parameter $ParamName already exists. Use -Overwrite to update. No manual SSM editing allowed." -ForegroundColor Red
    exit 1
}

# --- JSON 직렬화: PowerShell 객체 -> 한 줄 JSON, UTF-8 no BOM, file 기반으로 quoting 제거 ---
# 정렬된 키 순서: 필수 + 선택 + DJANGO_SETTINGS_MODULE (이미 collected에 있음)
$allOrderedKeys = @($RequiredKeys) + @($OptionalKeys) + "DJANGO_SETTINGS_MODULE"
$obj = [ordered]@{}
foreach ($k in $allOrderedKeys) {
    if ($collected.ContainsKey($k)) { $obj[$k] = $collected[$k] }
}
foreach ($k in $collected.Keys) {
    if (-not $obj.Contains($k)) { $obj[$k] = $collected[$k] }
}
if (-not $obj["DJANGO_SETTINGS_MODULE"]) {
    $obj["DJANGO_SETTINGS_MODULE"] = "apps.api.config.settings.worker"
}

$json = $obj | ConvertTo-Json -Compress -Depth 10

# UTF-8 no BOM으로 temp 파일에 기록 (키는 반드시 double-quote, 한 줄 JSON)
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$tempJsonPath = Join-Path ([System.IO.Path]::GetTempPath()) "academy_ssm_env_$([Guid]::NewGuid().ToString('N')).json"
try {
    [System.IO.File]::WriteAllText($tempJsonPath, $json, $utf8NoBom)
} catch {
    Write-Host "FAIL: Could not write temp JSON file: $_" -ForegroundColor Red
    exit 1
}

# 직렬화 검증: 생성한 JSON을 ConvertFrom-Json으로 파싱 가능한지 + DJANGO_SETTINGS_MODULE 확인
try {
    $roundTrip = $json | ConvertFrom-Json
    if (-not $roundTrip.PSObject.Properties["DJANGO_SETTINGS_MODULE"]) {
        Write-Host "FAIL: JSON round-trip missing DJANGO_SETTINGS_MODULE." -ForegroundColor Red
        Remove-Item -LiteralPath $tempJsonPath -Force -ErrorAction SilentlyContinue
        exit 1
    }
    $dsm = ($roundTrip.PSObject.Properties["DJANGO_SETTINGS_MODULE"].Value -as [string]).Trim()
    if ($dsm -ne "apps.api.config.settings.worker") {
        Write-Host "FAIL: DJANGO_SETTINGS_MODULE must be 'apps.api.config.settings.worker' (got '$dsm')." -ForegroundColor Red
        Remove-Item -LiteralPath $tempJsonPath -Force -ErrorAction SilentlyContinue
        exit 1
    }
} catch {
    Write-Host "FAIL: Generated JSON is not valid (ConvertFrom-Json failed): $_" -ForegroundColor Red
    Remove-Item -LiteralPath $tempJsonPath -Force -ErrorAction SilentlyContinue
    exit 1
}

# put-parameter: --cli-input-json file:// 사용해 값을 파일로 전달 (인자 이스케이프/길이 제한 회피)
$jsonBytes = [System.Text.Encoding]::UTF8.GetBytes($json)
$valueBase64 = [Convert]::ToBase64String($jsonBytes)
Remove-Item -LiteralPath $tempJsonPath -Force -ErrorAction SilentlyContinue

$cliInput = @{
    Name      = $ParamName
    Value     = $valueBase64
    Type      = "SecureString"
    Overwrite = $true
} | ConvertTo-Json -Compress
$cliInputPath = Join-Path ([System.IO.Path]::GetTempPath()) "academy_ssm_put_$([Guid]::NewGuid().ToString('N')).json"
[System.IO.File]::WriteAllText($cliInputPath, $cliInput, $utf8NoBom)

$cliInputUri = "file://" + (([System.IO.Path]::GetFullPath($cliInputPath)) -replace '\\', '/')
Write-Host "Putting SSM parameter: $ParamName (SecureString, base64-encoded JSON)" -ForegroundColor Cyan
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$putOut = & aws ssm put-parameter --cli-input-json $cliInputUri --region $Region 2>&1
$putErr = ($putOut | Out-String).Trim()
$putExit = $LASTEXITCODE
$ErrorActionPreference = $prevErr
Remove-Item -LiteralPath $cliInputPath -Force -ErrorAction SilentlyContinue
if ($putExit -ne 0) {
    Write-Host "FAIL: put-parameter failed (exit $putExit)." -ForegroundColor Red
    if ($putErr) { Write-Host $putErr -ForegroundColor Red }
    exit 1
}

# 저장 직후 get-parameter --with-decryption 으로 읽어서 Base64 디코딩 후 JSON 검증 (실패 시 exit 1)
$getValueRaw = aws ssm get-parameter --name $ParamName --region $Region --with-decryption --output json 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: get-parameter (with-decryption) after put failed." -ForegroundColor Red
    exit 1
}
$getValueStr = ($getValueRaw | Out-String).Trim()
try {
    $outerResp = $getValueStr | ConvertFrom-Json
} catch {
    Write-Host "FAIL: get-parameter response is not valid JSON." -ForegroundColor Red
    exit 1
}
if (-not $outerResp -or -not $outerResp.Parameter -or $null -eq $outerResp.Parameter.Value) {
    Write-Host "FAIL: SSM parameter value missing or empty after put." -ForegroundColor Red
    exit 1
}
$storedValueStr = $outerResp.Parameter.Value
# 값이 Base64로 저장되어 있으므로 디코딩 후 JSON 파싱
try {
    $storedValueBytes = [Convert]::FromBase64String($storedValueStr)
    $storedJsonStr = [System.Text.Encoding]::UTF8.GetString($storedValueBytes)
} catch {
    Write-Host "FAIL: Stored SSM value is not valid base64." -ForegroundColor Red
    exit 1
}
try {
    $storedPayload = $storedJsonStr | ConvertFrom-Json
} catch {
    Write-Host "FAIL: Stored SSM value (after base64 decode) is not valid JSON." -ForegroundColor Red
    exit 1
}
if (-not $storedPayload -or $storedPayload -isnot [System.Management.Automation.PSCustomObject]) {
    Write-Host "FAIL: Stored SSM value is not a JSON object." -ForegroundColor Red
    exit 1
}
if (-not $storedPayload.PSObject.Properties["DJANGO_SETTINGS_MODULE"]) {
    Write-Host "FAIL: Stored value missing DJANGO_SETTINGS_MODULE." -ForegroundColor Red
    exit 1
}
$storedDsm = ($storedPayload.PSObject.Properties["DJANGO_SETTINGS_MODULE"].Value -as [string]).Trim()
if ($storedDsm -ne "apps.api.config.settings.worker") {
    Write-Host "FAIL: Stored DJANGO_SETTINGS_MODULE is not worker (got '$storedDsm')." -ForegroundColor Red
    exit 1
}

# Confirm (version만 출력, 값은 출력하지 않음)
$getMetaRaw = aws ssm get-parameter --name $ParamName --region $Region --query "Parameter.{Name:Name,Type:Type,Version:Version}" --output json 2>&1
try {
    $getOut = ($getMetaRaw | Out-String).Trim() | ConvertFrom-Json
} catch {}
if ($getOut -and $getOut.Name -eq $ParamName) {
    Write-Host "ParameterVersion: $($getOut.Version)" -ForegroundColor Cyan
}
Write-Host "OK: $ParamName written successfully. Stored value validated as valid JSON with DJANGO_SETTINGS_MODULE=worker." -ForegroundColor Green
