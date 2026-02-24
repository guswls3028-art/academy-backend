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
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
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
    if ($null -ne $v -and ($v = $v.Trim()) -ne '') { return $v }
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
    if ($k -eq "DB_PORT") { $collected[$k] = if ($envHash["DB_PORT"]) { $envHash["DB_PORT"] } else { "5432" }; continue }
    if ($k -eq "REDIS_PORT") { $collected[$k] = if ($envHash["REDIS_PORT"]) { $envHash["REDIS_PORT"] } else { "6379" }; continue }
    $v = Get-ValueOrPrompt -Hash $envHash -Key $k -Prompt $prompt -Interactive ($Interactive -or -not (Test-Path -LiteralPath $EnvPath))
    if ($null -eq $v -or ($v = $v.Trim()) -eq '') {
        $missing += $k
    } else {
        $collected[$k] = $v
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
$envRegion = ($collected["AWS_DEFAULT_REGION"] -or $envHash["AWS_DEFAULT_REGION"] -or "").Trim()
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
    if ($envHash[$k] -and ($envHash[$k].Trim() -ne '')) {
        $collected[$k] = $envHash[$k].Trim()
    }
}
if ($envHash["R2_VIDEO_BUCKET"] -and $envHash["R2_VIDEO_BUCKET"].Trim() -ne '') {
    $collected["R2_VIDEO_BUCKET"] = $envHash["R2_VIDEO_BUCKET"].Trim()
}
$collected["API_BASE_URL"] = ($collected["API_BASE_URL"] -or "").ToString().TrimEnd('/')

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

# Build JSON payload
$obj = @{}
foreach ($k in $collected.Keys) {
    $obj[$k] = $collected[$k]
}
$json = $obj | ConvertTo-Json -Compress

# Put parameter (avoid stderr as exception)
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$putOut = aws ssm put-parameter --name $ParamName --value $json --type SecureString --region $Region --overwrite 2>&1
$putExit = $LASTEXITCODE
$ErrorActionPreference = $prevErr
if ($putExit -ne 0) {
    Write-Host "FAIL: put-parameter failed (exit $putExit): $putOut" -ForegroundColor Red
    exit 1
}

# Confirm (do not print Value)
$prevErr2 = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$getOut = aws ssm get-parameter --name $ParamName --region $Region --query "Parameter.{Name:Name,Type:Type,Version:Version}" --output json 2>&1 | ConvertFrom-Json
$getExit = $LASTEXITCODE
$ErrorActionPreference = $prevErr2
if ($getExit -ne 0 -or -not $getOut -or $getOut.Name -ne $ParamName) {
    Write-Host "FAIL: Parameter could not be validated after write (exit $getExit)." -ForegroundColor Red
    exit 1
}
Write-Host "Putting SSM parameter: $ParamName (SecureString)" -ForegroundColor Cyan
Write-Host "OK: $ParamName written successfully." -ForegroundColor Green
Write-Host "ParameterVersion: $($getOut.Version)" -ForegroundColor Cyan
