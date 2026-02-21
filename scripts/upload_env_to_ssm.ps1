# ==============================================================================
# .env -> SSM /academy/workers/env and /academy/api/env (SSOT: workers vs API)
# Usage: .\scripts\upload_env_to_ssm.ps1
# ==============================================================================

param(
    [string]$RepoRoot = (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) ".."),
    [string]$Region = "ap-northeast-2",
    [string]$ParameterName = "/academy/workers/env",
    [string]$ApiParameterName = "/academy/api/env"
)

$ErrorActionPreference = "Stop"
$envPath = Join-Path $RepoRoot ".env"
$envPath = [System.IO.Path]::GetFullPath($envPath)
if (-not (Test-Path -LiteralPath $envPath)) {
    $fallbackRoot = (Get-Location).Path
    $envPathFallback = [System.IO.Path]::GetFullPath((Join-Path $fallbackRoot ".env"))
    if (Test-Path -LiteralPath $envPathFallback) {
        $envPath = $envPathFallback
    }
}
if (-not (Test-Path -LiteralPath $envPath)) {
    Write-Host "upload_env_to_ssm: .env not found at: $envPath" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    Write-Host "upload_env_to_ssm: .env is a directory, not a file: $envPath" -ForegroundColor Red
    exit 1
}

# Read .env: try Get-Content (Windows-friendly) then fallback to ReadAllText
$content = $null
$lastErr = $null
try {
    $content = Get-Content -LiteralPath $envPath -Raw -Encoding UTF8 -ErrorAction Stop
} catch {
    $lastErr = $_
    try {
        $content = Get-Content -LiteralPath $envPath -Raw -ErrorAction Stop
    } catch {
        $lastErr = $_
    }
}
if ($null -eq $content) {
    try {
        $content = [System.IO.File]::ReadAllText($envPath, [System.Text.Encoding]::UTF8)
    } catch {
        $lastErr = $_
    }
}
if ($null -eq $content) {
    Write-Host "upload_env_to_ssm: could not read .env at: $envPath" -ForegroundColor Red
    if ($lastErr) { Write-Host "  Error: $($lastErr.Exception.Message)" -ForegroundColor Gray }
    exit 1
}
if ([string]::IsNullOrWhiteSpace($content)) {
    Write-Host "upload_env_to_ssm: .env is empty at: $envPath" -ForegroundColor Yellow
    exit 1
}
$content = $content -replace "`r`n", "`n" -replace "`r", "`n"

# Standard tier limit 4096 chars; use Advanced (up to 64KB) when larger
$SSM_STANDARD_MAX = 4096
$tier = if ($content.Length -gt $SSM_STANDARD_MAX) { "Advanced" } else { "Standard" }
if ($tier -eq "Advanced") {
    Write-Host "upload_env_to_ssm: .env size $($content.Length) chars > 4096, using SSM Advanced tier." -ForegroundColor Gray
}

$ea = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
aws ssm put-parameter --name $ParameterName --type SecureString --value "$content" --overwrite --tier $tier --region $Region
$ok1 = ($LASTEXITCODE -eq 0)
$ok2 = $false
if ($ok1) {
    aws ssm put-parameter --name $ApiParameterName --type SecureString --value "$content" --overwrite --tier $tier --region $Region
    $ok2 = ($LASTEXITCODE -eq 0)
}
$ErrorActionPreference = $ea

if ($ok1 -and $ok2) {
    Write-Host "SSM $ParameterName and $ApiParameterName updated." -ForegroundColor Green
    exit 0
} elseif ($ok1) {
    Write-Host "SSM $ParameterName updated; $ApiParameterName failed (check IAM ssm:PutParameter on academy/api/env)." -ForegroundColor Yellow
    exit 0
} else {
    Write-Host "upload_env_to_ssm: put-parameter failed." -ForegroundColor Red
    exit 1
}
