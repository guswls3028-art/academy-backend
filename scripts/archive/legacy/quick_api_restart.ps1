# ==============================================================================
# API server restart only (ECR latest pull -> container restart). No build/worker. ~1-2 min.
#
# Requires: ECR has academy-api:latest (build/push once: quick_redeploy.ps1 -DeployTarget api or full_redeploy)
#   C:\key\backend-api-key.pem (API EC2 SSH)
#
# Usage: cd C:\academy; .\scripts\quick_api_restart.ps1
#
# After code change: 1) quick_redeploy.ps1 -DeployTarget api  2) then just quick_api_restart.ps1 (or restart only)
# ==============================================================================

param(
    [string]$KeyDir = "C:\key",
    [string]$Region = "ap-northeast-2",
    [switch]$StartStoppedInstances = $true
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot

$AccountId = (aws sts get-caller-identity --query Account --output text 2>&1)
if ($LASTEXITCODE -ne 0) {
    Write-Host "AWS identity check failed. Check login/permissions." -ForegroundColor Red
    exit 1
}
$ECR = "${AccountId}.dkr.ecr.${Region}.amazonaws.com"
$EC2_USER = "ec2-user"
$KeyFile = "backend-api-key.pem"
$ApiRemoteCmd = "aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $ECR && docker pull ${ECR}/academy-api:latest && (docker stop academy-api 2>/dev/null; docker rm academy-api 2>/dev/null; true) && docker run -d --name academy-api --restart unless-stopped --env-file .env -p 8000:8000 ${ECR}/academy-api:latest && docker update --restart unless-stopped academy-api"

function Get-ApiEc2Ip {
    $raw = aws ec2 describe-instances --region $Region `
        --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=academy-api" `
        --query "Reservations[].Instances[].[Tags[?Key=='Name'].Value | [0], PublicIpAddress]" `
        --output text 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
    $line = ($raw -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })[0]
    if (-not $line) { return $null }
    $p = $line -split "\s+", 2
    if ($p.Length -ge 2 -and $p[1] -and $p[1] -ne "None") { return $p[1].Trim() }
    return $null
}

function Start-StoppedApiInstance {
    $raw = aws ec2 describe-instances --region $Region `
        --filters "Name=tag:Name,Values=academy-api" "Name=instance-state-name,Values=stopped" `
        --query "Reservations[].Instances[].InstanceId" --output text 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return }
    $ids = $raw.Trim() -split "\s+" | Where-Object { $_ }
    if ($ids.Count -eq 0) { return }
    Write-Host "[EC2] Starting academy-api instance: $ids" -ForegroundColor Cyan
    aws ec2 start-instances --region $Region --instance-ids $ids 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { return }
    aws ec2 wait instance-running --region $Region --instance-ids $ids 2>&1 | Out-Null
    Start-Sleep -Seconds 15
    Write-Host "[EC2] Started." -ForegroundColor Green
}

Write-Host "`n=== Quick API restart (ECR pull + container restart) ===`n" -ForegroundColor Cyan

if ($StartStoppedInstances) { Start-StoppedApiInstance }

$apiIp = Get-ApiEc2Ip
if (-not $apiIp) {
    Write-Host "academy-api instance not found or not running." -ForegroundColor Red
    exit 1
}

$keyPath = Join-Path $KeyDir $KeyFile
if (-not (Test-Path $keyPath)) {
    Write-Host "Key not found: $keyPath" -ForegroundColor Red
    exit 1
}

Write-Host "[academy-api] $apiIp ..." -ForegroundColor Cyan
$cmd = "ssh -o StrictHostKeyChecking=accept-new -i `"$keyPath`" ${EC2_USER}@${apiIp} `"$ApiRemoteCmd`""
Invoke-Expression $cmd
if ($LASTEXITCODE -ne 0) {
    Write-Host "[academy-api] FAIL (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}
Write-Host "[academy-api] OK" -ForegroundColor Green
Write-Host "`n=== Quick API restart done ===`n" -ForegroundColor Green
