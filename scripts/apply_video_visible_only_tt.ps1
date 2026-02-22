# ==============================================================================
# Video ASG: Visible-only Target Tracking (e1 = m1, NotVisible 제외)
# desired = ApproximateNumberOfMessagesVisible / 1 → 유령(inflight)으로 scale 유지 방지
# ==============================================================================
# 사용: .\scripts\apply_video_visible_only_tt.ps1
#      .\scripts\apply_video_visible_only_tt.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$AsgName = "academy-video-worker-asg",
    [string]$OldPolicyName = "video-backlogcount-tt",
    [string]$NewPolicyName = "video-visible-only-tt"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$ConfigPath = Join-Path $RepoRoot "infra\worker_asg\video-visible-tt.json"

if (-not (Test-Path $ConfigPath)) {
    Write-Host "ERROR: Config not found: $ConfigPath" -ForegroundColor Red
    exit 1
}

$pathUri = "file://$($ConfigPath -replace '\\','/')"

Write-Host "[1/3] Deleting old policy: $OldPolicyName ..." -ForegroundColor Cyan
aws autoscaling delete-policy `
    --auto-scaling-group-name $AsgName `
    --policy-name $OldPolicyName `
    --region $Region 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "  (policy may not exist, continuing)" -ForegroundColor Gray }

Write-Host "[2/3] Putting new policy: $NewPolicyName (Visible only, e1=m1) ..." -ForegroundColor Cyan
aws autoscaling put-scaling-policy `
    --auto-scaling-group-name $AsgName `
    --policy-name $NewPolicyName `
    --policy-type TargetTrackingScaling `
    --target-tracking-configuration $pathUri `
    --region $Region

if ($LASTEXITCODE -ne 0) {
    Write-Host "put-scaling-policy failed." -ForegroundColor Red
    exit 1
}

Write-Host "[3/3] Verifying ..." -ForegroundColor Cyan
$tt = aws autoscaling describe-policies `
    --auto-scaling-group-name $AsgName `
    --region $Region `
    --query "ScalingPolicies[?PolicyName=='$NewPolicyName'].TargetTrackingConfiguration.CustomizedMetricSpecification.Metrics" `
    --output json
Write-Host $tt -ForegroundColor Gray
Write-Host "`nDone. desired = Visible / 1 (VideoQueueVisible)." -ForegroundColor Green
