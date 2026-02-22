# ==============================================================================
# [DEPRECATED] Video = AWS Batch 전용. academy-video-worker-asg 미사용.
# Video Worker ASG 재배포 (Instance Refresh 전용)
# 규칙: put-scaling-policy 금지, ASG Desired 변경 금지, TargetTracking 수정 금지
# LT version update, Instance Refresh만 수행.
# ==============================================================================
# 사용: .\scripts\redeploy\redeploy_video_worker.ps1
#      .\scripts\redeploy\redeploy_video_worker.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$AsgName = "academy-video-worker-asg"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptsRoot = Split-Path -Parent $ScriptRoot

Write-Host "`n=== Video Worker Redeploy (Instance Refresh only) ===" -ForegroundColor Cyan
Write-Host "  ASG: $AsgName | put-scaling-policy 절대 호출 안 함`n" -ForegroundColor Gray

# 진행 중인 Instance Refresh 확인
$inProgress = aws autoscaling describe-instance-refreshes `
    --region $Region `
    --auto-scaling-group-name $AsgName `
    --query "InstanceRefreshes[?Status=='InProgress'].[InstanceRefreshId,Status]" `
    --output json 2>&1
$inProgressObj = $inProgress | ConvertFrom-Json -ErrorAction SilentlyContinue
if ($inProgressObj -and $inProgressObj.Count -gt 0) {
    Write-Host "Instance refresh already in progress. Skipping." -ForegroundColor Yellow
} else {
    Write-Host "[1/2] Starting instance refresh..." -ForegroundColor Cyan
    aws autoscaling start-instance-refresh `
        --region $Region `
        --auto-scaling-group-name $AsgName
    if ($LASTEXITCODE -ne 0) {
        Write-Host "start-instance-refresh failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Instance refresh started." -ForegroundColor Green
}

Write-Host "`n[2/2] Scaling Policy 검증 (Expression=m1, m2 미포함 유지):" -ForegroundColor Cyan
aws autoscaling describe-policies `
    --auto-scaling-group-name $AsgName `
    --region $Region `
    --query "ScalingPolicies[?PolicyType=='TargetTrackingScaling'].TargetTrackingConfiguration.CustomizedMetricSpecification.Metrics" `
    --output json

Write-Host "`nDone. redeploy 시 Scaling Policy 롤백 없음." -ForegroundColor Green
