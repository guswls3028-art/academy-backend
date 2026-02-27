# ==============================================================================
# Cleanup Legacy Video ASG Scaling (VIDEO ONLY)
# - Video 인코딩은 AWS Batch로 전환됨
# - AI/Messaging ASG는 건드리지 않음
# Usage: .\scripts\infra\batch_video_cleanup_legacy.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [string]$VideoAsgName = "academy-video-worker-asg"
)

$ErrorActionPreference = "Stop"

Write-Host "== Cleanup Legacy Video ASG Scaling (VIDEO ONLY) ==" -ForegroundColor Cyan
Write-Host "ASG: $VideoAsgName | Region: $Region" -ForegroundColor Gray

# 1) Set ASG capacity to 0
Write-Host "`n[1] Set ASG min/max/desired = 0" -ForegroundColor Cyan
try {
    aws autoscaling update-auto-scaling-group `
        --auto-scaling-group-name $VideoAsgName `
        --min-size 0 --max-size 0 --desired-capacity 0 `
        --region $Region 2>&1 | Out-Host
    if ($LASTEXITCODE -eq 0) { Write-Host "  Done" -ForegroundColor Green }
    else { Write-Host "  (ASG may not exist)" -ForegroundColor Yellow }
} catch {
    Write-Host "  ASG update failed (may not exist): $_" -ForegroundColor Yellow
}

# 2) Delete TargetTracking policies for this ASG only
Write-Host "`n[2] Delete TargetTracking policies (VIDEO ONLY)" -ForegroundColor Cyan
try {
    $policiesJson = aws autoscaling describe-policies --auto-scaling-group-name $VideoAsgName --region $Region --output json 2>&1
    $policies = $policiesJson | ConvertFrom-Json
    $tt = $policies.ScalingPolicies | Where-Object { $_.PolicyType -eq "TargetTrackingScaling" }
    foreach ($p in $tt) {
        Write-Host "  Deleting policy: $($p.PolicyName)" -ForegroundColor Yellow
        aws autoscaling delete-policy --auto-scaling-group-name $VideoAsgName --policy-name $p.PolicyName --region $Region 2>&1 | Out-Null
    }
    if (-not $tt -or $tt.Count -eq 0) { Write-Host "  No TargetTracking policies found" -ForegroundColor Gray }
} catch {
    Write-Host "  (No policies or ASG not found)" -ForegroundColor Gray
}

# 3) Delete related CloudWatch alarms
Write-Host "`n[3] Delete TargetTracking alarms for ASG" -ForegroundColor Cyan
$alarmPrefix = "TargetTracking-$VideoAsgName"
try {
    $alarmsJson = aws cloudwatch describe-alarms --alarm-name-prefix $alarmPrefix --region $Region --output json 2>&1
    $alarms = $alarmsJson | ConvertFrom-Json
    if ($alarms.MetricAlarms -and $alarms.MetricAlarms.Count -gt 0) {
        $names = $alarms.MetricAlarms | Select-Object -ExpandProperty AlarmName
        Write-Host "  Deleting alarms: $($names -join ', ')" -ForegroundColor Yellow
        aws cloudwatch delete-alarms --alarm-names $names --region $Region
    } else {
        Write-Host "  No alarms with prefix $alarmPrefix" -ForegroundColor Gray
    }
} catch {
    Write-Host "  (No alarms found)" -ForegroundColor Gray
}

Write-Host "`nDONE. Legacy VIDEO scaling disabled. AI/Messaging untouched." -ForegroundColor Green
