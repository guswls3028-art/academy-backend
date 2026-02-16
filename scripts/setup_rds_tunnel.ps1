# ==============================================================================
# RDS SSH Tunnel — 로컬에서 RDS 접속용 (academy-api EC2 경유)
# 사용: .\scripts\setup_rds_tunnel.ps1
# 백그라운드 실행: Start-Process powershell -ArgumentList "-File", ".\scripts\setup_rds_tunnel.ps1"
# ==============================================================================

param(
    [string]$KeyDir = "C:\key",
    [string]$KeyFile = "backend-api-key.pem",
    [string]$ApiHost = "15.165.147.157",  # academy-api EC2 Public IP
    [string]$RdsHost = "academy-db.cbm4oqigwl80.ap-northeast-2.rds.amazonaws.com",
    [int]$LocalPort = 5433,  # 로컬 포트 (5432는 로컬 DB용)
    [int]$RdsPort = 5432,
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$KeyPath = Join-Path $KeyDir $KeyFile

if (-not (Test-Path $KeyPath)) {
    Write-Host "SSH key not found: $KeyPath" -ForegroundColor Red
    Write-Host "Set -KeyDir and -KeyFile to your SSH key location." -ForegroundColor Yellow
    exit 1
}

# 기존 터널 프로세스 확인 및 종료 (포트 사용 중인지 확인)
$portInUse = Get-NetTCPConnection -LocalPort $LocalPort -ErrorAction SilentlyContinue
if ($portInUse) {
    Write-Host "Port $LocalPort is already in use. Stopping..." -ForegroundColor Yellow
    $pid = ($portInUse | Select-Object -First 1).OwningProcess
    if ($pid) {
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
}

Write-Host "Setting up SSH tunnel to RDS..." -ForegroundColor Cyan
Write-Host "  Local: localhost:${LocalPort}" -ForegroundColor Gray
Write-Host "  Remote: ${RdsHost}:${RdsPort}" -ForegroundColor Gray
Write-Host "  Via: ${ApiHost} (academy-api)" -ForegroundColor Gray
Write-Host ""
Write-Host "To use in Django, set in .env.local:" -ForegroundColor Yellow
Write-Host "  DB_HOST=127.0.0.1" -ForegroundColor White
Write-Host "  DB_PORT=$LocalPort" -ForegroundColor White
Write-Host ""
Write-Host "Press Ctrl+C to stop the tunnel." -ForegroundColor Gray
Write-Host ""

# SSH 터널 생성 (포트 포워딩)
ssh -i "$KeyPath" `
    -L ${LocalPort}:${RdsHost}:${RdsPort} `
    -N `
    -o StrictHostKeyChecking=accept-new `
    -o ServerAliveInterval=60 `
    ec2-user@${ApiHost}
