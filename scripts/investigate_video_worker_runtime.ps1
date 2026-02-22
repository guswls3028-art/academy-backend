# ==============================================================================
# Video Worker Runtime Investigation — STRICT MODE
# Output: InstanceId, ContainerRunning, WorkerLog, ffmpegProcessCount per instance
# Usage: .\scripts\investigate_video_worker_runtime.ps1
# ==============================================================================

param([string]$Region = "ap-northeast-2")

$ErrorActionPreference = "Stop"
$AsgName = "academy-video-worker-asg"
$ContainerName = "academy-video-worker"
$TimeoutSec = 60

Write-Host "`n=== Video Worker Runtime Investigation ===" -ForegroundColor Cyan
Write-Host "Region=$Region`n" -ForegroundColor Gray

# 1) Get running video worker instance IDs
$instancesRaw = aws ec2 describe-instances `
  --region $Region `
  --filters "Name=tag:aws:autoscaling:groupName,Values=$AsgName" "Name=instance-state-name,Values=running" `
  --query "Reservations[].Instances[].InstanceId" `
  --output text 2>&1
if ($LASTEXITCODE -ne 0) { Write-Host "EC2 describe failed: $instancesRaw" -ForegroundColor Red; exit 1 }
$instances = ($instancesRaw -split "\s+") | Where-Object { $_.Trim() -ne "" }
if ($instances.Count -eq 0) { Write-Host "No running instances in $AsgName" -ForegroundColor Yellow; exit 0 }

foreach ($instanceId in $instances) {
  Write-Host "InstanceId: $instanceId" -ForegroundColor Cyan

  $commands = @(
    "echo '==== INSTANCE ===='",
    "hostname",
    "docker ps 2>/dev/null || true",
    "docker logs $ContainerName --tail 50 2>/dev/null || echo 'NO_LOGS'",
    "ps aux | grep ffmpeg 2>/dev/null | grep -v grep || echo 'NO_FFMPEG'"
  )
  $commandsJson = $commands | ConvertTo-Json -Compress
  $paramsJson = "{`"commands`":$commandsJson}"

  $sendOut = aws ssm send-command `
    --document-name "AWS-RunShellScript" `
    --instance-ids $instanceId `
    --parameters $paramsJson `
    --timeout-seconds $TimeoutSec `
    --region $Region `
    --output json 2>&1
  if ($LASTEXITCODE -ne 0) {
    Write-Host "ContainerRunning: NO (SSM send failed)" -ForegroundColor Red
    Write-Host "WorkerLog: $sendOut" -ForegroundColor Gray
    Write-Host "ffmpegProcessCount: N/A`n" -ForegroundColor Gray
    continue
  }

  $cmdObj = $sendOut | ConvertFrom-Json
  $cmdId = $cmdObj.Command.CommandId
  if (-not $cmdId) {
    Write-Host "ContainerRunning: NO (no CommandId)" -ForegroundColor Red
    Write-Host "WorkerLog: N/A" -ForegroundColor Gray
    Write-Host "ffmpegProcessCount: N/A`n" -ForegroundColor Gray
    continue
  }

  $status = "Pending"
  $inv = $null
  for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 3
    $invRaw = aws ssm get-command-invocation --command-id $cmdId --instance-id $instanceId --region $Region --output json 2>&1
    $inv = $invRaw | ConvertFrom-Json
    $status = $inv.Status
    if ($status -eq "Success" -or $status -eq "Failed" -or $status -eq "Cancelled") { break }
  }

  $stdout = ""
  $stderr = ""
  if ($inv) {
    $stdout = if ($inv.StandardOutputContent) { $inv.StandardOutputContent -replace "`r`n", "`n" -replace "`r", "`n" } else { "" }
    $stderr = if ($inv.StandardErrorContent) { $inv.StandardErrorContent -replace "`r`n", "`n" -replace "`r", "`n" } else { "" }
  }

  $containerRunning = "NO"
  if ($stdout -match "academy-video-worker") { $containerRunning = "YES" }

  $ffmpegCount = 0
  $ffLines = $stdout -split "`n" | Where-Object { $_ -match "ffmpeg" -and $_ -notmatch "grep" }
  if ($ffLines) { $ffmpegCount = $ffLines.Count }

  Write-Host "ContainerRunning: $containerRunning"
  Write-Host "WorkerLog:"
  $all = ($stdout + "`n" + $stderr).Trim()
  foreach ($ln in ($all -split "`n")) {
    Write-Host "  $ln" -ForegroundColor Gray
  }
  Write-Host "ffmpegProcessCount: $ffmpegCount`n"
}
