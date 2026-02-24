# ==============================================================================
# SSM dump/verify for /academy/workers/env: write Value to UTF-8 file, validate JSON, print key list.
# Avoids printing Value to console (Windows cp949 Unicode issue). Exit non-zero if invalid or required keys missing.
# Usage: .\scripts\infra\ssm_dump_video_worker_env.ps1 -Region ap-northeast-2 [-OutFile .env.ssm.verify]
# ==============================================================================

try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [string]$OutFile = "",
    [string]$ParamName = "/academy/workers/env"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)

# Must match scripts/infra/ssm_bootstrap_video_worker.ps1 and runbook
$RequiredKeys = @(
    "AWS_DEFAULT_REGION",
    "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT",
    "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_ENDPOINT", "R2_VIDEO_BUCKET",
    "API_BASE_URL", "INTERNAL_WORKER_TOKEN",
    "REDIS_HOST", "REDIS_PORT"
)

if (-not $OutFile) {
    $OutFile = Join-Path $RepoRoot ".env.ssm.verify"
}

$raw = $null
$exitCode = 0
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $raw = aws ssm get-parameter --name $ParamName --region $Region --with-decryption --query "Parameter.Value" --output text 2>&1
    $exitCode = $LASTEXITCODE
} finally { $ErrorActionPreference = $prevErr }

if ($exitCode -ne 0) {
    Write-Host "FAIL: SSM get-parameter failed (exit $exitCode)." -ForegroundColor Red
    exit 1
}
if (-not $raw) {
    Write-Host "FAIL: SSM parameter value empty." -ForegroundColor Red
    exit 1
}

# Ensure we have a single string (avoid ErrorRecord from stderr)
if ($raw -is [System.Management.Automation.ErrorRecord]) {
    Write-Host "FAIL: SSM returned error." -ForegroundColor Red
    exit 1
}
if ($raw -is [object[]]) {
    $raw = ($raw | Where-Object { $_ -is [string] } | Select-Object -First 1)
}
if (-not ($raw -is [string]) -or [string]::IsNullOrWhiteSpace($raw)) {
    Write-Host "FAIL: SSM parameter value empty or not a string." -ForegroundColor Red
    exit 1
}

# Write to UTF-8 file (no console print of Value)
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($OutFile, $raw.Trim(), $utf8NoBom)
Write-Host "Written to $OutFile (UTF-8, value not printed to console)." -ForegroundColor Cyan

# Parse JSON
try {
    $obj = $raw | ConvertFrom-Json
} catch {
    Write-Host "FAIL: JSON parse error: $_" -ForegroundColor Red
    exit 1
}

if (-not $obj -or $obj -isnot [PSCustomObject]) {
    Write-Host "FAIL: Parameter value is not a JSON object." -ForegroundColor Red
    exit 1
}

$keys = @($obj.PSObject.Properties | ForEach-Object { $_.Name })
Write-Host "Keys ($($keys.Count)): $($keys -join ', ')" -ForegroundColor Gray

$missing = @()
foreach ($k in $RequiredKeys) {
    $v = $obj.PSObject.Properties[$k]
    if (-not $v -or [string]::IsNullOrWhiteSpace($v.Value)) {
        $missing += $k
    }
}

if ($missing.Count -gt 0) {
    Write-Host "FAIL: Required keys missing or empty: $($missing -join ', ')." -ForegroundColor Red
    exit 1
}

Write-Host "OK: JSON valid, all required keys present." -ForegroundColor Green
exit 0
