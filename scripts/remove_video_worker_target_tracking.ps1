# Video Worker ASG에서 TargetTracking 정책 제거 (Lambda 단독 컨트롤용)
# Usage: .\scripts\remove_video_worker_target_tracking.ps1

param(
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
$ResourceIdVideo = "auto-scaling-group/academy-video-worker-asg"

Write-Host "Removing QueueDepthTargetTracking from academy-video-worker-asg..." -ForegroundColor Cyan
$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
aws application-autoscaling delete-scaling-policy --service-namespace ec2 --resource-id $ResourceIdVideo `
    --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" --policy-name "QueueDepthTargetTracking" --region $Region 2>&1
$ErrorActionPreference = $ea
if ($LASTEXITCODE -eq 0) {
    Write-Host "Done. Video ASG now uses Lambda-only control." -ForegroundColor Green
} else {
    Write-Host "Policy may not exist (already removed). Check: aws application-autoscaling describe-scaling-policies --service-namespace ec2 --resource-id $ResourceIdVideo --region $Region" -ForegroundColor Yellow
}
