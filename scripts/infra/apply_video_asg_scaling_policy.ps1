# ==============================================================================
# SSOT: Video ASG Scaling Policy 적용 (유일하게 put-scaling-policy 허용)
# redeploy_worker_asg.ps1 / full_redeploy.ps1 에서 절대 호출 금지.
# 사용: .\scripts\infra\apply_video_asg_scaling_policy.ps1
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$AsgName = "academy-video-worker-asg",
    [string]$PolicyName = "video-visible-only-tt"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$ConfigPath = Join-Path $RepoRoot "infra\worker_asg\video-visible-tt.json"

if (-not (Test-Path $ConfigPath)) {
    Write-Host "ERROR: SSOT config not found: $ConfigPath" -ForegroundColor Red
    exit 1
}

$pathUri = "file://$($ConfigPath -replace '\\','/')"
$OldPolicyName = "video-backlogcount-tt"

Write-Host "[1/3] Deleting old policy: $OldPolicyName ..." -ForegroundColor Cyan
aws autoscaling delete-policy `
    --auto-scaling-group-name $AsgName `
    --policy-name $OldPolicyName `
    --region $Region 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "  (policy may not exist)" -ForegroundColor Gray }

Write-Host "[2/3] Putting policy: $PolicyName (e1=m1 Visible only, SSOT: video-visible-tt.json) ..." -ForegroundColor Cyan
aws autoscaling put-scaling-policy `
    --auto-scaling-group-name $AsgName `
    --policy-name $PolicyName `
    --policy-type TargetTrackingScaling `
    --target-tracking-configuration $pathUri `
    --region $Region

if ($LASTEXITCODE -ne 0) {
    Write-Host "put-scaling-policy failed." -ForegroundColor Red
    exit 1
}

Write-Host "[3/3] Verifying (Expression=m1, m2 미포함) ..." -ForegroundColor Cyan
$metrics = aws autoscaling describe-policies `
    --auto-scaling-group-name $AsgName `
    --region $Region `
    --query "ScalingPolicies[?PolicyType=='TargetTrackingScaling'].TargetTrackingConfiguration.CustomizedMetricSpecification.Metrics" `
    --output json 2>$null
$metricsObj = $metrics | ConvertFrom-Json
$e1 = $metricsObj | Where-Object { $_.Id -eq "e1" } | Select-Object -First 1
if ($e1 -and $e1.Expression -eq "m1") {
    Write-Host "  OK: Expression=m1 (Visible only)" -ForegroundColor Green
} else {
    Write-Host "  WARN: Expected Expression=m1, got: $($e1.Expression)" -ForegroundColor Yellow
}
if ($metricsObj | Where-Object { $_.Id -eq "m2" }) {
    Write-Host "  FAIL: m2(NotVisible) should not exist" -ForegroundColor Red
    exit 1
}
Write-Host "`nDone. desired = Visible / 1" -ForegroundColor Green
