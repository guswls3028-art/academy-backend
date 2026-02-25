# ==============================================================================
# Verify SSM /academy/workers/env: exists, valid JSON, required keys present and non-empty.
# Fetches value with --with-decryption; does NOT print secret values.
# JSON 파싱 로직: ssm_bootstrap_video_worker.ps1 저장 직후 검증과 동일 (전체 응답 문자열 -> ConvertFrom-Json -> .Parameter.Value -> ConvertFrom-Json).
# Usage: .\scripts\infra\verify_ssm_env_shape.ps1 -Region ap-northeast-2
# Exit: 0 = PASS, 1 = FAIL
# ==============================================================================

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [string]$ParamName = "/academy/workers/env"
)

try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ErrorActionPreference = "Stop"

# Must match ssm_bootstrap_video_worker.ps1 and batch_entrypoint.py REQUIRED_KEYS
$RequiredKeys = @(
    "AWS_DEFAULT_REGION",
    "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT",
    "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_ENDPOINT", "R2_VIDEO_BUCKET",
    "API_BASE_URL", "INTERNAL_WORKER_TOKEN",
    "REDIS_HOST", "REDIS_PORT",
    "DJANGO_SETTINGS_MODULE"
)

# --- SSM 응답 파싱 (ssm_bootstrap의 get-parameter 검증과 동일) ---
$prev = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$raw = aws ssm get-parameter --name $ParamName --region $Region --with-decryption --output json 2>&1
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $prev

if ($exitCode -ne 0) {
    Write-Host "FAIL: SSM get-parameter failed (exit $exitCode). Parameter may not exist or no decrypt permission." -ForegroundColor Red
    exit 1
}

# 전체 응답을 하나의 문자열로 합친 뒤 JSON 파싱 (멀티라인/인코딩 안전)
$responseStr = ($raw | Out-String).Trim()
$outer = $null
try {
    $outer = $responseStr | ConvertFrom-Json
} catch {
    Write-Host "FAIL: SSM response is not valid JSON." -ForegroundColor Red
    exit 1
}

if (-not $outer -or -not $outer.Parameter -or $null -eq $outer.Parameter.Value) {
    Write-Host "FAIL: SSM parameter value missing or empty." -ForegroundColor Red
    exit 1
}

# 저장된 값(문자열)을 다시 JSON으로 파싱
$valueStr = $outer.Parameter.Value
if (-not ($valueStr -is [string]) -or [string]::IsNullOrWhiteSpace($valueStr)) {
    Write-Host "FAIL: SSM parameter value is not a non-empty string." -ForegroundColor Red
    exit 1
}

$payload = $null
try {
    $payload = $valueStr | ConvertFrom-Json
} catch {
    Write-Host "FAIL: SSM parameter value is not valid JSON." -ForegroundColor Red
    exit 1
}

if (-not $payload -or $payload -isnot [System.Management.Automation.PSCustomObject]) {
    $payload = $null
}
if (-not $payload) {
    Write-Host "FAIL: SSM parameter value is not a JSON object." -ForegroundColor Red
    exit 1
}

$missing = @()
$empty = @()
foreach ($k in $RequiredKeys) {
    $v = $payload.PSObject.Properties[$k]
    if ($null -eq $v) {
        $missing += $k
    } else {
        $val = $v.Value
        if ($null -eq $val -or ([string]$val).Trim() -eq '') {
            $empty += $k
        }
    }
}

if ($missing.Count -gt 0) {
    Write-Host "FAIL: SSM JSON missing required keys: $($missing -join ', ')" -ForegroundColor Red
    exit 1
}
if ($empty.Count -gt 0) {
    Write-Host "FAIL: SSM JSON has empty required keys: $($empty -join ', ')" -ForegroundColor Red
    exit 1
}

$dsm = ($payload.PSObject.Properties["DJANGO_SETTINGS_MODULE"].Value -as [string]).Trim()
if ($dsm -ne "apps.api.config.settings.worker") {
    Write-Host "FAIL: DJANGO_SETTINGS_MODULE must be 'apps.api.config.settings.worker' (got '$dsm')." -ForegroundColor Red
    exit 1
}

Write-Host "OK: SSM parameter exists, valid JSON, all required keys present and non-empty, DJANGO_SETTINGS_MODULE=worker." -ForegroundColor Green
exit 0
