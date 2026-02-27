# ==============================================================================
# Academy v4 — 새 PC 준비. aws cli, pwsh, 인증, region·권한 확인.
# Usage: pwsh scripts/v4/bootstrap.ps1
# ==============================================================================
$ErrorActionPreference = "Stop"
Write-Host "`n=== Bootstrap v4 ===" -ForegroundColor Cyan

# PowerShell 7 권장
$psVersion = $PSVersionTable.PSVersion.Major
if ($psVersion -lt 5) {
    Write-Host "WARN: PowerShell 5+ recommended. Current: $psVersion" -ForegroundColor Yellow
}

# AWS CLI
$aws = Get-Command aws -ErrorAction SilentlyContinue
if (-not $aws) {
    Write-Host "FAIL: aws CLI not found. Install: https://aws.amazon.com/cli/" -ForegroundColor Red
    exit 1
}
Write-Host "OK: aws CLI $($aws.Source)" -ForegroundColor Green

# AWS identity
$id = aws sts get-caller-identity --output json 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: AWS not configured or no credentials. Run: aws configure" -ForegroundColor Red
    exit 1
}
$idObj = $id | ConvertFrom-Json
Write-Host "OK: Account $($idObj.Account)" -ForegroundColor Green

# Region
$region = $env:AWS_REGION
if (-not $region) { $region = "ap-northeast-2" }
Write-Host "OK: Region $region (set AWS_REGION to override)" -ForegroundColor Green

# params.yaml 존재
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$ParamsPath = Join-Path $RepoRoot "docs\00-SSOT\v4\params.yaml"
if (-not (Test-Path $ParamsPath)) {
    Write-Host "FAIL: params.yaml not found at $ParamsPath" -ForegroundColor Red
    exit 1
}
Write-Host "OK: params.yaml found" -ForegroundColor Green

Write-Host "`nNext: pwsh scripts/v4/deploy.ps1 -Plan" -ForegroundColor Cyan
Write-Host "=== Bootstrap done ===`n" -ForegroundColor Green
