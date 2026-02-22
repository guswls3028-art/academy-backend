# Run docker ps -a on each academy instance via SSM Run Command (no SSH)
# Usage: .\scripts\check_worker_docker.ps1 [-Region ap-northeast-2]
param([string]$Region = "ap-northeast-2")

$ErrorActionPreference = "Continue"
$nameFilter = "academy-api,academy-ai-worker-cpu,academy-messaging-worker"
$raw = aws ec2 describe-instances --region $Region `
    --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=$nameFilter" `
    --query "Reservations[].Instances[].[Tags[?Key=='Name'].Value | [0], InstanceId]" --output text 2>&1
if ($LASTEXITCODE -ne 0 -or -not $raw) {
    Write-Host "No instances found." -ForegroundColor Red
    exit 1
}

$lines = $raw.Trim() -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ }
foreach ($line in $lines) {
    $parts = $line -split "\s+", 2
    $name = $parts[0]
    $instanceId = $parts[1]
    if (-not $instanceId) { continue }
    Write-Host "`n=== $name ($instanceId) ===" -ForegroundColor Cyan
    $cmdId = aws ssm send-command --region $Region --instance-ids $instanceId `
        --document-name "AWS-RunShellScript" `
        --parameters 'commands=["docker ps -a"]' `
        --timeout-seconds 30 --output text --query "Command.CommandId" 2>&1
    if (-not $cmdId -or $cmdId -match "error|Error|not registered") {
        Write-Host "  SSM not available (agent offline or no permission)" -ForegroundColor Yellow
        continue
    }
    Start-Sleep -Seconds 3
    $status = aws ssm get-command-invocation --region $Region --command-id $cmdId --instance-id $instanceId --query "Status" --output text 2>&1
    $i = 0
    while ($status -eq "InProgress" -and $i -lt 10) { Start-Sleep -Seconds 2; $status = aws ssm get-command-invocation --region $Region --command-id $cmdId --instance-id $instanceId --query "Status" --output text 2>&1; $i++ }
    if ($status -eq "Success") {
        $out = aws ssm get-command-invocation --region $Region --command-id $cmdId --instance-id $instanceId --query "StandardOutputContent" --output text 2>&1
        if ($out) { Write-Host $out } else { Write-Host "  (no output)" -ForegroundColor Gray }
    } else {
        $err = aws ssm get-command-invocation --region $Region --command-id $cmdId --instance-id $instanceId --query "StandardErrorContent" --output text 2>&1
        Write-Host "  Status: $status" -ForegroundColor Red
        if ($err) { Write-Host $err }
    }
}
Write-Host ""
