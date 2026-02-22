# ==============================================================================
# Video Worker SSM 검증 — redeploy 후 확인
# Usage: .\scripts\verify_video_worker_ssm.ps1
# ==============================================================================

param([string]$Region = "ap-northeast-2")

$ErrorActionPreference = "Continue"
Write-Host "`n=== Video Worker SSM Verification ===`n" -ForegroundColor Cyan

# 1) ASG 인스턴스
$ids = aws ec2 describe-instances --region $Region `
  --filters "Name=tag:aws:autoscaling:groupName,Values=academy-video-worker-asg" "Name=instance-state-name,Values=running,pending" `
  --query "Reservations[].Instances[].[InstanceId,State.Name,LaunchTime]" --output text 2>$null
Write-Host "[1] Video Worker instances:" -ForegroundColor Yellow
if ($ids) { $ids | ForEach-Object { Write-Host "    $_" } } else { Write-Host "    (none)" }

# 2) SSM 등록 여부
$instanceIds = (aws ec2 describe-instances --region $Region `
  --filters "Name=tag:aws:autoscaling:groupName,Values=academy-video-worker-asg" "Name=instance-state-name,Values=running" `
  --query "Reservations[].Instances[].InstanceId" --output text 2>$null) -split "\s+" | Where-Object { $_ }
if ($instanceIds) {
  Write-Host "`n[2] SSM PingStatus (per instance):" -ForegroundColor Yellow
  foreach ($id in $instanceIds) {
    $info = aws ssm describe-instance-information --region $Region --filters "Key=InstanceIds,Values=$id" --query "InstanceInformationList[0].PingStatus" --output text 2>$null
    $info = if ($info -and $info -ne "None") { $info } else { "NotRegistered" }
    Write-Host "    $id : $info"
  }
} else {
  Write-Host "`n[2] SSM: No running instances to check" -ForegroundColor Gray
}

# 3) Scaling policy (SQS direct 확인)
Write-Host "`n[3] Scaling policy namespace:" -ForegroundColor Yellow
$ns = aws autoscaling describe-policies --auto-scaling-group-names academy-video-worker-asg --region $Region --query "ScalingPolicies[?PolicyName=='video-backlogcount-tt'].TargetTrackingConfiguration.CustomizedMetricSpecification.Metrics[0].MetricStat.Metric.Namespace" --output text 2>$null
Write-Host "    $ns (should be AWS/SQS for SQS direct)"

# 4) SSM Run Command 테스트 (첫 인스턴스 1개만)
if ($instanceIds -and $instanceIds[0]) {
  Write-Host "`n[4] SSM Run Command test (hostname):" -ForegroundColor Yellow
  $id = $instanceIds[0]
  $inputObj = @{ DocumentName = "AWS-RunShellScript"; InstanceIds = @($id); Parameters = @{ commands = @("hostname") } }
  $utf8 = [System.Text.UTF8Encoding]::new($false)
  $tmp = Join-Path $env:TEMP "ssm_verify_$(Get-Random).json"
  [System.IO.File]::WriteAllText($tmp, ($inputObj | ConvertTo-Json -Depth 5 -Compress), $utf8)
  $path = "file://$($tmp -replace '\\','/' -replace ' ', '%20')"
  $out = aws ssm send-command --cli-input-json $path --region $Region --output json 2>$null
  Remove-Item $tmp -Force -ErrorAction SilentlyContinue
  if ($out -match '"CommandId"') {
    $cmdId = ($out | ConvertFrom-Json).Command.CommandId
    Start-Sleep -Seconds 5
    $inv = aws ssm get-command-invocation --command-id $cmdId --instance-id $id --region $Region --output json 2>$null | ConvertFrom-Json
    Write-Host "    Status: $($inv.Status) | Output: $($inv.StandardOutputContent.Trim())"
    if ($inv.Status -eq "Success") { Write-Host "    SSM Run Command OK" -ForegroundColor Green } else { Write-Host "    SSM Run Command FAIL" -ForegroundColor Red }
  } else {
    Write-Host "    SSM send-command failed (instance may need 1-2 min to register)" -ForegroundColor Yellow
  }
}

Write-Host "`n=== Done ===`n" -ForegroundColor Cyan
