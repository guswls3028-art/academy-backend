# params.yaml loader — sets script: variables. Single source of truth; no env/prod.ps1.
$ErrorActionPreference = "Stop"
$SsotDir = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $SsotDir "..\..\..")).Path
$ParamsPath = Join-Path $RepoRoot "docs\00-SSOT\v4\params.yaml"

if (-not (Test-Path $ParamsPath)) { throw "params.yaml not found: $ParamsPath" }

function Get-ParamsYaml {
    $lines = Get-Content $ParamsPath -Raw
    $h = @{}
    $section = ""
    foreach ($line in ($lines -split "`r?`n")) {
        $l = $line
        if ($l -match '^([a-zA-Z0-9_]+):\s*$') {
            $section = $matches[1]
            if (-not $h[$section]) { $h[$section] = @{} }
            continue
        }
        if ($l -match '^\s{2}([a-zA-Z0-9_]+):\s*(.*)$') {
            $key = $matches[1]; $val = $matches[2].Trim()
            if ($val -match '^"(.*)"$') { $val = $matches[1] }
            if ($section) { $h[$section][$key] = $val }
            continue
        }
        if ($l -match '^\s+-\s+(.+)$') {
            $item = $matches[1].Trim()
            if ($item -match '^"(.*)"$') { $item = $matches[1] }
            if (-not $h[$section]["_list"]) { $h[$section]["_list"] = [System.Collections.ArrayList]::new() }
            [void]$h[$section]["_list"].Add($item)
            continue
        }
    }
    return $h
}

# Extract nested values from raw content (api.identification.allocationId etc.)
function Get-ParamFromRaw {
    param([string]$raw, [string]$key)
    if ($raw -match "(?m)^\s*$key\s*:\s*([^\s#\r\n""]+|\""[^\""]*\"")") { return $matches[1].Trim('"') }
    return $null
}

function Load-SSOT {
    param([string]$Env = "prod")
    $p = Get-ParamsYaml
    $raw = Get-Content $ParamsPath -Raw

    $g = $p["global"]
    $n = $p["network"]
    $script:Region = $g["region"]
    $script:AccountId = $g["accountId"]
    $script:VpcId = $n["vpcId"]
    $script:PublicSubnets = @()
    if ($n["_list"]) { $script:PublicSubnets = @($n["_list"]) }
    if ($raw -match 'batch:\s*(sg-[a-zA-Z0-9]+)') { $script:BatchSecurityGroupId = $matches[1] }
    elseif ($n["securityGroups"] -is [string]) { $script:BatchSecurityGroupId = $n["securityGroups"] }
    else { $script:BatchSecurityGroupId = "sg-011ed1d9eb4a65b8f" }

    $script:SsmWorkersEnv = $p["ssm"]["workersEnv"]
    $script:SsmApiEnv = $p["ssm"]["apiEnv"]

    $script:EcrApiRepo = $p["ecr"]["apiRepo"]
    $script:VideoWorkerRepo = $p["ecr"]["videoWorkerRepo"]
    $script:EcrMessagingRepo = $p["ecr"]["messagingWorkerRepo"]
    $script:EcrAiRepo = $p["ecr"]["aiWorkerRepo"]
    $script:EcrBaseRepo = if ($p["ecr"]["baseRepo"]) { $p["ecr"]["baseRepo"] } else { "academy-base" }

    $script:ApiAllocationId = Get-ParamFromRaw $raw "allocationId"
    if (-not $script:ApiAllocationId) { $script:ApiAllocationId = "eipalloc-071ef2b5b5bec9428" }
    $script:ApiPublicIp = Get-ParamFromRaw $raw "publicIp"
    if (-not $script:ApiPublicIp) { $script:ApiPublicIp = "15.165.147.157" }
    $script:ApiContainerName = $p["api"]["containerName"]
    $script:ApiBaseUrl = $p["api"]["apiBaseUrl"]
    $script:ApiInstanceTagKey = $p["api"]["instanceTagKey"]
    $script:ApiInstanceTagValue = $p["api"]["instanceTagValue"]
    $script:ApiAmiId = $p["api"]["amiId"]
    $script:ApiInstanceProfile = $p["api"]["instanceProfile"]
    $script:ApiSubnetId = $p["api"]["subnetId"]
    $script:ApiSecurityGroupId = $p["api"]["securityGroupId"]
    $script:ApiInstanceType = if ($p["api"]["instanceType"]) { $p["api"]["instanceType"] } else { "t3.small" }
    if (-not $script:ApiSubnetId -and $script:PublicSubnets -and $script:PublicSubnets.Count -gt 0) { $script:ApiSubnetId = $script:PublicSubnets[0] }
    if (-not $script:ApiSecurityGroupId) { $script:ApiSecurityGroupId = $script:BatchSecurityGroupId }
    $script:ApiUserData = $p["api"]["userData"]
    $script:ApiASGName = $p["api"]["asgName"]
    $script:ApiLaunchTemplateName = $p["api"]["asgLaunchTemplateName"]
    $script:ApiASGMinSize = [int](($p["api"]["asgMinSize"] -as [int]))
    if (-not $script:ApiASGMinSize -and $script:ApiASGMinSize -ne 0) { $script:ApiASGMinSize = 1 }
    $script:ApiASGMaxSize = [int](($p["api"]["asgMaxSize"] -as [int]))
    if (-not $script:ApiASGMaxSize -and $script:ApiASGMaxSize -ne 0) { $script:ApiASGMaxSize = 1 }
    $script:ApiASGDesiredCapacity = [int](($p["api"]["asgDesiredCapacity"] -as [int]))
    if (-not $script:ApiASGDesiredCapacity -and $script:ApiASGDesiredCapacity -ne 0) { $script:ApiASGDesiredCapacity = 1 }

    $script:BuildTagKey = $p["build"]["instanceTagKey"]
    $script:BuildTagValue = $p["build"]["instanceTagValue"]
    $script:BuildAmiId = $p["build"]["amiId"]
    $script:BuildInstanceProfile = $p["build"]["instanceProfile"]
    $script:BuildSubnetId = $p["build"]["subnetId"]
    $script:BuildSecurityGroupId = $p["build"]["securityGroupId"]
    $script:BuildInstanceType = if ($p["build"]["instanceType"]) { $p["build"]["instanceType"] } else { "t4g.small" }
    if (-not $script:BuildSubnetId -and $script:PublicSubnets -and $script:PublicSubnets.Count -gt 0) { $script:BuildSubnetId = $script:PublicSubnets[0] }
    if (-not $script:BuildSecurityGroupId) { $script:BuildSecurityGroupId = $script:BatchSecurityGroupId }

    $script:MessagingASGName = $p["messagingWorker"]["asgName"]
    $script:MessagingLaunchTemplateName = $p["messagingWorker"]["launchTemplateName"]
    $script:MessagingAmiId = $p["messagingWorker"]["amiId"]
    $script:MessagingInstanceType = if ($p["messagingWorker"]["instanceType"]) { $p["messagingWorker"]["instanceType"] } else { "t3.small" }
    $script:MessagingMinSize = [int]($p["messagingWorker"]["minSize"])
    $script:MessagingMaxSize = [int]($p["messagingWorker"]["maxSize"])
    $script:MessagingDesiredCapacity = [int]($p["messagingWorker"]["desiredCapacity"])
    $script:AiASGName = $p["aiWorker"]["asgName"]
    $script:AiLaunchTemplateName = $p["aiWorker"]["launchTemplateName"]
    $script:AiAmiId = $p["aiWorker"]["amiId"]
    $script:AiInstanceType = if ($p["aiWorker"]["instanceType"]) { $p["aiWorker"]["instanceType"] } else { "t3.small" }
    $script:AiMinSize = [int]($p["aiWorker"]["minSize"])
    $script:AiMaxSize = [int]($p["aiWorker"]["maxSize"])
    $script:AiDesiredCapacity = [int]($p["aiWorker"]["desiredCapacity"])

    $script:VideoCEName = $p["videoBatch"]["computeEnvironmentName"]
    $script:VideoQueueName = $p["videoBatch"]["videoQueueName"]
    $script:VideoJobDefName = $p["videoBatch"]["workerJobDefName"]
    $script:OpsCEName = $p["videoBatch"]["opsComputeEnvironmentName"]
    $script:OpsQueueName = $p["videoBatch"]["opsQueueName"]
    $script:OpsJobDefReconcile = "academy-video-ops-reconcile"
    $script:OpsJobDefScanStuck = "academy-video-ops-scanstuck"
    $script:OpsJobDefNetprobe = "academy-video-ops-netprobe"
    if ($raw -match 'reconcile:\s*([a-zA-Z0-9-]+)') { $script:OpsJobDefReconcile = $matches[1] }
    if ($raw -match 'scanstuck:\s*([a-zA-Z0-9-]+)') { $script:OpsJobDefScanStuck = $matches[1] }
    if ($raw -match 'netprobe:\s*([a-zA-Z0-9-]+)') { $script:OpsJobDefNetprobe = $matches[1] }

    $script:EventBridgeReconcileRule = $p["eventBridge"]["reconcileRuleName"]
    $script:EventBridgeScanStuckRule = $p["eventBridge"]["scanStuckRuleName"]
    $script:EventBridgeRoleName = $p["eventBridge"]["roleName"]

    $script:RdsDbIdentifier = $p["rds"]["dbIdentifier"]
    if (-not $script:RdsDbIdentifier) { $script:RdsDbIdentifier = Get-ParamFromRaw $raw "dbIdentifier" }
    $script:RedisReplicationGroupId = $p["redis"]["replicationGroupId"]
    if (-not $script:RedisReplicationGroupId) { $script:RedisReplicationGroupId = Get-ParamFromRaw $raw "replicationGroupId" }
    $script:RedisSubnetGroupName = $p["redis"]["subnetGroupName"]
    $script:RedisSecurityGroupId = $p["redis"]["securityGroupId"]

    $script:VideoLogGroup = "/aws/batch/academy-video-worker"
    $script:OpsLogGroup = "/aws/batch/academy-video-ops"

    $script:SSOT_CE = @($script:VideoCEName, $script:OpsCEName)
    $script:SSOT_Queue = @($script:VideoQueueName, $script:OpsQueueName)
    $script:SSOT_JobDef = @($script:VideoJobDefName, $script:OpsJobDefReconcile, $script:OpsJobDefScanStuck, $script:OpsJobDefNetprobe)
    $script:SSOT_EventBridgeRule = @($script:EventBridgeReconcileRule, $script:EventBridgeScanStuckRule)
    $script:SSOT_ASG = @($script:MessagingASGName, $script:AiASGName)
    $script:SSOT_RDS = @($script:RdsDbIdentifier)
    $script:SSOT_Redis = @($script:RedisReplicationGroupId)
    $script:SSOT_ECR = @($script:EcrApiRepo, $script:VideoWorkerRepo, $script:EcrMessagingRepo, $script:EcrAiRepo)
    $script:SSOT_SSM = @($script:SsmApiEnv, $script:SsmWorkersEnv)
    $script:SSOT_EIP = @($script:ApiAllocationId)
    $script:SSOT_IAMRoles = @(
        "academy-batch-service-role",
        "academy-batch-ecs-instance-role",
        "academy-batch-ecs-task-execution-role",
        "academy-video-batch-job-role",
        "academy-eventbridge-batch-video-role"
    )
    $script:SSOT_InstanceProfile = @("academy-batch-ecs-instance-profile")
    $script:SSOT_ECSClusterPatterns = @("*academy-video-batch-ce-final*", "*academy-video-ops-ce*")
}
