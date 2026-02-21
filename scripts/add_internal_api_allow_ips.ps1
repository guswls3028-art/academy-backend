# ==============================================================================
# SSM /academy/api/env에 INTERNAL_API_ALLOW_IPS 추가/수정 (Lambda -> API 403 방지)
# Lambda가 API와 같은 VPC(172.30.x.x)에 있으면 이 대역이 없으면 IsLambdaInternal에서 403.
# Usage: .\scripts\add_internal_api_allow_ips.ps1
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$SsmName = "/academy/api/env",
    [string]$AllowIps = "172.30.0.0/16"
)

$ErrorActionPreference = "Stop"
$NewLine = "INTERNAL_API_ALLOW_IPS=$AllowIps"

Write-Host "[1/3] Get current SSM $SsmName..." -ForegroundColor Cyan
$ea = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $current = & aws ssm get-parameter --name $SsmName --with-decryption --region $Region --query "Parameter.Value" --output text 2>$null
} finally {
    $ErrorActionPreference = $ea
}
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($current)) {
    Write-Host "  SSM get failed or parameter empty. Refusing to overwrite." -ForegroundColor Red
    Write-Host "  Run: .\scripts\upload_env_to_ssm.ps1  first." -ForegroundColor Yellow
    exit 1
}

$lines = ($current -replace "`r`n", "`n" -replace "`r", "`n" -split "`n" | Where-Object { $_.Trim() -ne "" })
$newLines = @()
$replaced = $false
foreach ($line in $lines) {
    if ($line -match '^\s*INTERNAL_API_ALLOW_IPS\s*=') {
        $newLines += $NewLine
        $replaced = $true
    } else {
        $newLines += $line
    }
}
if (-not $replaced) {
    $newLines += $NewLine
}
$newContent = ($newLines -join "`n").Trim()

Write-Host "[2/3] Put updated SSM $SsmName..." -ForegroundColor Cyan
$tier = if ($newContent.Length -gt 4096) { "Advanced" } else { "Standard" }
aws ssm put-parameter --name $SsmName --type SecureString --value $newContent --overwrite --tier $tier --region $Region
if ($LASTEXITCODE -ne 0) {
    Write-Host "  SSM put failed." -ForegroundColor Red
    exit 1
}
Write-Host "  INTERNAL_API_ALLOW_IPS=$AllowIps set in SSM." -ForegroundColor Green

Write-Host "[3/3] Next: on API EC2 run deploy so container picks up new env:" -ForegroundColor Cyan
Write-Host "  cd /home/ec2-user/academy && bash scripts/deploy_api_on_server.sh" -ForegroundColor Gray
Write-Host "  (or: bash scripts/merge_ssm_into_env.sh /home/ec2-user/.env $Region $SsmName && bash scripts/refresh_api_container_env.sh)" -ForegroundColor Gray
Write-Host "Then: aws lambda invoke --function-name academy-worker-queue-depth-metric --region $Region response.json; Get-Content response.json" -ForegroundColor Gray
