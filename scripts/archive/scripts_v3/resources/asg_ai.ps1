# ASG AI: ensure academy-ai-worker-asg exists. Do not overwrite Desired 0.
function Ensure-ASGAi {
    Write-Step "ASG $($script:AiASGName)"
    $a = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $script:AiASGName, "--region", $script:Region, "--output", "json")
    if (-not $a -or -not $a.AutoScalingGroups -or $a.AutoScalingGroups.Count -eq 0) {
        Write-Warn "ASG $($script:AiASGName) not found (create via deploy_worker_asg.ps1 or manual)"
        return
    }
    $x = $a.AutoScalingGroups[0]
    Write-Ok "ASG $($script:AiASGName) Desired=$($x.DesiredCapacity) Min=$($x.MinSize) Max=$($x.MaxSize)"
}

function Confirm-ASGAiState {
    Ensure-ASGAi
}
