# Wait for API instance refresh to complete. Usage: pwsh -File wait-api-refresh.ps1 [-RefreshId <id>]
param([string]$RefreshId = "28b31570-88d0-408e-842b-4499c6e1d25d", [int]$MaxWaitSeconds = 900)
$ErrorActionPreference = "Stop"
$env:AWS_PROFILE = "default"
if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
$elapsed = 0
while ($elapsed -lt $MaxWaitSeconds) {
    $r = aws autoscaling describe-instance-refreshes --auto-scaling-group-name academy-v1-api-asg --instance-refresh-ids $RefreshId --region ap-northeast-2 --profile default --query "InstanceRefreshes[0].Status" --output text 2>&1
    Write-Host "Refresh status: $r (${elapsed}s)"
    if ($r -eq "Successful") { Write-Host "DONE"; exit 0 }
    if ($r -eq "Failed" -or $r -eq "Cancelled") { Write-Host "FAIL: $r"; exit 1 }
    Start-Sleep -Seconds 30
    $elapsed += 30
}
Write-Host "TIMEOUT after $MaxWaitSeconds s"; exit 2
