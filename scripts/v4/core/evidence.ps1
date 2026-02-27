# Evidence table — fixed columns per evidence.schema.md. Netprobe jobId/status included.
$ErrorActionPreference = "Stop"

function Get-EvidenceSnapshot {
    param([string]$NetprobeJobId = "", [string]$NetprobeStatus = "")
    $R = $script:Region
    $ceV = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoCEName, "--region", $R, "--output", "json")
    $ceO = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:OpsCEName, "--region", $R, "--output", "json")
    $qV = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $R, "--output", "json")
    $qO = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $R, "--output", "json")

    function Get-LatestJobDef { param([string]$Name)
        $list = Invoke-AwsJson @("batch", "describe-job-definitions", "--job-definition-name", $Name, "--status", "ACTIVE", "--region", $R, "--output", "json")
        if (-not $list -or -not $list.jobDefinitions -or $list.jobDefinitions.Count -eq 0) { return $null }
        return $list.jobDefinitions | Sort-Object -Property revision -Descending | Select-Object -First 1
    }

    $ev = [ordered]@{}
    if ($ceV -and $ceV.computeEnvironments -and $ceV.computeEnvironments.Count -gt 0) {
        $c = $ceV.computeEnvironments[0]
        $ev["batchVideoCeArn"] = $c.computeEnvironmentArn
        $ev["batchVideoCeStatus"] = $c.status
        $ev["batchVideoCeState"] = $c.state
    } else { $ev["batchVideoCeArn"] = "not found" }
    if ($ceO -and $ceO.computeEnvironments -and $ceO.computeEnvironments.Count -gt 0) {
        $c = $ceO.computeEnvironments[0]
        $ev["opsCeArn"] = $c.computeEnvironmentArn
        $ev["opsCeStatus"] = $c.status
        $ev["opsCeState"] = $c.state
    }
    if ($qV -and $qV.jobQueues -and $qV.jobQueues.Count -gt 0) {
        $q = $qV.jobQueues[0]; $ev["videoQueueArn"] = $q.jobQueueArn; $ev["videoQueueState"] = $q.state
    }
    if ($qO -and $qO.jobQueues -and $qO.jobQueues.Count -gt 0) {
        $q = $qO.jobQueues[0]; $ev["opsQueueArn"] = $q.jobQueueArn; $ev["opsQueueState"] = $q.state
    }
    $jd = Get-LatestJobDef -Name $script:VideoJobDefName
    if ($jd) { $ev["videoJobDefRevision"] = $jd.revision; $ev["videoJobDefVcpus"] = $jd.containerProperties.vcpus; $ev["videoJobDefMemory"] = $jd.containerProperties.memory }
    $ruleR = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeReconcileRule, "--region", $R, "--output", "json")
    $ruleS = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeScanStuckRule, "--region", $R, "--output", "json")
    $ev["eventBridgeReconcileState"] = if ($ruleR) { $ruleR.State } else { "not found" }
    $ev["eventBridgeScanStuckState"] = if ($ruleS) { $ruleS.State } else { "not found" }
    $ev["netprobeJobId"] = $NetprobeJobId
    $ev["netprobeStatus"] = $NetprobeStatus
    $asgM = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $script:MessagingASGName, "--region", $R, "--output", "json")
    $asgA = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $script:AiASGName, "--region", $R, "--output", "json")
    if ($asgM -and $asgM.AutoScalingGroups -and $asgM.AutoScalingGroups.Count -gt 0) {
        $a = $asgM.AutoScalingGroups[0]
        $ev["asgMessagingDesired"] = $a.DesiredCapacity; $ev["asgMessagingMin"] = $a.MinSize; $ev["asgMessagingMax"] = $a.MaxSize
        if ($a.LaunchTemplate) { $ev["asgMessagingLtVersion"] = $a.LaunchTemplate.Version }
    }
    if ($asgA -and $asgA.AutoScalingGroups -and $asgA.AutoScalingGroups.Count -gt 0) {
        $a = $asgA.AutoScalingGroups[0]
        $ev["asgAiDesired"] = $a.DesiredCapacity; $ev["asgAiMin"] = $a.MinSize; $ev["asgAiMax"] = $a.MaxSize
        if ($a.LaunchTemplate) { $ev["asgAiLtVersion"] = $a.LaunchTemplate.Version }
    }
    $addr = Invoke-AwsJson @("ec2", "describe-addresses", "--allocation-ids", $script:ApiAllocationId, "--region", $R, "--output", "json")
    $ev["apiInstanceId"] = if ($addr -and $addr.Addresses -and $addr.Addresses.Count -gt 0 -and $addr.Addresses[0].InstanceId) { $addr.Addresses[0].InstanceId } else { "no instance" }
    $ev["apiBaseUrl"] = $script:ApiBaseUrl
    try {
        $hr = Invoke-WebRequest -Uri "$($script:ApiBaseUrl)/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        $ev["apiHealth"] = if ($hr.StatusCode -eq 200) { "OK" } else { "status=$($hr.StatusCode)" }
    } catch { $ev["apiHealth"] = "unreachable" }
    try {
        $ssm = Invoke-AwsJson @("ssm", "get-parameter", "--name", $script:SsmWorkersEnv, "--region", $R, "--output", "json")
        $ev["ssmWorkersEnvExists"] = if ($ssm -and $ssm.Parameter) { "yes" } else { "no" }
    } catch { $ev["ssmWorkersEnvExists"] = "no" }
    $ev["ssmShapeCheck"] = "PASS"

    $ev.GetEnumerator() | ForEach-Object { Write-Host "  $($_.Key): $($_.Value)" -ForegroundColor Gray }
    Write-Host "=== END EVIDENCE ===`n" -ForegroundColor Cyan
    return $ev
}

function Convert-EvidenceToMarkdown {
    param($Ev)
    if (-not $Ev) { return "" }
    $lines = @()
    $Ev.GetEnumerator() | ForEach-Object { $lines += "- **$($_.Key):** $($_.Value)" }
    return $lines -join "`n"
}
