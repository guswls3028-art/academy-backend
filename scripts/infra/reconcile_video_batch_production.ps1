param(
    [string]$Region = "ap-northeast-2",
    [string]$VideoCEName = "academy-video-batch-ce-v2",
    [string]$VideoQueueName = "academy-video-batch-queue",
    [string]$OpsCEName = "academy-video-ops-ce",
    [string]$OpsQueueName = "academy-video-ops-queue",
    [string]$VideoJobDefName = "academy-video-batch-jobdef",
    [string]$OpsReconcileJobDefName = "academy-video-ops-reconcile",
    [string]$ReconcileRuleName = "academy-reconcile-video-jobs",
    [string]$RunnableAlarmName = "academy-video-QueueRunnable"
)
$ErrorActionPreference = "Stop"
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
function ExecJson($argsArray) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @argsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    $str = ($out | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($str)) { return $null }
    try { return $str | ConvertFrom-Json } catch { return $null }
}
function Invoke-Aws($ArgsArray, $ErrorMessage = "AWS failed") {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @ArgsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) {
        $txt = ($out | Out-String).Trim()
        Write-Error "$ErrorMessage. ExitCode=$exit. $txt"
    }
    return $out
}
$videoCeList = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json")
$videoCe = $null
if ($videoCeList -and $videoCeList.computeEnvironments) {
    $videoCe = $videoCeList.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1
}
$opsCeList = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $OpsCEName, "--region", $Region, "--output", "json")
$opsCe = $null
if ($opsCeList -and $opsCeList.computeEnvironments) {
    $opsCe = $opsCeList.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $OpsCEName } | Select-Object -First 1
}
$videoJqList = ExecJson @("batch", "describe-job-queues", "--job-queues", $VideoQueueName, "--region", $Region, "--output", "json")
$videoQueueArn = $null
$videoQueue = $null
if ($videoJqList -and $videoJqList.jobQueues) {
    $videoQueue = $videoJqList.jobQueues | Where-Object { $_.jobQueueName -eq $VideoQueueName } | Select-Object -First 1
    if ($videoQueue) { $videoQueueArn = $videoQueue.jobQueueArn }
}
$opsJqList = ExecJson @("batch", "describe-job-queues", "--job-queues", $OpsQueueName, "--region", $Region, "--output", "json")
$opsQueueArn = $null
if ($opsJqList -and $opsJqList.jobQueues) {
    $oq = $opsJqList.jobQueues | Where-Object { $_.jobQueueName -eq $OpsQueueName } | Select-Object -First 1
    if ($oq) { $opsQueueArn = $oq.jobQueueArn }
}
if (-not $videoCe) { Write-Error "Video CE $VideoCEName not found"; exit 1 }
if (-not $opsCe) { Write-Error "Ops CE $OpsCEName not found"; exit 1 }
if (-not $videoQueueArn) { Write-Error "Video queue $VideoQueueName not found"; exit 1 }
if (-not $opsQueueArn) { Write-Error "Ops queue $OpsQueueName not found"; exit 1 }
$crV = $videoCe.computeResources
$desiredV = [int]$crV.desiredvCpus
$runningV = (ExecJson @("batch", "list-jobs", "--job-queue", $videoQueueArn, "--job-status", "RUNNING", "--region", $Region, "--query", "length(jobSummaryList)", "--output", "json")) -as [int]
if (-not $runningV) { $runningV = 0 }
if ($desiredV -gt 0 -and $runningV -eq 0) { Write-Warning "Video CE desiredvCpus=$desiredV but no RUNNING jobs on Video queue" }
$minV = [int]$crV.minvCpus
$maxV = [int]$crV.maxvCpus
$allocV = $crV.allocationStrategy
if ($minV -ne 0 -or $maxV -ne 32 -or $allocV -ne "BEST_FIT_PROGRESSIVE") {
    Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $VideoCEName, "--compute-resources", "minvCpus=0,maxvCpus=32", "--region", $Region) -ErrorMessage "update Video CE failed"
    $wait = 0
    while ($wait -lt 90) {
        Start-Sleep -Seconds 5
        $wait += 5
        $v = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json")
        $ve = $v.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1
        if ($ve.status -eq "VALID") { break }
        if ($ve.status -eq "INVALID") { Write-Error "Video CE INVALID"; exit 1 }
    }
}
$crO = $opsCe.computeResources
$maxO = [int]$crO.maxvCpus
if ($maxO -ne 1) {
    Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $OpsCEName, "--compute-resources", "minvCpus=0,maxvCpus=1", "--region", $Region) -ErrorMessage "update Ops CE failed"
    $wait = 0
    while ($wait -lt 90) {
        Start-Sleep -Seconds 5
        $wait += 5
        $o = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $OpsCEName, "--region", $Region, "--output", "json")
        $oe = $o.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $OpsCEName } | Select-Object -First 1
        if ($oe.status -eq "VALID") { break }
        if ($oe.status -eq "INVALID") { Write-Error "Ops CE INVALID"; exit 1 }
    }
}
$videoJdAll = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $VideoJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
$videoJdLatest = $null
if ($videoJdAll -and $videoJdAll.jobDefinitions -and $videoJdAll.jobDefinitions.Count -gt 0) {
    $videoJdLatest = $videoJdAll.jobDefinitions | Sort-Object { [int]$_.revision } -Descending | Select-Object -First 1
}
$needVideoJdRegister = $false
if ($videoJdLatest) {
    $mem = [int]$videoJdLatest.containerProperties.memory
    $vcpus = [int]$videoJdLatest.containerProperties.vcpus
    $timeoutSec = $null
    if ($videoJdLatest.timeout -and $videoJdLatest.timeout.attemptDurationSeconds) { $timeoutSec = [int]$videoJdLatest.timeout.attemptDurationSeconds }
    $rp = $videoJdLatest.containerProperties.runtimePlatform
    $arm = ($rp -and $rp.cpuArchitecture -eq "ARM64")
    if ($mem -eq 4096 -or $mem -ne 3072 -or $vcpus -ne 2 -or $timeoutSec -ne 14400 -or -not $arm) { $needVideoJdRegister = $true }
}
if ($needVideoJdRegister -and $videoJdLatest) {
    $illegal = @("revision", "status", "jobDefinitionArn", "containerOrchestrationType")
    $regObj = @{}
    foreach ($key in $videoJdLatest.PSObject.Properties.Name) {
        if ($key -notin $illegal) { $regObj[$key] = $videoJdLatest.$key }
    }
    $regObj.containerProperties.memory = 3072
    $regObj.containerProperties.vcpus = 2
    if (-not $regObj.containerProperties.runtimePlatform) { $regObj.containerProperties | Add-Member -NotePropertyName "runtimePlatform" -NotePropertyValue @{ cpuArchitecture = "ARM64" } -Force }
    else { $regObj.containerProperties.runtimePlatform = @{ cpuArchitecture = "ARM64" } }
    if (-not $regObj.timeout) { $regObj | Add-Member -NotePropertyName "timeout" -NotePropertyValue @{ attemptDurationSeconds = 14400 } -Force }
    else { $regObj.timeout = @{ attemptDurationSeconds = 14400 } }
    $jdPath = Join-Path $env:TEMP "reconcile_video_jd_$(Get-Date -Format 'yyyyMMddHHmmss').json"
    $jsonStr = $regObj | ConvertTo-Json -Depth 25 -Compress:$false
    $jsonStr = $jsonStr -replace '"JobDefinitionName"', '"jobDefinitionName"' -replace '"ContainerProperties"', '"containerProperties"' -replace '"Memory":', '"memory":' -replace '"Vcpus":', '"vcpus":' -replace '"Image":', '"image":' -replace '"Command":', '"command":' -replace '"JobRoleArn":', '"jobRoleArn":' -replace '"ExecutionRoleArn":', '"executionRoleArn"' -replace '"ResourceRequirements":', '"resourceRequirements"' -replace '"LogConfiguration":', '"logConfiguration"' -replace '"RuntimePlatform":', '"runtimePlatform"' -replace '"CpuArchitecture":', '"cpuArchitecture"' -replace '"Timeout"', '"timeout"' -replace '"AttemptDurationSeconds"', '"attemptDurationSeconds"' -replace '"PlatformCapabilities"', '"platformCapabilities"' -replace '"Parameters"', '"parameters"' -replace '"RetryStrategy"', '"retryStrategy"' -replace '"Attempts":', '"attempts":' -replace '(\s)"Type":', '$1"type":'
    $jsonStr = $jsonStr -replace '"LogDriver":', '"logDriver":' -replace '"Options":', '"options"' -replace '"Awslogs-group":', '"awslogs-group":' -replace '"Awslogs-region":', '"awslogs-region":' -replace '"Awslogs-stream-prefix":', '"awslogs-stream-prefix":'
    [System.IO.File]::WriteAllText($jdPath, $jsonStr, [System.Text.UTF8Encoding]::new($false))
    $uri = "file:///" + ([System.IO.Path]::GetFullPath($jdPath) -replace '\\', '/')
    $regOut = Invoke-Aws @("batch", "register-job-definition", "--cli-input-json", $uri, "--region", $Region, "--output", "json") -ErrorMessage "register Video job def failed"
    Remove-Item $jdPath -Force -ErrorAction SilentlyContinue
    $newRev = $null; if ($regOut -and $regOut.revision) { $newRev = [int]$regOut.revision }
    $videoJdAllAfter = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $VideoJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
    if ($videoJdAllAfter -and $videoJdAllAfter.jobDefinitions) {
        foreach ($d in $videoJdAllAfter.jobDefinitions) {
            if ([int]$d.containerProperties.memory -eq 4096 -and ($null -eq $newRev -or [int]$d.revision -ne $newRev)) {
                & aws batch deregister-job-definition --job-definition "$VideoJobDefName:$($d.revision)" --region $Region 2>&1 | Out-Null
            }
        }
    }
}
$opsJdAll = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $OpsReconcileJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
$opsJdLatest = $null
if ($opsJdAll -and $opsJdAll.jobDefinitions -and $opsJdAll.jobDefinitions.Count -gt 0) {
    $opsJdLatest = $opsJdAll.jobDefinitions | Sort-Object { [int]$_.revision } -Descending | Select-Object -First 1
}
$needOpsJdRegister = $false
if ($opsJdLatest) {
    $memO = [int]$opsJdLatest.containerProperties.memory
    $vcpusO = [int]$opsJdLatest.containerProperties.vcpus
    $timeoutO = $null
    if ($opsJdLatest.timeout -and $opsJdLatest.timeout.attemptDurationSeconds) { $timeoutO = [int]$opsJdLatest.timeout.attemptDurationSeconds }
    $rpO = $opsJdLatest.containerProperties.runtimePlatform
    $armO = ($rpO -and $rpO.cpuArchitecture -eq "ARM64")
    if ($memO -ne 1024 -or $vcpusO -ne 1 -or $timeoutO -ne 300 -or -not $armO) { $needOpsJdRegister = $true }
}
if ($needOpsJdRegister -and $opsJdLatest) {
    $illegal = @("revision", "status", "jobDefinitionArn", "containerOrchestrationType")
    $regO = @{}
    foreach ($key in $opsJdLatest.PSObject.Properties.Name) {
        if ($key -notin $illegal) { $regO[$key] = $opsJdLatest.$key }
    }
    $regO.containerProperties.memory = 1024
    $regO.containerProperties.vcpus = 1
    if (-not $regO.containerProperties.runtimePlatform) { $regO.containerProperties | Add-Member -NotePropertyName "runtimePlatform" -NotePropertyValue @{ cpuArchitecture = "ARM64" } -Force }
    else { $regO.containerProperties.runtimePlatform = @{ cpuArchitecture = "ARM64" } }
    $regO.timeout = @{ attemptDurationSeconds = 300 }
    $jdPathO = Join-Path $env:TEMP "reconcile_ops_jd_$(Get-Date -Format 'yyyyMMddHHmmss').json"
    $jsonStrO = $regO | ConvertTo-Json -Depth 25 -Compress:$false
    $jsonStrO = $jsonStrO -replace '"JobDefinitionName"', '"jobDefinitionName"' -replace '"ContainerProperties"', '"containerProperties"' -replace '"Memory":', '"memory":' -replace '"Vcpus":', '"vcpus":' -replace '"Timeout"', '"timeout"' -replace '"AttemptDurationSeconds"', '"attemptDurationSeconds"' -replace '"RuntimePlatform"', '"runtimePlatform"' -replace '"CpuArchitecture"', '"cpuArchitecture"' -replace '"Image":', '"image":' -replace '"Command":', '"command":' -replace '"JobRoleArn":', '"jobRoleArn":' -replace '"ExecutionRoleArn":', '"executionRoleArn"' -replace '"LogConfiguration":', '"logConfiguration"' -replace '"PlatformCapabilities"', '"platformCapabilities"' -replace '"RetryStrategy"', '"retryStrategy"'
    $jsonStrO = $jsonStrO -replace '"LogDriver":', '"logDriver":' -replace '"Options":', '"options"' -replace '"Awslogs-group":', '"awslogs-group":' -replace '"Awslogs-region":', '"awslogs-region":' -replace '"Awslogs-stream-prefix":', '"awslogs-stream-prefix":'
    [System.IO.File]::WriteAllText($jdPathO, $jsonStrO, [System.Text.UTF8Encoding]::new($false))
    $uriO = "file:///" + ([System.IO.Path]::GetFullPath($jdPathO) -replace '\\', '/')
    Invoke-Aws @("batch", "register-job-definition", "--cli-input-json", $uriO, "--region", $Region, "--output", "json") -ErrorMessage "register Ops reconcile job def failed"
    Remove-Item $jdPathO -Force -ErrorAction SilentlyContinue
    $opsJdAllAfter = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $OpsReconcileJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
    if ($opsJdAllAfter -and $opsJdAllAfter.jobDefinitions) {
        foreach ($d in $opsJdAllAfter.jobDefinitions) {
            $m = [int]$d.containerProperties.memory; $v = [int]$d.containerProperties.vcpus; $to = [int]$d.timeout.attemptDurationSeconds
            if ($m -ne 1024 -or $v -ne 1 -or $to -ne 300) {
                & aws batch deregister-job-definition --job-definition "$OpsReconcileJobDefName:$($d.revision)" --region $Region 2>&1 | Out-Null
            }
        }
    }
}
$rule = ExecJson @("events", "describe-rule", "--name", $ReconcileRuleName, "--region", $Region, "--output", "json")
$ruleExists = ($rule -and $rule.Name -eq $ReconcileRuleName)
if (-not $ruleExists) {
    Invoke-Aws @("events", "put-rule", "--name", $ReconcileRuleName, "--schedule-expression", "rate(5 minutes)", "--state", "ENABLED", "--description", "Reconcile video jobs", "--region", $Region) -ErrorMessage "put-rule failed"
}
else {
    if ($rule.ScheduleExpression -notmatch "rate\s*\(\s*5\s*minute") {
        Invoke-Aws @("events", "put-rule", "--name", $ReconcileRuleName, "--schedule-expression", "rate(5 minutes)", "--state", "ENABLED", "--region", $Region) -ErrorMessage "put-rule schedule failed"
    }
}
$tgtList = ExecJson @("events", "list-targets-by-rule", "--rule", $ReconcileRuleName, "--region", $Region, "--output", "json")
$tgtCorrect = $false
if ($tgtList -and $tgtList.Targets -and $tgtList.Targets.Count -gt 0) {
    $t = $tgtList.Targets[0]
    if ($t.Arn -eq $opsQueueArn -and $t.BatchParameters -and $t.BatchParameters.JobDefinition -like "${OpsReconcileJobDefName}*") { $tgtCorrect = $true }
}
if (-not $tgtCorrect) {
    $EventsRoleName = "academy-eventbridge-batch-video-role"
    $roleResp = ExecJson @("iam", "get-role", "--role-name", $EventsRoleName, "--output", "json")
    if (-not $roleResp -or -not $roleResp.Role) { Write-Error "EventBridge role $EventsRoleName not found"; exit 1 }
    $eventsRoleArn = $roleResp.Role.Arn
    $targetsJson = '[{"Id":"1","Arn":"' + $opsQueueArn + '","RoleArn":"' + $eventsRoleArn + '","BatchParameters":{"JobDefinition":"' + $OpsReconcileJobDefName + '","JobName":"reconcile-video-jobs"}}]'
    Invoke-Aws @("events", "put-targets", "--rule", $ReconcileRuleName, "--targets", $targetsJson, "--region", $Region) -ErrorMessage "put-targets failed"
}
$alarmList = ExecJson @("cloudwatch", "describe-alarms", "--alarm-names", $RunnableAlarmName, "--region", $Region, "--output", "json")
$alarmExists = ($alarmList -and $alarmList.MetricAlarms -and $alarmList.MetricAlarms.Count -gt 0)
$dimensionsJson = "Name=JobQueue,Value=$videoQueueArn"
if (-not $alarmExists) {
    & aws cloudwatch put-metric-alarm --alarm-name $RunnableAlarmName --alarm-description "Video queue RUNNABLE > 0" --namespace AWS/Batch --metric-name RUNNABLE --dimensions $dimensionsJson --statistic Average --period 300 --evaluation-periods 1 --threshold 0 --comparison-operator GreaterThanThreshold --treat-missing-data notBreaching --region $Region 2>&1 | Out-Null
}
else {
    $a = $alarmList.MetricAlarms[0]
    if ([int]$a.Threshold -ne 0 -or $a.ComparisonOperator -ne "GreaterThanThreshold") {
        & aws cloudwatch put-metric-alarm --alarm-name $RunnableAlarmName --alarm-description "Video queue RUNNABLE > 0" --namespace AWS/Batch --metric-name RUNNABLE --dimensions $dimensionsJson --statistic Average --period 300 --evaluation-periods 1 --threshold 0 --comparison-operator GreaterThanThreshold --treat-missing-data notBreaching --region $Region 2>&1 | Out-Null
    }
}
$videoCeList2 = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json")
$videoCe2 = $videoCeList2.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1
$opsCeList2 = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $OpsCEName, "--region", $Region, "--output", "json")
$opsCe2 = $opsCeList2.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $OpsCEName } | Select-Object -First 1
$videoJdAll2 = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $VideoJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
$videoJdLatest2 = $videoJdAll2.jobDefinitions | Sort-Object { [int]$_.revision } -Descending | Select-Object -First 1
$opsJdAll2 = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $OpsReconcileJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
$opsJdLatest2 = $opsJdAll2.jobDefinitions | Sort-Object { [int]$_.revision } -Descending | Select-Object -First 1
$rule2 = ExecJson @("events", "describe-rule", "--name", $ReconcileRuleName, "--region", $Region, "--output", "json")
$tgtList2 = ExecJson @("events", "list-targets-by-rule", "--rule", $ReconcileRuleName, "--region", $Region, "--output", "json")
$alarmList2 = ExecJson @("cloudwatch", "describe-alarms", "--alarm-names", $RunnableAlarmName, "--region", $Region, "--output", "json")
$fail = $false
if ($videoCe2.status -ne "VALID" -or $videoCe2.state -ne "ENABLED") { Write-Error "Video CE state=$($videoCe2.state) status=$($videoCe2.status)"; $fail = $true }
if ($opsCe2.status -ne "VALID" -or $opsCe2.state -ne "ENABLED") { Write-Error "Ops CE state=$($opsCe2.state) status=$($opsCe2.status)"; $fail = $true }
$crV2 = $videoCe2.computeResources
if ([int]$crV2.minvCpus -ne 0 -or [int]$crV2.maxvCpus -ne 32) { Write-Error "Video CE min/max vCpus mismatch"; $fail = $true }
if ([int]$opsCe2.computeResources.maxvCpus -ne 1) { Write-Error "Ops CE maxvCpus != 1"; $fail = $true }
if ($videoJdLatest2) {
    if ([int]$videoJdLatest2.containerProperties.memory -ne 3072) { Write-Error "Video JobDef memory != 3072"; $fail = $true }
    if ([int]$videoJdLatest2.containerProperties.vcpus -ne 2) { Write-Error "Video JobDef vcpus != 2"; $fail = $true }
    if ($videoJdLatest2.timeout.attemptDurationSeconds -ne 14400) { Write-Error "Video JobDef timeout != 14400"; $fail = $true }
    if (-not $videoJdLatest2.containerProperties.runtimePlatform -or $videoJdLatest2.containerProperties.runtimePlatform.cpuArchitecture -ne "ARM64") { Write-Error "Video JobDef not ARM64"; $fail = $true }
}
else { Write-Error "Video JobDef not found"; $fail = $true }
if ($opsJdLatest2) {
    if ([int]$opsJdLatest2.containerProperties.memory -ne 1024) { Write-Error "Ops JobDef memory != 1024"; $fail = $true }
    if ([int]$opsJdLatest2.containerProperties.vcpus -ne 1) { Write-Error "Ops JobDef vcpus != 1"; $fail = $true }
    if ($opsJdLatest2.timeout.attemptDurationSeconds -ne 300) { Write-Error "Ops JobDef timeout != 300"; $fail = $true }
}
else { Write-Error "Ops reconcile JobDef not found"; $fail = $true }
if (-not $rule2 -or $rule2.Name -ne $ReconcileRuleName) { Write-Error "EventBridge rule missing"; $fail = $true }
if ($rule2.ScheduleExpression -notmatch "rate\s*\(\s*5\s*minute") { Write-Error "Reconcile rule schedule not rate(5 minutes)"; $fail = $true }
$tgtOk = $false
if ($tgtList2 -and $tgtList2.Targets -and $tgtList2.Targets.Count -gt 0) {
    $t0 = $tgtList2.Targets[0]
    if ($t0.Arn -eq $opsQueueArn -and $t0.BatchParameters.JobDefinition -like "${OpsReconcileJobDefName}*") { $tgtOk = $true }
}
if (-not $tgtOk) { Write-Error "EventBridge target not Ops queue or wrong job def"; $fail = $true }
if (-not $alarmList2 -or -not $alarmList2.MetricAlarms -or $alarmList2.MetricAlarms.Count -eq 0) { Write-Error "CloudWatch RUNNABLE alarm missing"; $fail = $true }
else {
    if ([int]$alarmList2.MetricAlarms[0].Threshold -ne 0 -or $alarmList2.MetricAlarms[0].ComparisonOperator -ne "GreaterThanThreshold") { Write-Error "RUNNABLE alarm threshold not > 0"; $fail = $true }
}
if ($fail) { exit 1 }
