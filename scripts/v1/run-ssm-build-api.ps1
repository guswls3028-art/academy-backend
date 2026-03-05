# 빌드 서버(academy-build-arm64)에서 academy-api 이미지 빌드 후 ECR 푸시를 SSM으로 실행.
# 사용: pwsh -NoProfile -File scripts/v1/run-with-env.ps1 -- pwsh -NoProfile -File scripts/v1/run-ssm-build-api.ps1
# AWS 인증은 run-with-env로 주입. 본 스크립트는 .env 직접 로드하지 않음.
$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$jsonPath = Join-Path $ScriptRoot "tmp-ssm-api-build.json"
if (-not (Test-Path $jsonPath)) { Write-Error "Not found: $jsonPath"; exit 1 }
$o = Get-Content -Raw $jsonPath | ConvertFrom-Json
$commands = @($o.commands)
$paramsJson = @{ commands = $commands } | ConvertTo-Json -Compress
$region = "ap-northeast-2"
$instanceId = "i-07f6f245de7026361"
$out = aws ssm send-command --instance-ids $instanceId --document-name "AWS-RunShellScript" --parameters $paramsJson --region $region --output json 2>&1
if ($LASTEXITCODE -ne 0) { Write-Error "SSM send-command failed: $out"; exit 1 }
$cmdId = ($out | ConvertFrom-Json).Command.CommandId
Write-Host "CommandId: $cmdId"
Write-Host "Waiting for command to complete..."
$done = $false
for ($i = 0; $i -lt 90; $i++) {
    $status = aws ssm get-command-invocation --command-id $cmdId --instance-id $instanceId --region $region --output json 2>&1 | ConvertFrom-Json
    $st = $status.Status
    Write-Host "  Status: $st"
    if ($st -eq "Success") { $done = $true; break }
    if ($st -eq "Failed" -or $st -eq "Cancelled") { Write-Error "SSM command $st"; Write-Host $status.StandardErrorContent; exit 1 }
    Start-Sleep -Seconds 10
}
if (-not $done) { Write-Error "SSM command timed out"; exit 1 }
Write-Host $status.StandardOutputContent
if ($status.StandardErrorContent) { Write-Host "Stderr: $($status.StandardErrorContent)" }
Write-Host "Build and push completed."
