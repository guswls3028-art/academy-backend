# Video Worker ASG: TargetTracking 정책 제거 (Lambda 단독 컨트롤)
# Usage: .\scripts\fix_video_worker_scaling_policy.ps1

param(
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ResourceIdVideo = "auto-scaling-group/academy-video-worker-asg"

Write-Host "[1/2] Removing TargetTracking policy from Video ASG..." -ForegroundColor Cyan
$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
aws application-autoscaling delete-scaling-policy --service-namespace ec2 --resource-id $ResourceIdVideo `
    --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" --policy-name "QueueDepthTargetTracking" --region $Region 2>&1
$ErrorActionPreference = $ea

Write-Host "[2/2] Verifying no scaling policy on Video ASG..." -ForegroundColor Cyan
aws application-autoscaling describe-scaling-policies --service-namespace ec2 --resource-id $ResourceIdVideo --region $Region --output json

Write-Host "Done. Video ASG uses Lambda-only control (no TargetTracking)." -ForegroundColor Green
