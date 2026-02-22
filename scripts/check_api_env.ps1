# ==============================================================================
# API Env Settings Verify
# Run check_api_env_settings inside academy-api container.
# Usage (local): .\scripts\check_api_env.ps1
# Usage (remote): .\scripts\check_api_env.ps1 -ApiIp 1.2.3.4 -KeyPath C:\key\backend-api-key.pem
# ==============================================================================

param(
    [string]$ApiIp = "",
    [string]$KeyPath = "",
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "== API Env Settings Verify ==" -ForegroundColor Cyan

$extraArgs = if ($Verbose) { " --verbose" } else { "" }

if ($ApiIp) {
    if (-not $KeyPath -or -not (Test-Path $KeyPath)) {
        Write-Host "FAIL: -KeyPath required for remote check" -ForegroundColor Red
        exit 1
    }
    $EC2_USER = "ec2-user"
    $remoteCmd = "docker exec academy-api python manage.py check_api_env_settings$extraArgs"
    $sshCmd = "ssh -o StrictHostKeyChecking=accept-new -i `"$KeyPath`" ${EC2_USER}@${ApiIp} `"$remoteCmd`""
    Invoke-Expression $sshCmd
} else {
    $cid = docker ps -q --filter name=academy-api 2>$null
    if (-not $cid) {
        Write-Host "FAIL: academy-api container not running" -ForegroundColor Red
        exit 1
    }

    docker exec $cid python manage.py check_api_env_settings $extraArgs
}

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "FAIL: Required env vars missing in API runtime" -ForegroundColor Red
    Write-Host "  Run: .\scripts\verify_ssm_api_env.ps1  then  .\scripts\upload_env_to_ssm.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "PASS: API env settings OK" -ForegroundColor Green
exit 0
