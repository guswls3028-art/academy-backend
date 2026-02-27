# RDS status check (including public access)

param(
    [string]$Region = "ap-northeast-2",
    [string]$RdsIdentifier = "academy-db"
)

Write-Host "Checking RDS status..." -ForegroundColor Cyan

$rdsInfo = aws rds describe-db-instances --region $Region --db-instance-identifier $RdsIdentifier --query "DBInstances[0].[PubliclyAccessible,Endpoint.Address,DBInstanceStatus]" --output text 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Failed to get RDS info" -ForegroundColor Red
    Write-Host $rdsInfo -ForegroundColor Yellow
    exit 1
}

$parts = $rdsInfo.Trim() -split "`t"
$publiclyAccessible = $parts[0]
$endpoint = $parts[1]
$status = $parts[2]

Write-Host "`nRDS Status:" -ForegroundColor Yellow
Write-Host "  Status: $status" -ForegroundColor $(if ($status -eq "available") { "Green" } else { "Yellow" })
Write-Host "  Endpoint: $endpoint" -ForegroundColor White
Write-Host "  Publicly Accessible: $publiclyAccessible" -ForegroundColor $(if ($publiclyAccessible -eq "True") { "Green" } else { "Red" })

if ($publiclyAccessible -eq "True") {
    Write-Host "`n✓ RDS is publicly accessible!" -ForegroundColor Green
    Write-Host "You can connect directly without SSH tunnel." -ForegroundColor Green
} else {
    Write-Host "`n⚠ RDS is NOT publicly accessible" -ForegroundColor Red
    Write-Host "You need to use SSH tunnel or enable public access." -ForegroundColor Yellow
}

if ($status -ne "available") {
    Write-Host "`n⚠ RDS is modifying. Wait for status to become 'available'." -ForegroundColor Yellow
}
