# RDS 퍼블릭 액세스 활성화

param(
    [string]$Region = "ap-northeast-2",
    [string]$RdsIdentifier = "academy-db"
)

$ErrorActionPreference = "Stop"

Write-Host "Enabling public access for RDS..." -ForegroundColor Cyan
Write-Host "  DB Instance: $RdsIdentifier" -ForegroundColor Gray
Write-Host "  Region: $Region" -ForegroundColor Gray
Write-Host ""

Write-Host "This will make RDS publicly accessible." -ForegroundColor Yellow
Write-Host "Press Ctrl+C to cancel, or any key to continue..." -ForegroundColor Yellow
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

Write-Host "`nEnabling public access..." -ForegroundColor Gray

$result = aws rds modify-db-instance `
    --region $Region `
    --db-instance-identifier $RdsIdentifier `
    --publicly-accessible `
    --apply-immediately `
    2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ Public access enabled successfully!" -ForegroundColor Green
    Write-Host "`nNote: It may take a few minutes for the change to take effect." -ForegroundColor Yellow
    Write-Host "You can check the status in AWS Console." -ForegroundColor Yellow
} else {
    Write-Host "✗ Failed to enable public access:" -ForegroundColor Red
    Write-Host $result -ForegroundColor Red
    exit 1
}
