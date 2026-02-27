# ASG Messaging: ensure academy-messaging-worker-asg exists. Do not overwrite Desired 0.
function Ensure-ASGMessaging {
    Write-Step "ASG $($script:MessagingASGName)"
    $a = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $script:MessagingASGName, "--region", $script:Region, "--output", "json")
    if (-not $a -or -not $a.AutoScalingGroups -or $a.AutoScalingGroups.Count -eq 0) {
        Write-Warn "ASG $($script:MessagingASGName) not found (create via deploy_worker_asg.ps1 or manual)"
        return
    }
    $x = $a.AutoScalingGroups[0]
    Write-Ok "ASG $($script:MessagingASGName) Desired=$($x.DesiredCapacity) Min=$($x.MinSize) Max=$($x.MaxSize)"
}

function Confirm-ASGMessagingState {
    Ensure-ASGMessaging
}
