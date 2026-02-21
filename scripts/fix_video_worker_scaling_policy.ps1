# Video Worker ASG 스케일링 정책 재생성 (SQS 기반만)
# Usage: .\scripts\fix_video_worker_scaling_policy.ps1

param(
    [string]$Region = "ap-northeast-2",
    [int]$MaxCapacity = 20
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot

$AsgVideoName = "academy-video-worker-asg"
$ResourceIdVideo = "auto-scaling-group/$AsgVideoName"

# 1. Application Auto Scaling 타겟 등록 확인/생성
Write-Host "[1/3] Registering scalable target..." -ForegroundColor Cyan
$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
aws application-autoscaling register-scalable-target --service-namespace ec2 --resource-id $ResourceIdVideo `
    --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" --min-capacity 1 --max-capacity $MaxCapacity --region $Region 2>$null
$ErrorActionPreference = $ea

# 2. BacklogPerInstance 기반 Target Tracking 정책 생성
Write-Host "[2/3] Creating BacklogPerInstanceTargetTracking policy..." -ForegroundColor Cyan
$policyVideo = @"
{
  "TargetTrackingScalingPolicyConfiguration": {
    "TargetValue": 1.0,
    "CustomizedMetricSpecification": {
      "MetricName": "BacklogPerInstance",
      "Namespace": "Academy/Workers",
      "Dimensions": [{"Name": "WorkerType", "Value": "Video"}],
      "Statistic": "Average"
    },
    "ScaleInCooldown": 600,
    "ScaleOutCooldown": 60
  }
}
"@

$policyVideoFile = Join-Path $RepoRoot "asg_policy_video_temp.json"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($policyVideoFile, $policyVideo, $utf8NoBom)
$policyVideoPath = "file://$($policyVideoFile -replace '\\','/' -replace ' ', '%20')"

$ea = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
aws application-autoscaling put-scaling-policy --service-namespace ec2 --resource-id $ResourceIdVideo `
    --scalable-dimension "ec2:autoScalingGroup:DesiredCapacity" --policy-name "QueueDepthTargetTracking" `
    --policy-type "TargetTrackingScaling" --target-tracking-scaling-policy-configuration $policyVideoPath --region $Region
if ($LASTEXITCODE -eq 0) {
    Write-Host "      Policy created successfully" -ForegroundColor Green
} else {
    Write-Host "      Policy creation failed" -ForegroundColor Red
}
$ErrorActionPreference = $ea
Remove-Item $policyVideoFile -Force -ErrorAction SilentlyContinue

# 3. 정책 확인
Write-Host "[3/3] Verifying policy..." -ForegroundColor Cyan
aws application-autoscaling describe-scaling-policies --service-namespace ec2 --resource-id $ResourceIdVideo `
    --region $Region --query "ScalingPolicies[?PolicyName=='QueueDepthTargetTracking']" --output json

Write-Host "Done. Video Worker ASG scaling policy: BacklogPerInstance (TargetValue=1.0, ScaleInCooldown=600s)" -ForegroundColor Green
