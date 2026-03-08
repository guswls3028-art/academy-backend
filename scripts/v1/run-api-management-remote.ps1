# Run Django management command on API server (production DB)
param([string]$Command = "fix_qna_orphan_created_by --dry-run")
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot
. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")
. (Join-Path $PSScriptRoot "resources\api.ps1")
$env:AWS_PROFILE = "default"
$null = Load-SSOT -Env "prod"
$ids = @(Get-APIASGInstanceIds)
if (-not $ids -or $ids.Count -eq 0) { Write-Host "No API instance"; exit 1 }
$bashCmd = "cd /home/ec2-user/academy && docker run --rm --env-file /home/ec2-user/.env academy-api:latest python manage.py $Command"
$params = @{ commands = @($bashCmd) } | ConvertTo-Json -Compress
$send = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $ids[0], "--document-name", "AWS-RunShellScript", "--parameters", $params, "--region", $script:Region, "--output", "json")
$cid = $send.Command.CommandId
Write-Host "Waiting for command (up to 60s)..."
Start-Sleep -Seconds 15
$wait = 0
while ($wait -lt 60) {
    $inv = Invoke-AwsJson @("ssm", "get-command-invocation", "--command-id", $cid, "--instance-id", $ids[0], "--region", $script:Region, "--output", "json")
    if ($inv.Status -eq "Success") {
        Write-Host "Status: $($inv.Status)"
        Write-Host $inv.StandardOutputContent
        if ($inv.StandardErrorContent) { Write-Host $inv.StandardErrorContent -ForegroundColor Yellow }
        exit 0
    }
    if ($inv.Status -eq "Failed" -or $inv.Status -eq "Cancelled") {
        Write-Host "Status: $($inv.Status)" -ForegroundColor Red
        Write-Host $inv.StandardOutputContent
        Write-Host $inv.StandardErrorContent -ForegroundColor Red
        exit 1
    }
    Start-Sleep -Seconds 5
    $wait += 5
}
Write-Host "Timeout waiting for command"
exit 1
