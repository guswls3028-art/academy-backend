# ==============================================================================
# CloudWatch video alarms verification: print whether each expected alarm exists.
# Usage: .\scripts\infra\validate_video_alarms.ps1 -Region ap-northeast-2
# ==============================================================================

param([string]$Region = "ap-northeast-2")

$ErrorActionPreference = "Stop"
$AlarmNames = @(
    "academy-video-DeadJobs",
    "academy-video-UploadFailures",
    "academy-video-FailedJobs",
    "academy-video-BatchJobFailures",
    "academy-video-QueueRunnable"
)
$r = aws cloudwatch describe-alarms --alarm-names $AlarmNames --region $Region --output json 2>&1 | ConvertFrom-Json
$found = @(if ($r.MetricAlarms) { $r.MetricAlarms | ForEach-Object { $_.AlarmName } } else { @() })
foreach ($name in $AlarmNames) {
    $exists = $found -contains $name
    Write-Host "  $name : $(if ($exists) { 'EXISTS' } else { 'MISSING' })"
}
$missing = $AlarmNames | Where-Object { $_ -notin $found }
if ($missing.Count -gt 0) {
    Write-Host "FAIL: Missing alarms: $($missing -join ', '). Run scripts\infra\cloudwatch_deploy_video_alarms.ps1" -ForegroundColor Red
    exit 1
}
Write-Host "All video alarms exist." -ForegroundColor Green
