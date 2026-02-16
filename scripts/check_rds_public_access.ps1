# RDS 퍼블릭 액세스 설정 확인

param(
    [string]$Region = "ap-northeast-2",
    [string]$RdsIdentifier = "academy-db"
)

Write-Host "Checking RDS public access settings..." -ForegroundColor Cyan

$rdsInfo = aws rds describe-db-instances --region $Region --db-instance-identifier $RdsIdentifier --query "DBInstances[0].[PubliclyAccessible,Endpoint.Address,VpcSecurityGroups[0].VpcSecurityGroupId]" --output text 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Failed to get RDS info" -ForegroundColor Red
    exit 1
}

$parts = $rdsInfo.Trim() -split "`t"
$publiclyAccessible = $parts[0]
$endpoint = $parts[1]
$securityGroupId = $parts[2]

Write-Host "`nRDS Information:" -ForegroundColor Yellow
Write-Host "  Endpoint: $endpoint" -ForegroundColor White
Write-Host "  Publicly Accessible: $publiclyAccessible" -ForegroundColor $(if ($publiclyAccessible -eq "True") { "Green" } else { "Red" })
Write-Host "  Security Group: $securityGroupId" -ForegroundColor White

if ($publiclyAccessible -eq "False") {
    Write-Host "`n⚠ RDS is not publicly accessible!" -ForegroundColor Red
    Write-Host "You need to either:" -ForegroundColor Yellow
    Write-Host "  1. Enable public access for RDS (not recommended for production)" -ForegroundColor White
    Write-Host "  2. Use SSH tunnel (recommended)" -ForegroundColor White
    Write-Host "`nTo enable public access:" -ForegroundColor Yellow
    Write-Host "  aws rds modify-db-instance --region $Region --db-instance-identifier $RdsIdentifier --publicly-accessible --apply-immediately" -ForegroundColor Cyan
} else {
    Write-Host "`n✓ RDS is publicly accessible" -ForegroundColor Green
    Write-Host "Check security group rules if connection still fails." -ForegroundColor Yellow
}
