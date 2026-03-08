# Fetch docker logs from API instance via SSM
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
$instanceId = $ids[0]
$cmd = "docker logs academy-api --tail 150 2>&1"
$params = @{ commands = @($cmd) } | ConvertTo-Json -Compress
$send = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $instanceId, "--document-name", "AWS-RunShellScript", "--parameters", $params, "--region", $script:Region, "--output", "json")
$cid = $send.Command.CommandId
Write-Host "CommandId: $cid"
Start-Sleep -Seconds 20
$inv = Invoke-AwsJson @("ssm", "get-command-invocation", "--command-id", $cid, "--instance-id", $instanceId, "--region", $script:Region, "--output", "json")
Write-Host "Status: $($inv.Status)"
Write-Host "--- stdout ---"
Write-Host $inv.StandardOutputContent
if ($inv.StandardErrorContent) {
    Write-Host "--- stderr ---"
    Write-Host $inv.StandardErrorContent
}
