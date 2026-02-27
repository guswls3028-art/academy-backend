# Evidence: SSOT state-contract table. CE/Queue/JobDef(revision+digest)/EventBridge/ASG/API/SSM/Netprobe.
# Legacy residues: list resource names from describe/list that are not in SSOT.
function Get-SSOTLegacyResidues {
    $R = $script:Region
    $legacy = [ordered]@{}
    $ssotCE = @($script:VideoCEName, $script:OpsCEName)
    $ssotQueue = @($script:VideoQueueName, $script:OpsQueueName)
    $ssotRules = @($script:EventBridgeReconcileRule, $script:EventBridgeScanStuckRule)

    $ces = Invoke-AwsJson @("batch", "describe-compute-environments", "--region", $R, "--output", "json")
    if ($ces -and $ces.computeEnvironments) {
        $other = $ces.computeEnvironments | Where-Object { $_.computeEnvironmentName -notin $ssotCE } | ForEach-Object { $_.computeEnvironmentName }
        if ($other) { $legacy["Batch CE"] = $other }
    }
    $queues = Invoke-AwsJson @("batch", "describe-job-queues", "--region", $R, "--output", "json")
    if ($queues -and $queues.jobQueues) {
        $other = $queues.jobQueues | Where-Object { $_.jobQueueName -notin $ssotQueue } | ForEach-Object { $_.jobQueueName }
        if ($other) { $legacy["Batch Queue"] = $other }
    }
    $rules = Invoke-AwsJson @("events", "list-rules", "--region", $R, "--output", "json")
    if ($rules -and $rules.Rules) {
        $other = $rules.Rules | Where-Object { $_.Name -notin $ssotRules } | ForEach-Object { $_.Name }
        if ($other) { $legacy["EventBridge Rule"] = $other }
    }
    return $legacy
}

function Show-Evidence {
    param([string]$NetprobeJobId = "", [string]$NetprobeStatus = "")
    $R = $script:Region
    Write-Host "`n=== DEPLOY EVIDENCE ===" -ForegroundColor Cyan

    $ceV = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoCEName, "--region", $R, "--output", "json")
    $ceO = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:OpsCEName, "--region", $R, "--output", "json")
    $qV = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $R, "--output", "json")
    $qO = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $R, "--output", "json")

    $imgDigest = ""
    try {
        $di = Invoke-AwsJson @("ecr", "describe-images", "--repository-name", $script:VideoWorkerRepo, "--image-ids", "imageTag=latest", "--region", $R, "--output", "json")
        if ($di -and $di.imageDetails -and $di.imageDetails.Count -gt 0) {
            $imgDigest = $di.imageDetails[0].imageDigest
        }
    } catch {}

    function Get-LatestJobDef {
        param([string]$Name)
        $list = Invoke-AwsJson @("batch", "describe-job-definitions", "--job-definition-name", $Name, "--status", "ACTIVE", "--region", $R, "--output", "json")
        if (-not $list -or -not $list.jobDefinitions -or $list.jobDefinitions.Count -eq 0) { return $null }
        return $list.jobDefinitions | Sort-Object -Property revision -Descending | Select-Object -First 1
    }

    $ev = [ordered]@{}
    if ($ceV -and $ceV.computeEnvironments -and $ceV.computeEnvironments.Count -gt 0) {
        $c = $ceV.computeEnvironments[0]
        $ev["Video CE"] = "$($c.computeEnvironmentArn) | status=$($c.status) state=$($c.state)"
    } else { $ev["Video CE"] = "not found" }
    if ($ceO -and $ceO.computeEnvironments -and $ceO.computeEnvironments.Count -gt 0) {
        $c = $ceO.computeEnvironments[0]
        $inst = ""; $maxV = ""
        if ($c.computeResources) { $inst = ($c.computeResources.instanceTypes -join ","); $maxV = $c.computeResources.maxvCpus }
        $ev["Ops CE"] = "$($c.computeEnvironmentArn) | status=$($c.status) state=$($c.state) instanceTypes=$inst maxvCpus=$maxV"
    } else { $ev["Ops CE"] = "not found" }
    if ($qV -and $qV.jobQueues -and $qV.jobQueues.Count -gt 0) {
        $q = $qV.jobQueues[0]
        $ev["Video Queue"] = "$($q.jobQueueArn) | state=$($q.state)"
    } else { $ev["Video Queue"] = "not found" }
    if ($qO -and $qO.jobQueues -and $qO.jobQueues.Count -gt 0) {
        $q = $qO.jobQueues[0]
        $ev["Ops Queue"] = "$($q.jobQueueArn) | state=$($q.state)"
    } else { $ev["Ops Queue"] = "not found" }

    foreach ($jdn in @($script:VideoJobDefName, $script:OpsJobDefReconcile, $script:OpsJobDefScanStuck, $script:OpsJobDefNetprobe)) {
        $j = Get-LatestJobDef -Name $jdn
        if ($j) {
            $img = $j.containerProperties.image
            $rev = $j.revision
            $ev["JobDef $jdn"] = "ARN=$($j.jobDefinitionArn) | revision=$rev | image=$img"
        } else { $ev["JobDef $jdn"] = "not found" }
    }
    if ($imgDigest) { $ev["ECR imageDigest (video-worker:latest)"] = $imgDigest }

    $ruleR = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeReconcileRule, "--region", $R, "--output", "json")
    $ruleS = Invoke-AwsJson @("events", "describe-rule", "--name", $script:EventBridgeScanStuckRule, "--region", $R, "--output", "json")
    $ev["EventBridge reconcile"] = $(if ($ruleR) { "State=$($ruleR.State) Schedule=$($ruleR.ScheduleExpression)" } else { "not found" })
    $ev["EventBridge scan_stuck"] = $(if ($ruleS) { "State=$($ruleS.State) Schedule=$($ruleS.ScheduleExpression)" } else { "not found" })
    try {
        $tarR = Invoke-AwsJson @("events", "list-targets-by-rule", "--rule", $script:EventBridgeReconcileRule, "--region", $R, "--output", "json")
        if ($tarR -and $tarR.Targets -and $tarR.Targets.Count -gt 0) {
            $t = $tarR.Targets[0]; $ev["EventBridge reconcile target"] = "Queue=$($t.Arn) JobDef=$($t.BatchParameters.JobDefinition)"
        }
        $tarS = Invoke-AwsJson @("events", "list-targets-by-rule", "--rule", $script:EventBridgeScanStuckRule, "--region", $R, "--output", "json")
        if ($tarS -and $tarS.Targets -and $tarS.Targets.Count -gt 0) {
            $t = $tarS.Targets[0]; $ev["EventBridge scan_stuck target"] = "Queue=$($t.Arn) JobDef=$($t.BatchParameters.JobDefinition)"
        }
    } catch {}

    $ev["Netprobe jobId"] = $NetprobeJobId
    $ev["Netprobe status"] = $NetprobeStatus

    $asgM = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $script:MessagingASGName, "--region", $R, "--output", "json")
    $asgA = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $script:AiASGName, "--region", $R, "--output", "json")
    $ev["ASG Messaging"] = $(if ($asgM -and $asgM.AutoScalingGroups -and $asgM.AutoScalingGroups.Count -gt 0) {
        $a = $asgM.AutoScalingGroups[0]; "Desired=$($a.DesiredCapacity) Min=$($a.MinSize) Max=$($a.MaxSize)"
    } else { "not found" })
    $ev["ASG AI"] = $(if ($asgA -and $asgA.AutoScalingGroups -and $asgA.AutoScalingGroups.Count -gt 0) {
        $a = $asgA.AutoScalingGroups[0]; "Desired=$($a.DesiredCapacity) Min=$($a.MinSize) Max=$($a.MaxSize)"
    } else { "not found" })

    $addr = Invoke-AwsJson @("ec2", "describe-addresses", "--allocation-ids", $script:ApiAllocationId, "--region", $R, "--output", "json")
    $apiEipVal = $(if ($addr -and $addr.Addresses -and $addr.Addresses.Count -gt 0 -and $addr.Addresses[0].InstanceId) { $addr.Addresses[0].InstanceId } else { "no instance" })
    $ev["API EIP"] = "$($script:ApiPublicIp) -> " + $apiEipVal

    try {
        $ssm = Invoke-AwsJson @("ssm", "get-parameter", "--name", $script:SsmWorkersEnv, "--region", $R, "--output", "json")
        $ev["SSM env"] = $(if ($ssm -and $ssm.Parameter) { "exists" } else { "not found" })
    } catch { $ev["SSM env"] = "not found" }
    try {
        $apiHealth = Invoke-WebRequest -Uri "$($script:ApiBaseUrl)/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        $ev["API health"] = $(if ($apiHealth.StatusCode -eq 200) { "OK" } else { "status=$($apiHealth.StatusCode)" })
    } catch { $ev["API health"] = "unreachable or error" }

    $ev.GetEnumerator() | ForEach-Object { Write-Host "  $($_.Key): $($_.Value)" -ForegroundColor Gray }

    $legacy = Get-SSOTLegacyResidues
    if ($legacy -and $legacy.Count -gt 0) {
        Write-Host "`n  --- Legacy residues (non-SSOT) ---" -ForegroundColor Yellow
        $legacy.GetEnumerator() | ForEach-Object {
            Write-Host "  $($_.Key): $(($_.Value) -join ', ')" -ForegroundColor Gray
        }
    }

    Write-Host "=== END EVIDENCE ===`n" -ForegroundColor Cyan
}
