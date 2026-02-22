# ==============================================================================
# API Batch Runtime Verify
# Run check_batch_settings inside academy-api container.
# For local: docker ps. For remote EC2: pass -ApiIp and -KeyPath.
# Usage (local): .\scripts\check_api_batch_runtime.ps1
# Usage (remote): .\scripts\check_api_batch_runtime.ps1 -ApiIp 1.2.3.4 -KeyPath C:\key\backend-api-key.pem
# ==============================================================================

param(
    [string]$ApiIp = "",
    [string]$KeyPath = "",
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "== API Batch Runtime Verify ==" -ForegroundColor Cyan

if ($ApiIp) {
    if (-not $KeyPath -or -not (Test-Path $KeyPath)) {
        Write-Host "FAIL: -KeyPath required for remote check" -ForegroundColor Red
        exit 1
    }
    $EC2_USER = "ec2-user"
    $remoteCmd = "docker exec academy-api python manage.py check_batch_settings"
    $sshCmd = "ssh -o StrictHostKeyChecking=accept-new -i `"$KeyPath`" ${EC2_USER}@${ApiIp} `"$remoteCmd`""
    Invoke-Expression $sshCmd
} else {
    $cid = docker ps -q --filter name=academy-api 2>$null
    if (-not $cid) {
        Write-Host "FAIL: academy-api container not running" -ForegroundColor Red
        exit 1
    }

    docker exec $cid python manage.py check_batch_settings
}

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "FAIL: Batch settings missing in API runtime" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "PASS: Batch settings OK in API container" -ForegroundColor Green
exit 0
