# Evidence: describe current state and print table. ECR digest for video worker image included.
function Show-Evidence {
    param([string]$NetprobeJobId = "", [string]$NetprobeStatus = "")
    $R = $script:Region
    Write-Host "`n=== DEPLOY EVIDENCE ===" -ForegroundColor Cyan

    $ceV = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoCEName, "--region", $R, "--output", "json")
    $ceO = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:OpsCEName, "--region", $R, "--output", "json")
    $qV = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $R, "--output", "json")
    $qO = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $R, "--output", "json")
    $jd = Invoke-AwsJson @("batch", "describe-job-definitions", "--job-definition-name", $script:VideoJobDefName, "--status", "ACTIVE", "--region", $R, "--output", "json")
    $imgDigest = ""
    try {
        $di = Invoke-AwsJson @("ecr", "describe-images", "--repository-name", $script:VideoWorkerRepo, "--image-ids", "imageTag=latest", "--region", $R, "--output", "json")
        if ($di -and $di.imageDetails -and $di.imageDetails.Count -gt 0) {
            $imgDigest = $di.imageDetails[0].imageDigest
        }
    } catch {}

    $ev = [ordered]@{}
    if ($ceV -and $ceV.computeEnvironments -and $ceV.computeEnvironments.Count -gt 0) {
        $c = $ceV.computeEnvironments[0]
        $ev["Video CE"] = "$($c.computeEnvironmentArn) | status=$($c.status) state=$($c.state)"
    } else { $ev["Video CE"] = "not found" }
    if ($ceO -and $ceO.computeEnvironments -and $ceO.computeEnvironments.Count -gt 0) {
        $c = $ceO.computeEnvironments[0]
        $ev["Ops CE"] = "$($c.computeEnvironmentArn) | status=$($c.status) state=$($c.state)"
    } else { $ev["Ops CE"] = "not found" }
    if ($qV -and $qV.jobQueues -and $qV.jobQueues.Count -gt 0) {
        $q = $qV.jobQueues[0]
        $ev["Video Queue"] = "$($q.jobQueueArn) | state=$($q.state)"
    } else { $ev["Video Queue"] = "not found" }
    if ($qO -and $qO.jobQueues -and $qO.jobQueues.Count -gt 0) {
        $q = $qO.jobQueues[0]
        $ev["Ops Queue"] = "$($q.jobQueueArn) | state=$($q.state)"
    } else { $ev["Ops Queue"] = "not found" }
    if ($jd -and $jd.jobDefinitions -and $jd.jobDefinitions.Count -gt 0) {
        $j = $jd.jobDefinitions[0]
        $ev["Video JobDef"] = "$($j.jobDefinitionArn) | vcpus=$($j.containerProperties.vcpus) memory=$($j.containerProperties.memory)"
        if ($imgDigest) { $ev["Video image digest"] = $imgDigest }
    } else { $ev["Video JobDef"] = "not found" }

    $ruleR = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeReconcileRule, "--region", $R, "--output", "json")
    $ruleS = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeScanStuckRule, "--region", $R, "--output", "json")
    $ev["EventBridge reconcile"] = if ($ruleR) { "State=$($ruleR.State) Schedule=$($ruleR.ScheduleExpression)" } else { "not found" }
    $ev["EventBridge scan_stuck"] = if ($ruleS) { "State=$($ruleS.State) Schedule=$($ruleS.ScheduleExpression)" } else { "not found" }

    $ev["Netprobe jobId"] = $NetprobeJobId
    $ev["Netprobe status"] = $NetprobeStatus

    $asgM = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $script:MessagingASGName, "--region", $R, "--output", "json")
    $asgA = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $script:AiASGName, "--region", $R, "--output", "json")
    $ev["ASG Messaging"] = if ($asgM -and $asgM.AutoScalingGroups -and $asgM.AutoScalingGroups.Count -gt 0) {
        $a = $asgM.AutoScalingGroups[0]; "Desired=$($a.DesiredCapacity) Min=$($a.MinSize) Max=$($a.MaxSize)"
    } else { "not found" }
    $ev["ASG AI"] = if ($asgA -and $asgA.AutoScalingGroups -and $asgA.AutoScalingGroups.Count -gt 0) {
        $a = $asgA.AutoScalingGroups[0]; "Desired=$($a.DesiredCapacity) Min=$($a.MinSize) Max=$($a.MaxSize)"
    } else { "not found" }

    $addr = Invoke-AwsJson @("ec2", "describe-addresses", "--allocation-ids", $script:ApiAllocationId, "--region", $R, "--output", "json")
    $ev["API EIP"] = "$($script:ApiPublicIp) -> " + (if ($addr -and $addr.Addresses -and $addr.Addresses.Count -gt 0 -and $addr.Addresses[0].InstanceId) { $addr.Addresses[0].InstanceId } else { "no instance" })

    $ev.GetEnumerator() | ForEach-Object { Write-Host "  $($_.Key): $($_.Value)" -ForegroundColor Gray }
    Write-Host "=== END EVIDENCE ===`n" -ForegroundColor Cyan
}
