# ASG AI: Ensure academy-ai-worker-asg exists. Describe only; if missing warn (create via legacy or manual).
function Ensure-ASGAi {
    Write-Step "ASG $($script:AiASGName)"
    if ($script:PlanMode) { Write-Ok "ASG AI check skipped (Plan)"; return }
    $a = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $script:AiASGName, "--region", $script:Region, "--output", "json")
    if (-not $a -or -not $a.AutoScalingGroups -or $a.AutoScalingGroups.Count -eq 0) {
        Write-Warn "ASG $($script:AiASGName) not found (create via deploy_worker_asg.ps1 or manual)"
        return
    }
    $x = $a.AutoScalingGroups[0]
    Write-Ok "ASG $($script:AiASGName) Desired=$($x.DesiredCapacity) Min=$($x.MinSize) Max=$($x.MaxSize)"
}
