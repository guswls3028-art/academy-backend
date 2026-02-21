# Video Worker ASG: EC2 Auto Scaling 정책 제거 (Lambda 단독 DesiredCapacity 제어)
# EC2 ASG는 Application Auto Scaling 대상이 아님. aws autoscaling delete-policy 사용.
# Usage: .\scripts\fix_video_worker_scaling_policy.ps1

param(
    [string]$Region = "ap-northeast-2",
    [string]$AsgName = "academy-video-worker-asg"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "[1/3] Querying current ScalingPolicies on $AsgName..." -ForegroundColor Cyan
$result = aws autoscaling describe-policies `
    --auto-scaling-group-name $AsgName `
    --region $Region | ConvertFrom-Json

$policies = @()
if ($null -ne $result.ScalingPolicies) {
    $policies = @($result.ScalingPolicies)
}
$count = $policies.Count

Write-Host "  Found $count policy(ies)." -ForegroundColor Gray

if ($count -gt 0) {
    Write-Host "[2/3] Deleting all ScalingPolicies..." -ForegroundColor Cyan
    foreach ($p in $policies) {
        Write-Host "  Deleting policy: $($p.PolicyName)" -ForegroundColor Gray
        aws autoscaling delete-policy `
            --auto-scaling-group-name $AsgName `
            --policy-name $p.PolicyName `
            --region $Region
    }
} else {
    Write-Host "[2/3] No policies to delete." -ForegroundColor Gray
}

Write-Host "[3/3] Verifying ScalingPolicies is empty..." -ForegroundColor Cyan
$verify = aws autoscaling describe-policies `
    --auto-scaling-group-name $AsgName `
    --region $Region | ConvertFrom-Json

$remaining = if ($null -eq $verify.ScalingPolicies) { 0 } else { @($verify.ScalingPolicies).Count }
if ($remaining -gt 0) {
    throw "Verification failed: ScalingPolicies still has $remaining item(s)."
}

aws autoscaling describe-policies `
    --auto-scaling-group-name $AsgName `
    --region $Region --output json

Write-Host "Done. Video ASG ($AsgName) uses Lambda-only control (no EC2 scaling policy)." -ForegroundColor Green
