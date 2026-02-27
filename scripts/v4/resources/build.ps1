# Build: Tag existence check. Validate only.
function Confirm-BuildInstance {
    Write-Step "Build ($($script:BuildTagKey)=$($script:BuildTagValue))"
    if ($script:PlanMode) { Write-Ok "Build check skipped (Plan)"; return }
    $res = Invoke-AwsJson @("ec2", "describe-instances", "--filters", "Name=tag:$($script:BuildTagKey),Values=$($script:BuildTagValue)", "Name=instance-state-name,Values=running,pending,stopped", "--region", $script:Region, "--output", "json")
    $inst = $null
    if ($res -and $res.Reservations -and $res.Reservations.Count -gt 0) {
        $inst = $res.Reservations[0].Instances | Select-Object -First 1
    }
    if (-not $inst) {
        Write-Warn "Build instance ($($script:BuildTagValue)) not found"
        return
    }
    Write-Ok "Build InstanceId=$($inst.InstanceId) State=$($inst.State.Name)"
}
