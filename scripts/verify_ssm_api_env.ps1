# ==============================================================================
# STRICT: Verify SSM /academy/api/env has full API env (DB_*, REDIS_*, R2_*).
# Run locally (PowerShell). If missing -> instruct upload_env_to_ssm.ps1.
# Usage: .\scripts\verify_ssm_api_env.ps1
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$SsmName = "/academy/api/env"
)

$ErrorActionPreference = "Stop"

# Resolve AWS CLI (script may run in context where "aws" is not in PATH, e.g. 32-bit PowerShell)
$awsCmd = Get-Command aws -ErrorAction SilentlyContinue
$awsExe = if ($awsCmd) { $awsCmd.Source } else { $null }
if (-not $awsExe) {
    $candidates = @(
        "C:\Program Files\AmazonAWSCLIV2\aws.exe",
        "${env:ProgramFiles(x86)}\AmazonAWSCLIV2\aws.exe",
        "$env:LOCALAPPDATA\Programs\AmazonAWSCLIV2\aws.exe"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c -ErrorAction SilentlyContinue)) { $awsExe = $c; break }
    }
}
if (-not $awsExe) { $awsExe = "aws" }

$requiredKeys = @("DB_HOST", "DB_NAME", "DB_USER", "REDIS_HOST", "R2_ENDPOINT", "R2_ACCESS_KEY", "R2_SECRET_KEY")
$optionalButRecommended = @("LAMBDA_INTERNAL_API_KEY", "DJANGO_SETTINGS_MODULE")

Write-Host "[1/2] Get SSM $SsmName..." -ForegroundColor Cyan
# Use JSON output so large/Advanced-tier values are captured reliably (--output text can mangle newlines)
$raw = $null
$awsOut = $null
$exitCode = $null
try {
    $awsOut = & $awsExe ssm get-parameter --name $SsmName --with-decryption --region $Region --output json 2>&1
    $exitCode = $LASTEXITCODE
    $jsonStr = ($awsOut | Out-String).Trim()
    if ($exitCode -eq 0 -and $jsonStr.StartsWith("{")) {
        $obj = $jsonStr | ConvertFrom-Json
        $raw = $obj.Parameter.Value
    }
} catch {
    $raw = $null
    if ($null -eq $exitCode) { $exitCode = -1 }
    $jsonStr = if ($awsOut) { ($awsOut | Out-String).Trim() } else { "" }
}
if ($null -eq $raw -or [string]::IsNullOrWhiteSpace($raw)) {
    Write-Host "  FAIL: SSM get failed or parameter empty. (ExitCode: $exitCode)" -ForegroundColor Red
    if ([string]::IsNullOrWhiteSpace($jsonStr)) {
        if ($exitCode -eq -1) {
            Write-Host "  No output from AWS. Check: 'aws' in PATH? Run:  aws sts get-caller-identity" -ForegroundColor Gray
        }
    } else {
        $preview = if ($jsonStr.Length -gt 600) { $jsonStr.Substring(0, 600) + "..." } else { $jsonStr }
        Write-Host "  AWS output:" -ForegroundColor Gray
        Write-Host "  $($preview -replace "`r`n", "`n" -replace "`n", "`n  ")" -ForegroundColor Gray
    }
    Write-Host "  Run: .\scripts\upload_env_to_ssm.ps1  (with full .env containing DB_*, REDIS_*, R2_*)" -ForegroundColor Yellow
    exit 1
}

$content = ($raw -replace "`r`n", "`n" -replace "`r", "`n").Trim()
$lines = $content -split "`n" | Where-Object { $_.Trim() -ne "" }

$present = @{}
foreach ($line in $lines) {
    if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        $key = $Matches[1]
        $val = $Matches[2].Trim()
        if (-not [string]::IsNullOrEmpty($val)) { $present[$key] = $true }
    }
}

Write-Host "[2/2] Check required keys..." -ForegroundColor Cyan
$missing = @()
foreach ($k in $requiredKeys) {
    if (-not $present[$k]) {
        $missing += $k
    }
}

if ($missing.Count -gt 0) {
    Write-Host "  FAIL: SSM missing required keys: $($missing -join ', ')" -ForegroundColor Red
    Write-Host "  Run: .\scripts\upload_env_to_ssm.ps1  (with full .env)" -ForegroundColor Yellow
    exit 1
}

Write-Host "  OK: SSM has DB_*, REDIS_*, R2_* (required keys present)." -ForegroundColor Green
Write-Host "  Next: On EC2 run:  cd /home/ec2-user/academy && bash scripts/deploy_api_on_server.sh" -ForegroundColor Cyan
Write-Host "  Then:  bash scripts/verify_api_after_deploy.sh" -ForegroundColor Cyan
exit 0
