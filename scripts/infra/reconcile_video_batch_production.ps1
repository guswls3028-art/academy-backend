param(
    [string]$Region = "ap-northeast-2",
    [string]$VideoCEName = "academy-video-batch-ce-v2",
    [string]$VideoQueueName = "academy-video-batch-queue",
    [string]$OpsCEName = "academy-video-ops-ce",
    [string]$OpsQueueName = "academy-video-ops-queue",
    [string]$VideoJobDefName = "academy-video-batch-jobdef",
    [string]$OpsJobDefName = "academy-video-ops-jobdef",
    [string]$ReconcileRuleName = "academy-reconcile-video-jobs",
    [string]$RunnableAlarmName = "academy-video-QueueRunnable"
)
$ErrorActionPreference = "Stop"
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
function ExecJson($argsArray) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @argsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    if (-not $out) { return $null }
    $str = ($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($str)) { return $null }
    try { return $str | ConvertFrom-Json } catch { return $null }
}
function Invoke-Aws {
    param([string[]]$ArgsArray, [string]$ErrorMessage = "AWS failed")
    $prevErr = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @ArgsArray 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevErr
    if ($exitCode -ne 0) {
        $text = ($out | Out-String).Trim()
        throw "${ErrorMessage}. ExitCode=$exitCode. $text"
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
if (-not $videoCe) { Write-Error "Video CE $VideoCEName not found"; exit 1 }
if (-not $opsCe) { Write-Error "Ops CE $OpsCEName not found"; exit 1 }
$crV = $videoCe.computeResources
if (-not $crV.subnets -or $crV.subnets.Count -eq 0) { Write-Error "Video CE has no subnets; required network resources missing"; exit 1 }
if (-not $crV.securityGroupIds -or $crV.securityGroupIds.Count -eq 0) { Write-Error "Video CE has no securityGroupIds; required network resources missing"; exit 1 }
$crO = $opsCe.computeResources
if (-not $crO.subnets -or $crO.subnets.Count -eq 0) { Write-Error "Ops CE has no subnets; required network resources missing"; exit 1 }
if (-not $crO.securityGroupIds -or $crO.securityGroupIds.Count -eq 0) { Write-Error "Ops CE has no securityGroupIds; required network resources missing"; exit 1 }
$videoCeImageType = $null
if ($crV.ec2Configuration -and $crV.ec2Configuration.Count -gt 0 -and $crV.ec2Configuration[0].imageType) {
    $videoCeImageType = $crV.ec2Configuration[0].imageType
}
$videoCeImageOk = ($videoCeImageType -eq "ECS_AL2023_ARM64" -or $videoCeImageType -eq "ECS_AL2023")
if ($videoCeImageType -and -not $videoCeImageOk) {
    Write-Error "Video CE imageType is $videoCeImageType; ECS_AL2023 or ECS_AL2023_ARM64 required and cannot be changed in-place"; exit 1
}
$opsCeImageType = $null
if ($crO.ec2Configuration -and $crO.ec2Configuration.Count -gt 0 -and $crO.ec2Configuration[0].imageType) {
    $opsCeImageType = $crO.ec2Configuration[0].imageType
}
$opsCeImageOk = ($opsCeImageType -eq "ECS_AL2023_ARM64" -or $opsCeImageType -eq "ECS_AL2023")
if ($opsCeImageType -and -not $opsCeImageOk) {
    Write-Error "Ops CE imageType is $opsCeImageType; ECS_AL2023 or ECS_AL2023_ARM64 required"; exit 1
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
if (-not $videoQueueArn) { Write-Error "Video queue $VideoQueueName not found"; exit 1 }
if (-not $opsQueueArn) { Write-Error "Ops queue $OpsQueueName not found"; exit 1 }
$videoCeArn = $videoCe.computeEnvironmentArn
$opsCeArn = $opsCe.computeEnvironmentArn
$videoOrder = $videoQueue.computeEnvironmentOrder
$videoQueueCeOk = ($videoOrder -and $videoOrder.Count -eq 1 -and ($videoOrder[0].computeEnvironment -eq $videoCeArn -or $videoOrder[0].computeEnvironment -eq $VideoCEName))
if (-not $videoQueueCeOk) {
    $stateBefore = $videoQueue.state
    if ($stateBefore -eq "ENABLED") {
        Invoke-Aws -ArgsArray @("batch", "update-job-queue", "--job-queue", $VideoQueueName, "--state", "DISABLED", "--region", $Region) -ErrorMessage "disable Video queue failed"
        $w = 0
        while ($w -lt 60) {
            Start-Sleep -Seconds 3
            $w += 3
            $q2 = ExecJson @("batch", "describe-job-queues", "--job-queues", $VideoQueueName, "--region", $Region, "--output", "json")
            if (-not $q2 -or -not $q2.jobQueues) { continue }
            $s = ($q2.jobQueues | Where-Object { $_.jobQueueName -eq $VideoQueueName } | Select-Object -First 1).state
            if ($s -eq "DISABLED") { break }
        }
    }
    $payload = '{"jobQueue":"' + $VideoQueueName + '","computeEnvironmentOrder":[{"order":1,"computeEnvironment":"' + $videoCeArn + '"}]}'
    $tf = Join-Path $RepoRoot "reconcile_vq_temp.json"
    [System.IO.File]::WriteAllText($tf, $payload, $utf8NoBom)
    $fileUri = "file://" + ($tf -replace '\\', '/')
    try {
        Invoke-Aws -ArgsArray @("batch", "update-job-queue", "--cli-input-json", $fileUri, "--region", $Region) -ErrorMessage "update Video queue computeEnvironmentOrder failed"
    } finally {
        Remove-Item $tf -Force -ErrorAction SilentlyContinue
    }
    if ($stateBefore -eq "ENABLED") { Invoke-Aws -ArgsArray @("batch", "update-job-queue", "--job-queue", $VideoQueueName, "--state", "ENABLED", "--region", $Region) -ErrorMessage "re-enable Video queue failed" }
}
$opsOrder = ($opsJqList.jobQueues | Where-Object { $_.jobQueueName -eq $OpsQueueName } | Select-Object -First 1).computeEnvironmentOrder
$opsQueueCeOk = ($opsOrder -and $opsOrder.Count -eq 1 -and ($opsOrder[0].computeEnvironment -eq $opsCeArn -or $opsOrder[0].computeEnvironment -eq $OpsCEName))
if (-not $opsQueueCeOk) {
    $oqObj = $opsJqList.jobQueues | Where-Object { $_.jobQueueName -eq $OpsQueueName } | Select-Object -First 1
    $stateBefore = $oqObj.state
    if ($stateBefore -eq "ENABLED") {
        Invoke-Aws -ArgsArray @("batch", "update-job-queue", "--job-queue", $OpsQueueName, "--state", "DISABLED", "--region", $Region) -ErrorMessage "disable Ops queue failed"
        $w = 0
        while ($w -lt 60) {
            Start-Sleep -Seconds 3
            $w += 3
            $q2 = ExecJson @("batch", "describe-job-queues", "--job-queues", $OpsQueueName, "--region", $Region, "--output", "json")
            if (-not $q2 -or -not $q2.jobQueues) { continue }
            $s = ($q2.jobQueues | Where-Object { $_.jobQueueName -eq $OpsQueueName } | Select-Object -First 1).state
            if ($s -eq "DISABLED") { break }
        }
    }
    $payload = '{"jobQueue":"' + $OpsQueueName + '","computeEnvironmentOrder":[{"order":1,"computeEnvironment":"' + $opsCeArn + '"}]}'
    $tf = Join-Path $RepoRoot "reconcile_oq_temp.json"
    [System.IO.File]::WriteAllText($tf, $payload, $utf8NoBom)
    $fileUri = "file://" + ($tf -replace '\\', '/')
    try {
        Invoke-Aws -ArgsArray @("batch", "update-job-queue", "--cli-input-json", $fileUri, "--region", $Region) -ErrorMessage "update Ops queue computeEnvironmentOrder failed"
    } finally {
        Remove-Item $tf -Force -ErrorAction SilentlyContinue
    }
    if ($stateBefore -eq "ENABLED") { Invoke-Aws -ArgsArray @("batch", "update-job-queue", "--job-queue", $OpsQueueName, "--state", "ENABLED", "--region", $Region) -ErrorMessage "re-enable Ops queue failed" }
}
$runnableList = ExecJson @("batch", "list-jobs", "--job-queue", $videoQueueArn, "--job-status", "RUNNABLE", "--region", $Region, "--output", "json")
if ($runnableList -and $runnableList.jobSummaryList) {
    foreach ($j in $runnableList.jobSummaryList) {
        $detail = ExecJson @("batch", "describe-jobs", "--jobs", $j.jobId, "--region", $Region, "--output", "json")
        $statusReason = $null
        if ($detail -and $detail.jobs -and $detail.jobs[0].statusReason) { $statusReason = $detail.jobs[0].statusReason }
        if ($statusReason -and $statusReason -match "MISCONFIGURATION:JOB_RESOURCE_REQUIREMENT") {
            & aws batch terminate-job --job-id $j.jobId --reason "Reconcile: MISCONFIGURATION:JOB_RESOURCE_REQUIREMENT cleanup" --region $Region 2>&1 | Out-Null
        }
    }
}
$minV = [int]$crV.minvCpus
$maxV = [int]$crV.maxvCpus
$allocV = $crV.allocationStrategy
if ($minV -ne 0 -or $maxV -ne 32 -or $allocV -ne "BEST_FIT_PROGRESSIVE") {
    Invoke-Aws -ArgsArray @("batch", "update-compute-environment", "--compute-environment", $VideoCEName, "--compute-resources", "minvCpus=0,maxvCpus=32", "--region", $Region) -ErrorMessage "update Video CE failed"
    $wait = 0
    while ($wait -lt 90) {
        Start-Sleep -Seconds 5
        $wait += 5
        $v = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json")
        $ve = $null
        if ($v -and $v.computeEnvironments) { $ve = $v.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1 }
        if (-not $ve) { continue }
        if ($ve.status -eq "VALID") { break }
        if ($ve.status -eq "INVALID") { Write-Error "Video CE INVALID"; exit 1 }
    }
}
$maxO = [int]$crO.maxvCpus
if ($maxO -ne 1) {
    Invoke-Aws -ArgsArray @("batch", "update-compute-environment", "--compute-environment", $OpsCEName, "--compute-resources", "minvCpus=0,maxvCpus=1", "--region", $Region) -ErrorMessage "update Ops CE failed"
    $wait = 0
    while ($wait -lt 90) {
        Start-Sleep -Seconds 5
        $wait += 5
        $o = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $OpsCEName, "--region", $Region, "--output", "json")
        $oe = $null
        if ($o -and $o.computeEnvironments) { $oe = $o.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $OpsCEName } | Select-Object -First 1 }
        if (-not $oe) { continue }
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
    $jdPath = Join-Path $RepoRoot "reconcile_video_jd_temp.json"
    $jsonStr = $regObj | ConvertTo-Json -Depth 25 -Compress:$false
    $jsonStr = $jsonStr -replace '"JobDefinitionName"', '"jobDefinitionName"' -replace '"ContainerProperties"', '"containerProperties"' -replace '"Memory":', '"memory":' -replace '"Vcpus":', '"vcpus":' -replace '"Image":', '"image":' -replace '"Command":', '"command":' -replace '"JobRoleArn":', '"jobRoleArn":' -replace '"ExecutionRoleArn":', '"executionRoleArn"' -replace '"ResourceRequirements":', '"resourceRequirements"' -replace '"LogConfiguration":', '"logConfiguration"' -replace '"RuntimePlatform":', '"runtimePlatform"' -replace '"CpuArchitecture":', '"cpuArchitecture"' -replace '"Timeout"', '"timeout"' -replace '"AttemptDurationSeconds"', '"attemptDurationSeconds"' -replace '"PlatformCapabilities"', '"platformCapabilities"' -replace '"Parameters"', '"parameters"' -replace '"RetryStrategy"', '"retryStrategy"' -replace '"Attempts":', '"attempts":' -replace '(\s)"Type":', '$1"type":'
    $jsonStr = $jsonStr -replace '"LogDriver":', '"logDriver":' -replace '"Options":', '"options"' -replace '"Awslogs-group":', '"awslogs-group":' -replace '"Awslogs-region":', '"awslogs-region":' -replace '"Awslogs-stream-prefix":', '"awslogs-stream-prefix":'
    [System.IO.File]::WriteAllText($jdPath, $jsonStr, $utf8NoBom)
    $fileUri = "file://" + ($jdPath -replace '\\', '/')
    try {
        $regOutRaw = Invoke-Aws -ArgsArray @("batch", "register-job-definition", "--cli-input-json", $fileUri, "--region", $Region, "--output", "json") -ErrorMessage "register Video job def failed"
    } finally {
        Remove-Item $jdPath -Force -ErrorAction SilentlyContinue
    }
    $regOut = ($regOutRaw | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | Out-String).Trim() | ConvertFrom-Json
    $newRev = $null; if ($regOut -and $regOut.revision) { $newRev = [int]$regOut.revision }
    $videoJdAllAfter = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $VideoJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
    if ($videoJdAllAfter -and $videoJdAllAfter.jobDefinitions) {
        foreach ($d in $videoJdAllAfter.jobDefinitions) {
            $dm = [int]$d.containerProperties.memory; $dv = [int]$d.containerProperties.vcpus
            $dt = 0; if ($d.timeout -and $d.timeout.attemptDurationSeconds) { $dt = [int]$d.timeout.attemptDurationSeconds }
            $darm = ($d.containerProperties.runtimePlatform -and $d.containerProperties.runtimePlatform.cpuArchitecture -eq "ARM64")
            $wrong = ($dm -ne 3072 -or $dv -ne 2 -or $dt -ne 14400 -or -not $darm)
            if ($wrong -and ($null -eq $newRev -or [int]$d.revision -ne $newRev)) {
                & aws batch deregister-job-definition --job-definition "${VideoJobDefName}:$($d.revision)" --region $Region 2>&1 | Out-Null
            }
        }
    }
}
$opsJdAll = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $OpsJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
$opsJdLatest = $null
if ($opsJdAll -and $opsJdAll.jobDefinitions -and $opsJdAll.jobDefinitions.Count -gt 0) {
    $opsJdLatest = $opsJdAll.jobDefinitions | Sort-Object { [int]$_.revision } -Descending | Select-Object -First 1
}
$needOpsJdRegister = $false
if ($opsJdLatest) {
    $memO = [int]$opsJdLatest.containerProperties.memory
    $vcpusO = [int]$opsJdLatest.containerProperties.vcpus
    $timeoutO = 0; if ($opsJdLatest.timeout -and $opsJdLatest.timeout.attemptDurationSeconds) { $timeoutO = [int]$opsJdLatest.timeout.attemptDurationSeconds }
    $rpO = $opsJdLatest.containerProperties.runtimePlatform
    $armO = ($rpO -and $rpO.cpuArchitecture -eq "ARM64")
    if ($memO -ne 1024 -or $vcpusO -ne 1 -or $timeoutO -ne 300 -or -not $armO) { $needOpsJdRegister = $true }
}
if (-not $opsJdLatest) {
    $src = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $VideoJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
    $srcJd = $src.jobDefinitions | Sort-Object { [int]$_.revision } -Descending | Select-Object -First 1
    if (-not $srcJd) { Write-Error "Cannot create Ops job def: Video job def has no ACTIVE revision"; exit 1 }
    $illegal = @("revision", "status", "jobDefinitionArn", "containerOrchestrationType")
    $regO = @{}
    foreach ($key in $srcJd.PSObject.Properties.Name) {
        if ($key -notin $illegal) { $regO[$key] = $srcJd.$key }
    }
    $regO.jobDefinitionName = $OpsJobDefName
    $regO.containerProperties.memory = 1024
    $regO.containerProperties.vcpus = 1
    $regO.containerProperties.command = @("python", "manage.py", "reconcile_batch_video_jobs")
    $regO.containerProperties.runtimePlatform = @{ cpuArchitecture = "ARM64" }
    $regO.timeout = @{ attemptDurationSeconds = 300 }
    if ($regO.parameters) { $regO.parameters = @{} }
    if ($regO.containerProperties.logConfiguration) {
        $regO.containerProperties.logConfiguration.options = @{ "awslogs-group" = "/aws/batch/academy-video-ops"; "awslogs-region" = $Region; "awslogs-stream-prefix" = "ops" }
    }
    $jdPathO = Join-Path $RepoRoot "reconcile_ops_jd_new_temp.json"
    $jsonStrO = $regO | ConvertTo-Json -Depth 25 -Compress:$false
    $jsonStrO = $jsonStrO -replace '"JobDefinitionName"', '"jobDefinitionName"' -replace '"ContainerProperties"', '"containerProperties"' -replace '"Memory":', '"memory":' -replace '"Vcpus":', '"vcpus":' -replace '"Timeout"', '"timeout"' -replace '"AttemptDurationSeconds"', '"attemptDurationSeconds"' -replace '"RuntimePlatform"', '"runtimePlatform"' -replace '"CpuArchitecture"', '"cpuArchitecture"' -replace '"Image":', '"image":' -replace '"Command":', '"command":' -replace '"JobRoleArn":', '"jobRoleArn":' -replace '"ExecutionRoleArn":', '"executionRoleArn"' -replace '"LogConfiguration":', '"logConfiguration"' -replace '"PlatformCapabilities"', '"platformCapabilities"' -replace '"RetryStrategy"', '"retryStrategy"'
    $jsonStrO = $jsonStrO -replace '"LogDriver":', '"logDriver":' -replace '"Options":', '"options"' -replace '"Awslogs-group":', '"awslogs-group":' -replace '"Awslogs-region":', '"awslogs-region":' -replace '"Awslogs-stream-prefix":', '"awslogs-stream-prefix":'
    [System.IO.File]::WriteAllText($jdPathO, $jsonStrO, $utf8NoBom)
    $fileUriO = "file://" + ($jdPathO -replace '\\', '/')
    try {
        Invoke-Aws -ArgsArray @("batch", "register-job-definition", "--cli-input-json", $fileUriO, "--region", $Region, "--output", "json") -ErrorMessage "register Ops job def failed"
    } finally {
        Remove-Item $jdPathO -Force -ErrorAction SilentlyContinue
    }
}
elseif ($needOpsJdRegister -and $opsJdLatest) {
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
    $jdPathO = Join-Path $RepoRoot "reconcile_ops_jd_temp.json"
    $jsonStrO = $regO | ConvertTo-Json -Depth 25 -Compress:$false
    $jsonStrO = $jsonStrO -replace '"JobDefinitionName"', '"jobDefinitionName"' -replace '"ContainerProperties"', '"containerProperties"' -replace '"Memory":', '"memory":' -replace '"Vcpus":', '"vcpus":' -replace '"Timeout"', '"timeout"' -replace '"AttemptDurationSeconds"', '"attemptDurationSeconds"' -replace '"RuntimePlatform"', '"runtimePlatform"' -replace '"CpuArchitecture"', '"cpuArchitecture"' -replace '"Image":', '"image":' -replace '"Command":', '"command":' -replace '"JobRoleArn":', '"jobRoleArn":' -replace '"ExecutionRoleArn":', '"executionRoleArn"' -replace '"LogConfiguration":', '"logConfiguration"' -replace '"PlatformCapabilities"', '"platformCapabilities"' -replace '"RetryStrategy"', '"retryStrategy"'
    $jsonStrO = $jsonStrO -replace '"LogDriver":', '"logDriver":' -replace '"Options":', '"options"' -replace '"Awslogs-group":', '"awslogs-group":' -replace '"Awslogs-region":', '"awslogs-region":' -replace '"Awslogs-stream-prefix":', '"awslogs-stream-prefix":'
    [System.IO.File]::WriteAllText($jdPathO, $jsonStrO, $utf8NoBom)
    $fileUriO = "file://" + ($jdPathO -replace '\\', '/')
    try {
        $regOutORaw = Invoke-Aws -ArgsArray @("batch", "register-job-definition", "--cli-input-json", $fileUriO, "--region", $Region, "--output", "json") -ErrorMessage "register Ops job def failed"
    } finally {
        Remove-Item $jdPathO -Force -ErrorAction SilentlyContinue
    }
    $regOutO = ($regOutORaw | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | Out-String).Trim() | ConvertFrom-Json
    $newRevO = $null; if ($regOutO -and $regOutO.revision) { $newRevO = [int]$regOutO.revision }
    $opsJdAllAfter = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $OpsJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
    if ($opsJdAllAfter -and $opsJdAllAfter.jobDefinitions) {
        foreach ($d in $opsJdAllAfter.jobDefinitions) {
            $m = [int]$d.containerProperties.memory; $v = [int]$d.containerProperties.vcpus
            $to = 0; if ($d.timeout -and $d.timeout.attemptDurationSeconds) { $to = [int]$d.timeout.attemptDurationSeconds }
            if (($m -ne 1024 -or $v -ne 1 -or $to -ne 300) -and ($null -eq $newRevO -or [int]$d.revision -ne $newRevO)) {
                & aws batch deregister-job-definition --job-definition "${OpsJobDefName}:$($d.revision)" --region $Region 2>&1 | Out-Null
            }
        }
    }
}
$rule = ExecJson @("events", "describe-rule", "--name", $ReconcileRuleName, "--region", $Region, "--output", "json")
$ruleExists = ($rule -and $rule.Name -eq $ReconcileRuleName)
if (-not $ruleExists) {
    Invoke-Aws -ArgsArray @("events", "put-rule", "--name", $ReconcileRuleName, "--schedule-expression", "rate(5 minutes)", "--state", "ENABLED", "--description", "Reconcile video jobs", "--region", $Region) -ErrorMessage "put-rule failed"
}
else {
    if ($rule.ScheduleExpression -notmatch "rate\s*\(\s*5\s*minute") {
        Invoke-Aws -ArgsArray @("events", "put-rule", "--name", $ReconcileRuleName, "--schedule-expression", "rate(5 minutes)", "--state", "ENABLED", "--region", $Region) -ErrorMessage "put-rule schedule failed"
    }
}
$tgtList = ExecJson @("events", "list-targets-by-rule", "--rule", $ReconcileRuleName, "--region", $Region, "--output", "json")
$tgtCorrect = $false
if ($tgtList -and $tgtList.Targets -and $tgtList.Targets.Count -gt 0) {
    $t = $tgtList.Targets[0]
    if ($t.Arn -eq $opsQueueArn -and $t.BatchParameters -and $t.BatchParameters.JobDefinition -like "${OpsJobDefName}*") { $tgtCorrect = $true }
}
if (-not $tgtCorrect) {
    $EventsRoleName = "academy-eventbridge-batch-video-role"
    $roleResp = ExecJson @("iam", "get-role", "--role-name", $EventsRoleName, "--output", "json")
    if (-not $roleResp -or -not $roleResp.Role) { Write-Error "EventBridge role $EventsRoleName not found"; exit 1 }
    $eventsRoleArn = $roleResp.Role.Arn
    $targetsJson = '[{"Id":"1","Arn":"' + $opsQueueArn + '","RoleArn":"' + $eventsRoleArn + '","BatchParameters":{"JobDefinition":"' + $OpsJobDefName + '","JobName":"reconcile-video-jobs"}}]'
    Invoke-Aws -ArgsArray @("events", "put-targets", "--rule", $ReconcileRuleName, "--targets", $targetsJson, "--region", $Region) -ErrorMessage "put-targets failed"
}
$alarmList = ExecJson @("cloudwatch", "describe-alarms", "--alarm-names", $RunnableAlarmName, "--region", $Region, "--output", "json")
$alarmExists = ($alarmList -and $alarmList.MetricAlarms -and $alarmList.MetricAlarms.Count -gt 0)
$dimensionsJson = "Name=JobQueue,Value=$videoQueueArn"
if (-not $alarmExists) {
    & aws cloudwatch put-metric-alarm --alarm-name $RunnableAlarmName --alarm-description "Video queue RUNNABLE > 0 for 10 min" --namespace AWS/Batch --metric-name RUNNABLE --dimensions $dimensionsJson --statistic Average --period 300 --evaluation-periods 2 --threshold 0 --comparison-operator GreaterThanThreshold --treat-missing-data notBreaching --region $Region 2>&1 | Out-Null
}
else {
    $a = $alarmList.MetricAlarms[0]
    if ([int]$a.Threshold -ne 0 -or $a.ComparisonOperator -ne "GreaterThanThreshold" -or [int]$a.EvaluationPeriods -lt 2) {
        & aws cloudwatch put-metric-alarm --alarm-name $RunnableAlarmName --alarm-description "Video queue RUNNABLE > 0 for 10 min" --namespace AWS/Batch --metric-name RUNNABLE --dimensions $dimensionsJson --statistic Average --period 300 --evaluation-periods 2 --threshold 0 --comparison-operator GreaterThanThreshold --treat-missing-data notBreaching --region $Region 2>&1 | Out-Null
    }
}
$videoCeList2 = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $VideoCEName, "--region", $Region, "--output", "json")
$videoCe2 = $null; if ($videoCeList2 -and $videoCeList2.computeEnvironments) { $videoCe2 = $videoCeList2.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $VideoCEName } | Select-Object -First 1 }
$opsCeList2 = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $OpsCEName, "--region", $Region, "--output", "json")
$opsCe2 = $null; if ($opsCeList2 -and $opsCeList2.computeEnvironments) { $opsCe2 = $opsCeList2.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $OpsCEName } | Select-Object -First 1 }
$videoJdAll2 = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $VideoJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
$videoJdLatest2 = $null; if ($videoJdAll2 -and $videoJdAll2.jobDefinitions) { $videoJdLatest2 = $videoJdAll2.jobDefinitions | Sort-Object { [int]$_.revision } -Descending | Select-Object -First 1 }
$opsJdAll2 = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $OpsJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
$opsJdLatest2 = $null; if ($opsJdAll2 -and $opsJdAll2.jobDefinitions) { $opsJdLatest2 = $opsJdAll2.jobDefinitions | Sort-Object { [int]$_.revision } -Descending | Select-Object -First 1 }
$rule2 = ExecJson @("events", "describe-rule", "--name", $ReconcileRuleName, "--region", $Region, "--output", "json")
$tgtList2 = ExecJson @("events", "list-targets-by-rule", "--rule", $ReconcileRuleName, "--region", $Region, "--output", "json")
$alarmList2 = ExecJson @("cloudwatch", "describe-alarms", "--alarm-names", $RunnableAlarmName, "--region", $Region, "--output", "json")
$fail = $false
if (-not $videoCe2) { Write-Error "Video CE not found"; $fail = $true }
elseif ($videoCe2.status -ne "VALID" -or $videoCe2.state -ne "ENABLED") { Write-Error "Video CE state=$($videoCe2.state) status=$($videoCe2.status)"; $fail = $true }
if (-not $opsCe2) { Write-Error "Ops CE not found"; $fail = $true }
elseif ($opsCe2.status -ne "VALID" -or $opsCe2.state -ne "ENABLED") { Write-Error "Ops CE state=$($opsCe2.state) status=$($opsCe2.status)"; $fail = $true }
if ($videoCe2) {
    $crV2 = $videoCe2.computeResources
    if ([int]$crV2.minvCpus -ne 0 -or [int]$crV2.maxvCpus -ne 32) { Write-Error "Video CE min/max vCpus mismatch"; $fail = $true }
    $imgType = $null; if ($crV2.ec2Configuration -and $crV2.ec2Configuration.Count -gt 0) { $imgType = $crV2.ec2Configuration[0].imageType }
    $imgOk = ($imgType -eq "ECS_AL2023_ARM64" -or $imgType -eq "ECS_AL2023")
    if ($imgType -and -not $imgOk) { Write-Error "Video CE imageType not ECS_AL2023/ECS_AL2023_ARM64"; $fail = $true }
}
if ($opsCe2 -and [int]$opsCe2.computeResources.maxvCpus -ne 1) { Write-Error "Ops CE maxvCpus != 1"; $fail = $true }
if ($videoJdLatest2) {
    if ([int]$videoJdLatest2.containerProperties.memory -ne 3072) { Write-Error "Video JobDef memory != 3072"; $fail = $true }
    if ([int]$videoJdLatest2.containerProperties.vcpus -ne 2) { Write-Error "Video JobDef vcpus != 2"; $fail = $true }
    if (-not $videoJdLatest2.timeout -or [int]$videoJdLatest2.timeout.attemptDurationSeconds -ne 14400) { Write-Error "Video JobDef timeout != 14400"; $fail = $true }
    if (-not $videoJdLatest2.containerProperties.runtimePlatform -or $videoJdLatest2.containerProperties.runtimePlatform.cpuArchitecture -ne "ARM64") { Write-Error "Video JobDef not ARM64"; $fail = $true }
}
else { Write-Error "Video JobDef not found"; $fail = $true }
if (-not $opsJdLatest2) { Write-Error "Ops JobDef not found"; $fail = $true }
elseif ($opsJdLatest2) {
    if ([int]$opsJdLatest2.containerProperties.memory -ne 1024) { Write-Error "Ops JobDef memory != 1024"; $fail = $true }
    if ([int]$opsJdLatest2.containerProperties.vcpus -ne 1) { Write-Error "Ops JobDef vcpus != 1"; $fail = $true }
    if (-not $opsJdLatest2.timeout -or [int]$opsJdLatest2.timeout.attemptDurationSeconds -ne 300) { Write-Error "Ops JobDef timeout != 300"; $fail = $true }
    if (-not $opsJdLatest2.containerProperties.runtimePlatform -or $opsJdLatest2.containerProperties.runtimePlatform.cpuArchitecture -ne "ARM64") { Write-Error "Ops JobDef not ARM64"; $fail = $true }
}
if (-not $rule2 -or $rule2.Name -ne $ReconcileRuleName) { Write-Error "EventBridge rule missing"; $fail = $true }
if ($rule2 -and $rule2.ScheduleExpression -notmatch "rate\s*\(\s*5\s*minute") { Write-Error "Reconcile rule schedule not rate(5 minutes)"; $fail = $true }
$tgtOk = $false
if ($tgtList2 -and $tgtList2.Targets -and $tgtList2.Targets.Count -gt 0) {
    $t0 = $tgtList2.Targets[0]
    if ($t0.Arn -eq $opsQueueArn -and $t0.BatchParameters.JobDefinition -like "${OpsJobDefName}*") { $tgtOk = $true }
}
if (-not $tgtOk) { Write-Error "EventBridge target not Ops queue or wrong job def"; $fail = $true }
if (-not $alarmList2 -or -not $alarmList2.MetricAlarms -or $alarmList2.MetricAlarms.Count -eq 0) { Write-Error "CloudWatch RUNNABLE alarm missing"; $fail = $true }
else {
    if ([int]$alarmList2.MetricAlarms[0].Threshold -ne 0 -or $alarmList2.MetricAlarms[0].ComparisonOperator -ne "GreaterThanThreshold" -or [int]$alarmList2.MetricAlarms[0].EvaluationPeriods -lt 2) { Write-Error "RUNNABLE alarm threshold/period mismatch"; $fail = $true }
}
if ($fail) { exit 1 }
$vCr = $videoCe2.computeResources
$vImg = $null; if ($vCr.ec2Configuration -and $vCr.ec2Configuration.Count -gt 0) { $vImg = $vCr.ec2Configuration[0].imageType }
$vInst = ""; if ($vCr.instanceTypes -and $vCr.instanceTypes.Count -gt 0) { $vInst = ($vCr.instanceTypes -join ",") }
Write-Host "=== EVIDENCE ==="
Write-Host "Video CE: instanceTypes=$vInst imageType=$vImg minvCpus=$($vCr.minvCpus) maxvCpus=$($vCr.maxvCpus) desiredvCpus=$($vCr.desiredvCpus)"
Write-Host "Ops CE: maxvCpus=$($opsCe2.computeResources.maxvCpus)"
if ($videoJdLatest2) { Write-Host "Video jobdef latest: vcpus=$($videoJdLatest2.containerProperties.vcpus) memory=$($videoJdLatest2.containerProperties.memory) timeout=$($videoJdLatest2.timeout.attemptDurationSeconds) runtimePlatform=$($videoJdLatest2.containerProperties.runtimePlatform.cpuArchitecture)" }
if ($opsJdLatest2) { Write-Host "Ops jobdef latest: vcpus=$($opsJdLatest2.containerProperties.vcpus) memory=$($opsJdLatest2.containerProperties.memory) timeout=$($opsJdLatest2.timeout.attemptDurationSeconds) runtimePlatform=$($opsJdLatest2.containerProperties.runtimePlatform.cpuArchitecture)" }
$tgtJobDef = "n/a"
if ($tgtList2 -and $tgtList2.Targets -and $tgtList2.Targets.Count -gt 0) {
    $t0 = $tgtList2.Targets[0]
    if ($t0.BatchParameters -and $t0.BatchParameters.JobDefinition) { $tgtJobDef = $t0.BatchParameters.JobDefinition }
    Write-Host "EventBridge target: queueArn=$($t0.Arn) jobDef=$tgtJobDef"
}
$jv = ExecJson @("batch", "list-jobs", "--job-queue", $videoQueueArn, "--job-status", "RUNNABLE", "--region", $Region, "--output", "json"); $runV = 0; if ($jv -and $jv.jobSummaryList) { $runV = @($jv.jobSummaryList).Count }
$jv2 = ExecJson @("batch", "list-jobs", "--job-queue", $videoQueueArn, "--job-status", "STARTING", "--region", $Region, "--output", "json"); $startV = 0; if ($jv2 -and $jv2.jobSummaryList) { $startV = @($jv2.jobSummaryList).Count }
$jv3 = ExecJson @("batch", "list-jobs", "--job-queue", $videoQueueArn, "--job-status", "RUNNING", "--region", $Region, "--output", "json"); $runningV = 0; if ($jv3 -and $jv3.jobSummaryList) { $runningV = @($jv3.jobSummaryList).Count }
$jo = ExecJson @("batch", "list-jobs", "--job-queue", $opsQueueArn, "--job-status", "RUNNABLE", "--region", $Region, "--output", "json"); $runO = 0; if ($jo -and $jo.jobSummaryList) { $runO = @($jo.jobSummaryList).Count }
$jo2 = ExecJson @("batch", "list-jobs", "--job-queue", $opsQueueArn, "--job-status", "STARTING", "--region", $Region, "--output", "json"); $startO = 0; if ($jo2 -and $jo2.jobSummaryList) { $startO = @($jo2.jobSummaryList).Count }
$jo3 = ExecJson @("batch", "list-jobs", "--job-queue", $opsQueueArn, "--job-status", "RUNNING", "--region", $Region, "--output", "json"); $runningO = 0; if ($jo3 -and $jo3.jobSummaryList) { $runningO = @($jo3.jobSummaryList).Count }
Write-Host "Video queue: RUNNABLE=$runV STARTING=$startV RUNNING=$runningV"
Write-Host "Ops queue: RUNNABLE=$runO STARTING=$startO RUNNING=$runningO"
