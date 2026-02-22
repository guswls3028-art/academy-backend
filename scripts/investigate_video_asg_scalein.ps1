# ==============================================================================
# STRICT INVESTIGATION — academy-video-worker-asg scale-in
# Output: m1, m2, m3, e1, DesiredCapacity (CloudWatch + ASG 실제 값)
# Usage: .\scripts\investigate_video_asg_scalein.ps1
#        .\scripts\investigate_video_asg_scalein.ps1 -Region ap-northeast-2
# ==============================================================================

param([string]$Region = "ap-northeast-2")

$ErrorActionPreference = "Stop"
$AsgName = "academy-video-worker-asg"
$QueueName = "academy-video-jobs"

$end = (Get-Date).ToUniversalTime()
$start = $end.AddMinutes(-5)
$startStr = $start.ToString("yyyy-MM-ddTHH:mm:ssZ")
$endStr = $end.ToString("yyyy-MM-ddTHH:mm:ssZ")

Write-Host "`n=== Video ASG Scale-In Investigation ===" -ForegroundColor Cyan
Write-Host "Region=$Region | TimeRange=$startStr ~ $endStr`n" -ForegroundColor Gray

# 1) m3 = InService instance count
$asgJson = aws autoscaling describe-auto-scaling-groups `
  --auto-scaling-group-names $AsgName `
  --region $Region `
  --output json 2>&1
if ($LASTEXITCODE -ne 0) { Write-Host "ASG describe failed: $asgJson" -ForegroundColor Red; exit 1 }
$asg = $asgJson | ConvertFrom-Json
$inService = ($asg.AutoScalingGroups[0].Instances | Where-Object { $_.LifecycleState -eq "InService" }).Count
$desired = $asg.AutoScalingGroups[0].DesiredCapacity
$m3 = $inService

# 2) m1 = Visible
$visibleJson = aws cloudwatch get-metric-statistics `
  --namespace AWS/SQS `
  --metric-name ApproximateNumberOfMessagesVisible `
  --dimensions Name=QueueName,Value=$QueueName `
  --statistics Average `
  --period 60 `
  --start-time $startStr `
  --end-time $endStr `
  --region $Region `
  --output json 2>&1
if ($LASTEXITCODE -ne 0) { Write-Host "CloudWatch Visible failed: $visibleJson" -ForegroundColor Red; exit 1 }
$visibleObj = $visibleJson | ConvertFrom-Json
$m1 = 0
if ($visibleObj.Datapoints -and $visibleObj.Datapoints.Count -gt 0) {
  $m1 = [math]::Round(($visibleObj.Datapoints | Measure-Object -Property Average -Average).Average, 2)
}

# 3) m2 = NotVisible
$notVisibleJson = aws cloudwatch get-metric-statistics `
  --namespace AWS/SQS `
  --metric-name ApproximateNumberOfMessagesNotVisible `
  --dimensions Name=QueueName,Value=$QueueName `
  --statistics Average `
  --period 60 `
  --start-time $startStr `
  --end-time $endStr `
  --region $Region `
  --output json 2>&1
if ($LASTEXITCODE -ne 0) { Write-Host "CloudWatch NotVisible failed: $notVisibleJson" -ForegroundColor Red; exit 1 }
$notVisibleObj = $notVisibleJson | ConvertFrom-Json
$m2 = 0
if ($notVisibleObj.Datapoints -and $notVisibleObj.Datapoints.Count -gt 0) {
  $m2 = [math]::Round(($notVisibleObj.Datapoints | Measure-Object -Property Average -Average).Average, 2)
}

# 4) e1
$e1 = $m1 + $m2

# 5) Strict Report
Write-Host "m1 (Visible) = $m1"
Write-Host "m2 (NotVisible) = $m2"
Write-Host "m3 (InService) = $m3"
Write-Host "e1 (Metric Input) = $e1"
Write-Host "DesiredCapacity = $desired"
Write-Host ""
