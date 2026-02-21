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
# Run via Start-Process so we get a real exit code and reliable stdout/stderr (avoid 2>&1 / $LASTEXITCODE issues)
$raw = $null
$jsonStr = ""
$exitCode = -1
$stderrStr = ""
try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $awsExe
    $psi.Arguments = "ssm get-parameter --name `"$SsmName`" --with-decryption --region $Region --output json"
    [void]($psi.UseShellExecute = $false)
    [void]($psi.RedirectStandardOutput = $true)
    [void]($psi.RedirectStandardError = $true)
    [void]($psi.CreateNoWindow = $true)
    # Force UTF-8 stdout so SSM value with Unicode (e.g. U+2014) does not trigger cp949 encode error on Korean Windows
    $psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
    $psi.EnvironmentVariables["PYTHONUTF8"] = "1"
    try { $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8; $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8 } catch { }
    $p = [System.Diagnostics.Process]::Start($psi)
    $p.WaitForExit(60000)
    $stdout = $p.StandardOutput.ReadToEnd()
    $stderr = $p.StandardError.ReadToEnd()
    $exitCode = $p.ExitCode
    $jsonStr = ($stdout | Out-String).Trim()
    $stderrStr = ($stderr | Out-String).Trim()
    if ($exitCode -eq 0 -and $jsonStr.StartsWith("{")) {
        $obj = $jsonStr | ConvertFrom-Json
        $raw = $obj.Parameter.Value
    }
    $null  # avoid leaking True/False from assignments to host
} catch {
    $raw = $null
    if ($exitCode -eq -1) { $stderrStr = $_.Exception.Message }
}
if ($null -eq $raw -or [string]::IsNullOrWhiteSpace($raw)) {
    Write-Host "  FAIL: SSM get failed or parameter empty. (ExitCode: $exitCode)" -ForegroundColor Red
    $show = $stderrStr
    if ([string]::IsNullOrWhiteSpace($show)) { $show = $jsonStr }
    if (-not [string]::IsNullOrWhiteSpace($show)) {
        $preview = if ($show.Length -gt 600) { $show.Substring(0, 600) + "..." } else { $show }
        Write-Host "  AWS output:" -ForegroundColor Gray
        Write-Host "  $($preview -replace "`r`n", "`n" -replace "`n", "`n  ")" -ForegroundColor Gray
    } elseif ($exitCode -ne 0) {
        Write-Host "  Run:  & '$awsExe' sts get-caller-identity  (check credentials)" -ForegroundColor Gray
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
