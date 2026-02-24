# ==============================================================================
# Verify SSM /academy/workers/env: fetch, validate JSON, required keys exist. No value printing (cp949-safe).
# Usage: .\scripts\infra\verify_ssm_env_shape.ps1 -Region ap-northeast-2
# ==============================================================================

try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [string]$ParamName = "/academy/workers/env"
)

$ErrorActionPreference = "Stop"
$RequiredKeys = @(
    "AWS_DEFAULT_REGION", "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT",
    "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_ENDPOINT", "R2_VIDEO_BUCKET",
    "API_BASE_URL", "INTERNAL_WORKER_TOKEN", "REDIS_HOST", "REDIS_PORT"
)

$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$raw = aws ssm get-parameter --name $ParamName --region $Region --with-decryption --query "Parameter.Value" --output text 2>&1
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $prevErr

if ($exitCode -ne 0) {
    Write-Host "FAIL: SSM get-parameter failed (exit $exitCode)." -ForegroundColor Red
    exit 1
}
if (-not $raw -or ($raw -is [System.Management.Automation.ErrorRecord])) {
    Write-Host "FAIL: SSM parameter empty or error." -ForegroundColor Red
    exit 1
}
if ($raw -is [object[]]) { $raw = ($raw | Where-Object { $_ -is [string] } | Select-Object -First 1) }
try {
    $obj = $raw | ConvertFrom-Json
} catch {
    Write-Host "FAIL: JSON parse error." -ForegroundColor Red
    exit 1
}
$missing = @()
foreach ($k in $RequiredKeys) {
    $v = $obj.PSObject.Properties[$k]
    if (-not $v -or [string]::IsNullOrWhiteSpace($v.Value)) { $missing += $k }
}
if ($missing.Count -gt 0) {
    Write-Host "FAIL: Required keys missing or empty: $($missing -join ', ')." -ForegroundColor Red
    exit 1
}
Write-Host "OK: SSM parameter JSON valid, all required keys present (values not printed)." -ForegroundColor Green
