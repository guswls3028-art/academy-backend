# ==============================================================================
# 2단계: SSM /academy/api/env에 LAMBDA_INTERNAL_API_KEY 추가 후 API EC2 .env 반영 및 재시작
# Usage: .\scripts\add_lambda_internal_key_api.ps1
# ==============================================================================

param(
    [string]$KeyDir = "C:\key",
    [string]$Region = "ap-northeast-2",
    [string]$LambdaInternalApiKey = "hakwonplus-internal-key"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
. (Join-Path $ScriptRoot "_config_instance_keys.ps1")

$SsmName = "/academy/api/env"
$NewLine = "LAMBDA_INTERNAL_API_KEY=$LambdaInternalApiKey"

Write-Host "[1/4] Get current SSM $SsmName..." -ForegroundColor Cyan
$current = aws ssm get-parameter --name $SsmName --with-decryption --region $Region --query "Parameter.Value" --output text 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  SSM get failed. Creating new content with LAMBDA_INTERNAL_API_KEY only." -ForegroundColor Yellow
    $current = ""
}

# Normalize line endings and ensure LAMBDA_INTERNAL_API_KEY is set (add or replace)
$lines = ($current -replace "`r`n", "`n" -replace "`r", "`n" -split "`n" | Where-Object { $_.Trim() -ne "" })
$hasKey = $false
$newLines = @()
foreach ($line in $lines) {
    if ($line -match '^\s*LAMBDA_INTERNAL_API_KEY\s*=') {
        $newLines += $NewLine
        $hasKey = $true
    } else {
        $newLines += $line
    }
}
if (-not $hasKey) {
    $newLines += $NewLine
}
$newContent = ($newLines -join "`n").Trim()

Write-Host "[2/4] Put updated SSM $SsmName..." -ForegroundColor Cyan
$tier = if ($newContent.Length -gt 4096) { "Advanced" } else { "Standard" }
aws ssm put-parameter --name $SsmName --type SecureString --value $newContent --overwrite --tier $tier --region $Region
if ($LASTEXITCODE -ne 0) {
    Write-Host "  SSM put failed." -ForegroundColor Red
    exit 1
}
Write-Host "  LAMBDA_INTERNAL_API_KEY set in SSM." -ForegroundColor Green

Write-Host "[3/4] Get academy-api EC2 public IP..." -ForegroundColor Cyan
$raw = aws ec2 describe-instances --region $Region `
    --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=academy-api" `
    --query "Reservations[].Instances[].[PublicIpAddress]" --output text 2>&1
$apiIp = ($raw -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -ne "None" } | Select-Object -First 1)
if (-not $apiIp) {
    Write-Host "  academy-api EC2 not found or no public IP. SSM updated; run on EC2 manually:" -ForegroundColor Yellow
    Write-Host "  aws ssm get-parameter --name $SsmName --with-decryption --region $Region --query Parameter.Value --output text > /home/ec2-user/.env" -ForegroundColor Gray
    Write-Host "  docker restart academy-api" -ForegroundColor Gray
    exit 0
}
Write-Host "  IP: $apiIp" -ForegroundColor Gray

$keyPath = Join-Path $KeyDir $INSTANCE_KEY_FILES["academy-api"]
if (-not (Test-Path $keyPath)) {
    Write-Host "  Key not found: $keyPath. SSM updated; on EC2 run:" -ForegroundColor Yellow
    Write-Host "  aws ssm get-parameter --name $SsmName --with-decryption --region $Region --query Parameter.Value --output text > /home/ec2-user/.env" -ForegroundColor Gray
    Write-Host "  docker restart academy-api" -ForegroundColor Gray
    exit 0
}

Write-Host "[4/4] Merge SSM into EC2 .env and recreate academy-api..." -ForegroundColor Cyan
$remoteCmd = "cd /home/ec2-user/academy 2>/dev/null || true; bash /home/ec2-user/academy/scripts/merge_ssm_into_env.sh /home/ec2-user/.env $Region $SsmName && bash /home/ec2-user/academy/scripts/refresh_api_container_env.sh"
ssh -o StrictHostKeyChecking=accept-new -i "$keyPath" "ec2-user@${apiIp}" $remoteCmd
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Done. academy-api recreated with LAMBDA_INTERNAL_API_KEY." -ForegroundColor Green
} else {
    Write-Host "  SSH/script may have failed. On EC2 run:" -ForegroundColor Yellow
    Write-Host "  bash scripts/merge_ssm_into_env.sh /home/ec2-user/.env $Region $SsmName && bash scripts/refresh_api_container_env.sh" -ForegroundColor Gray
}
