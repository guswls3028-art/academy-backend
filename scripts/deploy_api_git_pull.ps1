# ==============================================================================
# API server: git pull -> build -> restart (apply code changes)
#
# Flow: you do code change -> git push; then run this -> API server git pull -> docker build -> API restart
#
# Requires:
#   - Repo already cloned on API server (academy-api EC2). If not, clone manually or set -RepoPath.
#   - .env at that path (or -EnvPath)
#   - C:\key\backend-api-key.pem (API EC2 SSH)
#
# Usage: cd C:\academy; .\scripts\deploy_api_git_pull.ps1
#
# First-time (no repo on API server): SSH then git clone ... /home/ec2-user/academy; put .env in /home/ec2-user/.env or repo dir
# ==============================================================================

param(
    [string]$KeyDir = "C:\key",
    [string]$Region = "ap-northeast-2",
    [string]$RepoPath = "/home/ec2-user/academy",
    [string]$EnvPath = "/home/ec2-user/.env",
    [string]$Branch = "main",
    [switch]$StartStoppedInstances = $true
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$EC2_USER = "ec2-user"
$KeyFile = "backend-api-key.pem"

# Remote: cd repo -> pull -> base build -> api build -> remove old container -> run new container
$RemoteScript = @"
set -e
if [ ! -d '$RepoPath' ]; then echo 'ERROR: Repo not found at $RepoPath. Clone it first (e.g. git clone <url> $RepoPath)'; exit 1; fi
cd '$RepoPath'
git fetch origin
git reset --hard origin/$Branch
git pull origin $Branch
docker build -f docker/Dockerfile.base -t academy-base:latest .
docker build -f docker/api/Dockerfile -t academy-api:latest .
(docker stop academy-api 2>/dev/null; docker rm academy-api 2>/dev/null; true)
docker run -d --name academy-api --restart unless-stopped --env-file '$EnvPath' -p 8000:8000 academy-api:latest
echo DONE
"@
$Utf8 = [System.Text.Encoding]::UTF8
$RemoteScriptB64 = [Convert]::ToBase64String($Utf8.GetBytes($RemoteScript.Trim()))

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
    Write-Host "[EC2] Starting academy-api instance..." -ForegroundColor Cyan
    aws ec2 start-instances --region $Region --instance-ids $ids 2>&1 | Out-Null
    aws ec2 wait instance-running --region $Region --instance-ids $ids 2>&1 | Out-Null
    Start-Sleep -Seconds 15
    Write-Host "[EC2] Started." -ForegroundColor Green
}

Write-Host "`n=== API server: git pull -> build -> restart ===`n" -ForegroundColor Cyan
Write-Host "Repo: $RepoPath  Branch: $Branch  Env: $EnvPath" -ForegroundColor Gray

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

Write-Host "[academy-api] $apiIp - git pull & build & restart ..." -ForegroundColor Cyan
& ssh -o StrictHostKeyChecking=accept-new -i $keyPath "${EC2_USER}@${apiIp}" "echo $RemoteScriptB64 | base64 -d | bash"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[academy-api] FAIL (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}
Write-Host "[academy-api] OK - code applied" -ForegroundColor Green
Write-Host "`n=== Done ===`n" -ForegroundColor Green
