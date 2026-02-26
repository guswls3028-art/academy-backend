# Validate ASG state (no create). Desired capacity must not be overwritten to 0 by any update.
function Confirm-ASGState {
    Write-Step "Validate ASG (Messaging + AI)"
    $m = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $script:MessagingASGName, "--region", $script:Region, "--output", "json")
    $a = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $script:AiASGName, "--region", $script:Region, "--output", "json")
    if ($m -and $m.AutoScalingGroups -and $m.AutoScalingGroups.Count -gt 0) {
        $x = $m.AutoScalingGroups[0]
        Write-Ok "ASG $($script:MessagingASGName) Desired=$($x.DesiredCapacity) Min=$($x.MinSize) Max=$($x.MaxSize)"
    } else { Write-Warn "ASG $($script:MessagingASGName) not found" }
    if ($a -and $a.AutoScalingGroups -and $a.AutoScalingGroups.Count -gt 0) {
        $x = $a.AutoScalingGroups[0]
        Write-Ok "ASG $($script:AiASGName) Desired=$($x.DesiredCapacity) Min=$($x.MinSize) Max=$($x.MaxSize)"
    } else { Write-Warn "ASG $($script:AiASGName) not found" }
}
