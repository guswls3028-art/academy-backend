# ==============================================================================
# LAMBDA_INTERNAL_API_KEY 반영: .env -> SSM 업로드 -> API EC2 .env 갱신 및 재시작
# Usage: .\scripts\sync_api_env_lambda_internal.ps1
# ==============================================================================

param(
    [string]$KeyDir = "C:\key",
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
. (Join-Path $ScriptRoot "_config_instance_keys.ps1")

Write-Host "[1/3] Upload .env to SSM /academy/workers/env..." -ForegroundColor Cyan
& (Join-Path $ScriptRoot "upload_env_to_ssm.ps1") -RepoRoot $RepoRoot -Region $Region
if ($LASTEXITCODE -ne 0) {
    Write-Host "  SSM upload failed. Ensure AWS credentials are valid." -ForegroundColor Red
    exit 1
}

Write-Host "[2/3] Get academy-api EC2 public IP..." -ForegroundColor Cyan
$raw = aws ec2 describe-instances --region $Region `
    --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=academy-api" `
    --query "Reservations[].Instances[].[PublicIpAddress]" --output text 2>&1
$apiIp = ($raw -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -ne "None" } | Select-Object -First 1)
if (-not $apiIp) {
    Write-Host "  academy-api EC2 not found or no public IP." -ForegroundColor Red
    exit 1
}
Write-Host "  IP: $apiIp" -ForegroundColor Gray

$keyPath = Join-Path $KeyDir $INSTANCE_KEY_FILES["academy-api"]
if (-not (Test-Path $keyPath)) {
    Write-Host "  Key not found: $keyPath" -ForegroundColor Red
    exit 1
}

Write-Host "[3/3] Merge SSM into EC2 .env (preserve R2 etc.) and recreate academy-api..." -ForegroundColor Cyan
# merge_ssm_into_env.sh: SSM으로 덮어쓰지 않고 병합 → 기존 .env에만 있던 변수(R2, VIDEO_BUCKET 등) 유지
$remoteCmd = "cd /home/ec2-user/academy 2>/dev/null || true; bash /home/ec2-user/academy/scripts/merge_ssm_into_env.sh /home/ec2-user/.env $Region && bash /home/ec2-user/academy/scripts/refresh_api_container_env.sh"
ssh -o StrictHostKeyChecking=accept-new -i "$keyPath" "ec2-user@${apiIp}" $remoteCmd
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Done. academy-api recreated with merged .env." -ForegroundColor Green
} else {
    Write-Host "  SSH/script may have failed. On EC2 run: bash scripts/merge_ssm_into_env.sh && bash scripts/refresh_api_container_env.sh" -ForegroundColor Yellow
}
