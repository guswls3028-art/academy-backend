# One-off: check API server has created_by fix and container running
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
$cmd = "grep -n student_profile /home/ec2-user/academy/apps/domains/community/api/views.py 2>/dev/null; echo '---'; docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null | head -3"
$params = @{ commands = @($cmd) } | ConvertTo-Json -Compress
$send = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $ids[0], "--document-name", "AWS-RunShellScript", "--parameters", $params, "--region", $script:Region, "--output", "json")
$cid = $send.Command.CommandId
Start-Sleep -Seconds 10
$inv = Invoke-AwsJson @("ssm", "get-command-invocation", "--command-id", $cid, "--instance-id", $ids[0], "--region", $script:Region, "--output", "json")
Write-Host "Status: $($inv.Status)"
Write-Host "StdOut: $($inv.StandardOutputContent)"
if ($inv.StandardErrorContent) { Write-Host "StdErr: $($inv.StandardErrorContent)" }
