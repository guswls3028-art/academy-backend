# ==============================================================================
# academy-video-worker-asg에 BacklogCount TargetTrackingScaling 정책 적용
# aws autoscaling put-scaling-policy 사용 (EC2 Auto Scaling API)
# ==============================================================================
# 사용: .\scripts\apply_video_target_tracking.ps1
#      .\scripts\apply_video_target_tracking.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$AsgName = "academy-video-worker-asg",
    [string]$PolicyName = "BacklogCountTargetTracking"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot

$config = @{
    TargetValue = 3
    CustomizedMetricSpecification = @{
        Namespace   = "Academy/VideoProcessing"
        MetricName  = "BacklogCount"
        Statistic   = "Average"
        Dimensions  = @(
            @{ Name = "WorkerType"; Value = "Video" }
            @{ Name = "AutoScalingGroupName"; Value = $AsgName }
        )
    }
    ScaleOutCooldown = 60
    ScaleInCooldown  = 300
}

$configJson = $config | ConvertTo-Json -Depth 5 -Compress
$configFile = Join-Path $RepoRoot "video_target_tracking_config.json"
[System.IO.File]::WriteAllText($configFile, $configJson, [System.Text.UTF8Encoding]::new($false))

$configPath = "file:///$($configFile -replace '\\','/' -replace ' ', '%20')"

Write-Host "[1/2] Creating BacklogCountTargetTracking policy on $AsgName ..." -ForegroundColor Cyan
aws autoscaling put-scaling-policy `
    --auto-scaling-group-name $AsgName `
    --policy-name $PolicyName `
    --policy-type TargetTrackingScaling `
    --target-tracking-configuration $configPath `
    --region $Region

if ($LASTEXITCODE -ne 0) {
    Remove-Item $configFile -Force -ErrorAction SilentlyContinue
    Write-Host "put-scaling-policy failed." -ForegroundColor Red
    exit 1
}

Remove-Item $configFile -Force -ErrorAction SilentlyContinue

Write-Host "`n[2/2] Verifying ScalingPolicies ..." -ForegroundColor Cyan
$result = aws autoscaling describe-policies `
    --auto-scaling-group-name $AsgName `
    --region $Region `
    --output json | ConvertFrom-Json

$policies = $result.ScalingPolicies
if ($policies.Count -eq 0) {
    Write-Host "WARN: No ScalingPolicies found. Policy may be under application-autoscaling (use describe-scaling-policies)." -ForegroundColor Yellow
} else {
    Write-Host "ScalingPolicies ($($policies.Count)):" -ForegroundColor Green
    $policies | ForEach-Object {
        Write-Host "  - $($_.PolicyName) | Type=$($_.PolicyType) | ARN=$($_.PolicyARN)" -ForegroundColor Gray
    }
}

Write-Host "`nDone. BacklogCountTargetTracking applied." -ForegroundColor Green
