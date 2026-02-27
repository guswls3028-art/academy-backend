# Restart build instance (fix SSM registration)

param(
    [string]$Region = "ap-northeast-2",
    [string]$BuildInstanceId = "i-0133290c3502844ab"
)

$ErrorActionPreference = "Stop"

Write-Host "Restarting build instance..." -ForegroundColor Cyan
Write-Host "  Instance: $BuildInstanceId" -ForegroundColor Gray
Write-Host "  Region: $Region" -ForegroundColor Gray
Write-Host ""

# Check instance state
$state = aws ec2 describe-instances --region $Region --instance-ids $BuildInstanceId --query "Reservations[0].Instances[0].State.Name" --output text 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "Error: Failed to get instance state" -ForegroundColor Red
    exit 1
}

Write-Host "Current state: $state" -ForegroundColor Gray

if ($state -eq "running") {
    Write-Host "Rebooting instance..." -ForegroundColor Yellow
    aws ec2 reboot-instances --region $Region --instance-ids $BuildInstanceId 2>&1 | Out-Null
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Reboot initiated" -ForegroundColor Green
        Write-Host "Waiting for instance to be running..." -ForegroundColor Gray
        aws ec2 wait instance-running --region $Region --instance-ids $BuildInstanceId
        Write-Host "✓ Instance is running" -ForegroundColor Green
        Write-Host "`nWaiting 30 seconds for SSM agent to start..." -ForegroundColor Gray
        Start-Sleep -Seconds 30
        Write-Host "✓ Ready. You can now run full_redeploy.ps1" -ForegroundColor Green
    } else {
        Write-Host "✗ Failed to reboot instance" -ForegroundColor Red
        exit 1
    }
} elseif ($state -eq "stopped") {
    Write-Host "Starting instance..." -ForegroundColor Yellow
    aws ec2 start-instances --region $Region --instance-ids $BuildInstanceId 2>&1 | Out-Null
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Start initiated" -ForegroundColor Green
        Write-Host "Waiting for instance to be running..." -ForegroundColor Gray
        aws ec2 wait instance-running --region $Region --instance-ids $BuildInstanceId
        Write-Host "✓ Instance is running" -ForegroundColor Green
        Write-Host "`nWaiting 30 seconds for SSM agent to start..." -ForegroundColor Gray
        Start-Sleep -Seconds 30
        Write-Host "✓ Ready. You can now run full_redeploy.ps1" -ForegroundColor Green
    } else {
        Write-Host "✗ Failed to start instance" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "Instance is in state: $state" -ForegroundColor Yellow
    Write-Host "Cannot restart from this state." -ForegroundColor Yellow
}
