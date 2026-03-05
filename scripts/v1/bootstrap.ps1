# ==============================================================================
# Academy v1 — 새 PC 준비. aws cli, pwsh, 인증, region·권한 확인.
# Usage: pwsh scripts/v1/bootstrap.ps1
# ==============================================================================
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
Write-Host "`n=== Bootstrap v1 ===" -ForegroundColor Cyan

# PowerShell 5+ required
$psVersion = $PSVersionTable.PSVersion.Major
if ($psVersion -lt 5) {
    Write-Host "FAIL: PowerShell 5+ required. Current: $psVersion" -ForegroundColor Red
    exit 1
}
Write-Host "OK: PowerShell $psVersion" -ForegroundColor Green

# AWS CLI
$aws = Get-Command aws -ErrorAction SilentlyContinue
if (-not $aws) {
    Write-Host "FAIL: aws CLI not found. Install: https://aws.amazon.com/cli/" -ForegroundColor Red
    exit 1
}
Write-Host "OK: aws CLI $($aws.Source)" -ForegroundColor Green
$awsVersion = aws --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: aws --version failed (encoding/runtime?). Fix AWS CLI." -ForegroundColor Red
    exit 1
}
Write-Host "OK: $($awsVersion | Out-String | Select-Object -First 1)" -ForegroundColor Green

# AWS identity
$region = $env:AWS_REGION
if (-not $region) { $region = "ap-northeast-2" }
$id = aws sts get-caller-identity --output json --region $region 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: AWS not configured or no credentials. Run: aws configure" -ForegroundColor Red
    exit 1
}
$idObj = $id | ConvertFrom-Json
Write-Host "OK: Account $($idObj.Account)" -ForegroundColor Green
Write-Host "OK: Region $region (set AWS_REGION to override)" -ForegroundColor Green

# Minimal describe (required for drift/deploy)
$null = aws ec2 describe-vpcs --max-items 1 --region $region --output json 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAIL: Minimal describe test failed. Check IAM permissions (ec2:DescribeVpcs)." -ForegroundColor Red
    exit 1
}
Write-Host "OK: Minimal describe permission" -ForegroundColor Green

# params.yaml
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$ParamsPath = Join-Path $RepoRoot "docs\00-SSOT\v1\params.yaml"
if (-not (Test-Path $ParamsPath)) {
    Write-Host "FAIL: params.yaml not found at $ParamsPath" -ForegroundColor Red
    exit 1
}
Write-Host "OK: params.yaml found" -ForegroundColor Green

Write-Host "`nNext: pwsh scripts/v1/deploy.ps1 -Plan" -ForegroundColor Cyan
Write-Host "=== Bootstrap done ===`n" -ForegroundColor Green
