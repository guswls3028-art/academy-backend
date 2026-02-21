# ==============================================================================
# Video ASG TargetTracking 정책만 TargetValue 1로 갱신 (영상 1개당 워커 1대)
# deploy_worker_asg.ps1 전체 실행 없이 정책만 덮어씌움.
# ==============================================================================
# 사용: .\scripts\update_video_tt_target.ps1
#      .\scripts\update_video_tt_target.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$AsgName = "academy-video-worker-asg",
    [string]$PolicyName = "video-backlogcount-tt"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

# TargetValue 1 = SQS 메시지 1개당 워커 1대 (스케일링 소스 = SQS only)
$videoTtJson = '{"TargetValue":1.0,"CustomizedMetricSpecification":{"MetricName":"VideoQueueDepthTotal","Namespace":"Academy/VideoProcessing","Dimensions":[{"Name":"WorkerType","Value":"Video"},{"Name":"AutoScalingGroupName","Value":"academy-video-worker-asg"}],"Statistic":"Average","Unit":"Count"},"ScaleOutCooldown":60,"ScaleInCooldown":300}'
$videoTtFile = Join-Path $RepoRoot "asg_video_tt_ec2.json"
[System.IO.File]::WriteAllText($videoTtFile, $videoTtJson, $utf8NoBom)
$videoTtPath = "file://$($videoTtFile -replace '\\','/' -replace ' ', '%20')"

Write-Host "Applying $PolicyName (TargetValue=1, 1 video per 1 worker) on $AsgName ..." -ForegroundColor Cyan
aws autoscaling put-scaling-policy --auto-scaling-group-name $AsgName --policy-name $PolicyName --policy-type TargetTrackingScaling --target-tracking-configuration $videoTtPath --region $Region
if ($LASTEXITCODE -ne 0) {
    Remove-Item $videoTtFile -Force -ErrorAction SilentlyContinue
    throw "put-scaling-policy failed."
}
Remove-Item $videoTtFile -Force -ErrorAction SilentlyContinue
Write-Host "Done. Video ASG will scale ~1 worker per 1 SQS message (TargetValue=1, VideoQueueDepthTotal)." -ForegroundColor Green
