# params.yaml loader — sets script: variables. Single source of truth; no env/prod.ps1.
$ErrorActionPreference = "Stop"
$SsotDir = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $SsotDir "..\..\..")).Path
$ParamsPath = Join-Path $RepoRoot "docs\00-SSOT\v1\params.yaml"

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
    function Coerce-Int { param($val, $default = 0) if ($val -ne $null -and $val -ne "") { [int]$val } else { [int]$default } }
    $p = Get-ParamsYaml
    $raw = Get-Content $ParamsPath -Raw

    $g = $p["global"]
    $n = $p["network"]
    $script:Region = $g["region"]
    $script:AccountId = $g["accountId"]
    $script:RelaxedValidation = ($g["validationMode"] -eq "Relaxed")
    $script:VpcId = if ($n["vpcId"]) { $n["vpcId"] } else { "" }
    $script:VpcCidr = if ($n["vpcCidr"]) { $n["vpcCidr"] } else { "10.0.0.0/16" }
    $script:PublicSubnetCidr1 = if ($n["publicSubnetCidr1"]) { $n["publicSubnetCidr1"] } else { "10.0.1.0/24" }
    $script:PublicSubnetCidr2 = if ($n["publicSubnetCidr2"]) { $n["publicSubnetCidr2"] } else { "10.0.2.0/24" }
    $script:PrivateSubnetCidr1 = if ($n["privateSubnetCidr1"]) { $n["privateSubnetCidr1"] } else { "10.0.11.0/24" }
    $script:PrivateSubnetCidr2 = if ($n["privateSubnetCidr2"]) { $n["privateSubnetCidr2"] } else { "10.0.12.0/24" }
    $script:VpcName = if ($n["vpcName"]) { $n["vpcName"] } else { "academy-v1-vpc" }
    $script:SgAppName = if ($n["sgAppName"]) { $n["sgAppName"] } else { "academy-v1-sg-app" }
    $script:SgBatchName = if ($n["sgBatchName"]) { $n["sgBatchName"] } else { "academy-v1-sg-batch" }
    $script:SgDataName = if ($n["sgDataName"]) { $n["sgDataName"] } else { "academy-v1-sg-data" }
    $script:PublicSubnets = @()
    if ($p["networkPublicSubnets"] -and $p["networkPublicSubnets"]["_list"]) { $script:PublicSubnets = @($p["networkPublicSubnets"]["_list"]) }
    elseif ($n["_list"]) { $script:PublicSubnets = @($n["_list"]) }
    $script:PrivateSubnets = @()
    if ($p["networkPrivateSubnets"] -and $p["networkPrivateSubnets"]["_list"]) { $script:PrivateSubnets = @($p["networkPrivateSubnets"]["_list"]) }
    $script:NatEnabled = ($n["natEnabled"] -eq "true")
    $script:AlbEnabled = ($n["albEnabled"] -eq "true")
    $script:NatGatewayId = if ($n["natGatewayId"]) { $n["natGatewayId"] } else { "" }
    $script:SecurityGroupApp = if ($n["securityGroupApp"]) { $n["securityGroupApp"] } else { "" }
    $script:BatchSecurityGroupId = if ($n["securityGroupBatch"]) { $n["securityGroupBatch"] } else { "sg-011ed1d9eb4a65b8f" }
    $script:SecurityGroupData = if ($n["securityGroupData"]) { $n["securityGroupData"] } else { "" }
    if ($raw -match 'batch:\s*(sg-[a-zA-Z0-9]+)') { $script:BatchSecurityGroupId = $matches[1] }

    $script:SsmWorkersEnv = $p["ssm"]["workersEnv"]
    $script:SsmApiEnv = $p["ssm"]["apiEnv"]
    $script:DeployLockParamName = if ($p["ssm"]["deployLockParam"]) { $p["ssm"]["deployLockParam"] } else { "/academy/deploy-lock" }

    $script:EcrApiRepo = $p["ecr"]["apiRepo"]
    $script:VideoWorkerRepo = $p["ecr"]["videoWorkerRepo"]
    $script:EcrMessagingRepo = $p["ecr"]["messagingWorkerRepo"]
    $script:EcrAiRepo = $p["ecr"]["aiWorkerRepo"]
    $script:EcrBaseRepo = if ($p["ecr"]["baseRepo"]) { $p["ecr"]["baseRepo"] } else { "academy-base" }
    $script:EcrImmutableTagRequired = ($p["ecr"]["immutableTagRequired"] -eq "true")

    $script:ApiAllocationId = Get-ParamFromRaw $raw "allocationId"
    if (-not $script:ApiAllocationId) { $script:ApiAllocationId = "" }
    $script:ApiPublicIp = Get-ParamFromRaw $raw "publicIp"
    if (-not $script:ApiPublicIp) { $script:ApiPublicIp = "" }
    $script:ApiContainerName = $p["api"]["containerName"]
    $script:ApiBaseUrl = if ($p["api"]["apiBaseUrl"]) { $p["api"]["apiBaseUrl"] } else { "" }
    $script:ApiInstanceTagKey = $p["api"]["instanceTagKey"]
    $script:ApiInstanceTagValue = $p["api"]["instanceTagValue"]
    $script:ApiAmiId = $p["api"]["amiId"]
    $script:ApiInstanceProfile = $p["api"]["instanceProfile"]
    $script:ApiSubnetId = $p["api"]["subnetId"]
    $script:ApiSecurityGroupId = if ($p["api"]["securityGroupId"]) { $p["api"]["securityGroupId"] } else { $script:SecurityGroupApp }
    if (-not $script:ApiSecurityGroupId) { $script:ApiSecurityGroupId = $script:BatchSecurityGroupId }
    $script:ApiInstanceType = if ($p["api"]["instanceType"]) { $p["api"]["instanceType"] } else { "t4g.medium" }
    if (-not $script:ApiSubnetId -and $script:PrivateSubnets -and $script:PrivateSubnets.Count -gt 0) { $script:ApiSubnetId = $script:PrivateSubnets[0] }
    if (-not $script:ApiSubnetId -and $script:PublicSubnets -and $script:PublicSubnets.Count -gt 0) { $script:ApiSubnetId = $script:PublicSubnets[0] }
    $script:ApiUserData = $p["api"]["userData"]
    $script:ApiASGName = $p["api"]["asgName"]
    $script:ApiLaunchTemplateName = $p["api"]["asgLaunchTemplateName"]
    $script:ApiAlbName = if ($p["api"]["albName"]) { $p["api"]["albName"] } else { "" }
    $script:ApiTargetGroupName = if ($p["api"]["targetGroupName"]) { $p["api"]["targetGroupName"] } else { "" }
    $script:ApiHealthPath = if ($p["api"]["healthPath"]) { $p["api"]["healthPath"] } else { "/health" }
    $script:ApiASGMinSize = 1
    $script:ApiASGMaxSize = 2
    $script:ApiASGDesiredCapacity = 1
    if ($p["api"]["asgMinSize"]) { $script:ApiASGMinSize = [int]$p["api"]["asgMinSize"] }
    if ($p["api"]["asgMaxSize"]) { $script:ApiASGMaxSize = [int]$p["api"]["asgMaxSize"] }
    if ($p["api"]["asgDesiredCapacity"]) { $script:ApiASGDesiredCapacity = [int]$p["api"]["asgDesiredCapacity"] }

    $script:BuildTagKey = $p["build"]["instanceTagKey"] = $p["build"]["instanceTagKey"]
    $script:BuildTagValue = $p["build"]["instanceTagValue"]
    $script:BuildAmiId = $p["build"]["amiId"]
    $script:BuildInstanceProfile = $p["build"]["instanceProfile"]
    $script:BuildSubnetId = $p["build"]["subnetId"]
    $script:BuildSecurityGroupId = $p["build"]["securityGroupId"]
    $script:BuildInstanceType = if ($p["build"]["instanceType"]) { $p["build"]["instanceType"] } else { "t4g.medium" }
    if (-not $script:BuildSubnetId -and $script:PrivateSubnets -and $script:PrivateSubnets.Count -gt 0) { $script:BuildSubnetId = $script:PrivateSubnets[0] }
    if (-not $script:BuildSubnetId -and $script:PublicSubnets -and $script:PublicSubnets.Count -gt 0) { $script:BuildSubnetId = $script:PublicSubnets[0] }
    if (-not $script:BuildSecurityGroupId) { $script:BuildSecurityGroupId = $script:SecurityGroupApp }
    if (-not $script:BuildSecurityGroupId) { $script:BuildSecurityGroupId = $script:BatchSecurityGroupId }
    $script:BuildRepoPath = if ($p["build"]["repoPath"]) { $p["build"]["repoPath"] } else { "" }

    $script:MessagingASGName = $p["messagingWorker"]["asgName"]
    $script:MessagingLaunchTemplateName = $p["messagingWorker"]["launchTemplateName"]
    $script:MessagingAmiId = $p["messagingWorker"]["amiId"]
    $script:MessagingInstanceType = if ($p["messagingWorker"]["instanceType"]) { $p["messagingWorker"]["instanceType"] } else { "t4g.medium" }
    $script:MessagingMinSize = Coerce-Int $p["messagingWorker"]["minSize"] 1
    $script:MessagingMaxSize = Coerce-Int $p["messagingWorker"]["maxSize"] 10
    $script:MessagingDesiredCapacity = Coerce-Int $p["messagingWorker"]["desiredCapacity"] 1
    $script:MessagingScaleInProtection = ($p["messagingWorker"]["scaleInProtection"] -eq $true -or $p["messagingWorker"]["scaleInProtection"] -eq "true")
    $script:MessagingScaleOutCooldown = Coerce-Int $p["messagingWorker"]["scalingPolicyScaleOutCooldown"] 300
    $script:MessagingScaleInCooldown = Coerce-Int $p["messagingWorker"]["scalingPolicyScaleInCooldown"] 900
    $script:MessagingScaleOutThreshold = Coerce-Int $p["messagingWorker"]["scalingPolicyScaleOutThreshold"] 20
    $script:MessagingScaleInThreshold = Coerce-Int $p["messagingWorker"]["scalingPolicyScaleInThreshold"] 0
    $script:MessagingSqsQueueUrl = if ($p["messagingWorker"]["sqsQueueUrl"]) { $p["messagingWorker"]["sqsQueueUrl"] } else { "" }
    $script:MessagingSqsQueueName = if ($p["messagingWorker"]["sqsQueueName"]) { $p["messagingWorker"]["sqsQueueName"] } else { "" }
    $script:AiASGName = $p["aiWorker"]["asgName"]
    $script:AiLaunchTemplateName = $p["aiWorker"]["launchTemplateName"]
    $script:AiAmiId = $p["aiWorker"]["amiId"]
    $script:AiInstanceType = if ($p["aiWorker"]["instanceType"]) { $p["aiWorker"]["instanceType"] } else { "t4g.medium" }
    $script:AiMinSize = Coerce-Int $p["aiWorker"]["minSize"] 1
    $script:AiMaxSize = Coerce-Int $p["aiWorker"]["maxSize"] 10
    $script:AiDesiredCapacity = Coerce-Int $p["aiWorker"]["desiredCapacity"] 1
    $script:AiScaleInProtection = ($p["aiWorker"]["scaleInProtection"] -eq $true -or $p["aiWorker"]["scaleInProtection"] -eq "true")
    $script:AiScaleOutCooldown = Coerce-Int $p["aiWorker"]["scalingPolicyScaleOutCooldown"] 300
    $script:AiScaleInCooldown = Coerce-Int $p["aiWorker"]["scalingPolicyScaleInCooldown"] 900
    $script:AiScaleOutThreshold = Coerce-Int $p["aiWorker"]["scalingPolicyScaleOutThreshold"] 20
    $script:AiScaleInThreshold = Coerce-Int $p["aiWorker"]["scalingPolicyScaleInThreshold"] 0
    $script:AiSqsQueueUrl = if ($p["aiWorker"]["sqsQueueUrl"]) { $p["aiWorker"]["sqsQueueUrl"] } else { "" }
    $script:AiSqsQueueName = if ($p["aiWorker"]["sqsQueueName"]) { $p["aiWorker"]["sqsQueueName"] } else { "" }

    $script:VideoCEName = $p["videoBatch"]["computeEnvironmentName"]
    $script:VideoQueueName = $p["videoBatch"]["videoQueueName"]
    $script:VideoJobDefName = $p["videoBatch"]["workerJobDefName"]
    $script:VideoCEMinvCpus = Coerce-Int $p["videoBatch"]["minvCpus"] 0
    $script:VideoCEMaxvCpus = Coerce-Int $p["videoBatch"]["maxvCpus"] 10
    $script:VideoCEInstanceType = if ($p["videoBatch"]["instanceType"]) { $p["videoBatch"]["instanceType"] } else { "c6g.large" }
    $script:OpsCEName = $p["videoBatch"]["opsComputeEnvironmentName"]
    $script:OpsQueueName = $p["videoBatch"]["opsQueueName"]
    $script:OpsJobDefReconcile = "academy-v1-video-ops-reconcile"
    $script:OpsJobDefScanStuck = "academy-v1-video-ops-scanstuck"
    $script:OpsJobDefNetprobe = "academy-v1-video-ops-netprobe"
    if ($raw -match 'reconcile:\s*([a-zA-Z0-9-]+)') { $script:OpsJobDefReconcile = $matches[1] }
    if ($raw -match 'scanstuck:\s*([a-zA-Z0-9-]+)') { $script:OpsJobDefScanStuck = $matches[1] }
    if ($raw -match 'netprobe:\s*([a-zA-Z0-9-]+)') { $script:OpsJobDefNetprobe = $matches[1] }

    $script:EventBridgeReconcileRule = $p["eventBridge"]["reconcileRuleName"]
    $script:EventBridgeScanStuckRule = $p["eventBridge"]["scanStuckRuleName"]
    $script:EventBridgeRoleName = $p["eventBridge"]["roleName"]

    $script:DynamoLockTableName = if ($p["dynamodb"]["lockTableName"]) { $p["dynamodb"]["lockTableName"] } else { "video_job_lock" }
    $script:DynamoLockTtlAttribute = if ($p["dynamodb"]["lockTableTtlAttribute"]) { $p["dynamodb"]["lockTableTtlAttribute"] } else { "ttl" }

    $script:RdsDbIdentifier = $p["rds"]["dbIdentifier"]
    if (-not $script:RdsDbIdentifier) { $script:RdsDbIdentifier = Get-ParamFromRaw $raw "dbIdentifier" }
    $script:RdsDbSubnetGroupName = $p["rds"]["dbSubnetGroupName"]
    $script:RdsEngine = if ($p["rds"]["engine"]) { $p["rds"]["engine"] } else { "postgres" }
    $script:RdsEngineVersion = if ($p["rds"]["engineVersion"]) { $p["rds"]["engineVersion"] } else { "" }
    $script:RdsInstanceClass = if ($p["rds"]["instanceClass"]) { $p["rds"]["instanceClass"] } else { "db.t4g.medium" }
    $script:RdsAllocatedStorage = Coerce-Int $p["rds"]["allocatedStorage"] 20
    $script:RdsMasterUsername = $p["rds"]["masterUsername"]
    $script:RdsMasterPasswordSsmParam = $p["rds"]["masterPasswordSsmParam"]
    if (-not $script:RdsMasterPasswordSsmParam) { $script:RdsMasterPasswordSsmParam = "" }
    if ($script:RdsDbIdentifier -and $script:RdsMasterPasswordSsmParam.Trim() -eq "") { $script:RdsMasterPasswordSsmParam = "/academy/rds/master_password" }
    $script:RedisReplicationGroupId = $p["redis"]["replicationGroupId"]
    if (-not $script:RedisReplicationGroupId) { $script:RedisReplicationGroupId = Get-ParamFromRaw $raw "replicationGroupId" }
    $script:RedisSubnetGroupName = $p["redis"]["subnetGroupName"]
    $script:RedisSecurityGroupId = $p["redis"]["securityGroupId"]
    $script:RedisNodeType = if ($p["redis"]["nodeType"]) { $p["redis"]["nodeType"] } else { "cache.t4g.small" }
    $script:RedisEngineVersion = if ($p["redis"]["engineVersion"]) { $p["redis"]["engineVersion"] } else { "" }

    $script:VideoLogGroup = "/aws/batch/academy-video-worker"
    $script:OpsLogGroup = "/aws/batch/academy-video-ops"

    $script:SSOT_CE = @($script:VideoCEName, $script:OpsCEName)
    $script:SSOT_Queue = @($script:VideoQueueName, $script:OpsQueueName)
    $script:SSOT_JobDef = @($script:VideoJobDefName, $script:OpsJobDefReconcile, $script:OpsJobDefScanStuck, $script:OpsJobDefNetprobe)
    $script:SSOT_EventBridgeRule = @($script:EventBridgeReconcileRule, $script:EventBridgeScanStuckRule)
    $script:SSOT_ASG = @($script:ApiASGName, $script:MessagingASGName, $script:AiASGName)
    $script:SSOT_RDS = @($script:RdsDbIdentifier)
    $script:SSOT_Redis = @($script:RedisReplicationGroupId)
    $script:SSOT_ECR = @($script:EcrApiRepo, $script:VideoWorkerRepo, $script:EcrMessagingRepo, $script:EcrAiRepo)
    $script:SSOT_SSM = @($script:SsmApiEnv, $script:SsmWorkersEnv)
    $script:SSOT_EIP = @()
    if ($script:ApiAllocationId) { $script:SSOT_EIP = @($script:ApiAllocationId) }
    $script:SSOT_IAMRoles = @(
        "academy-batch-service-role",
        "academy-batch-ecs-instance-role",
        "academy-batch-ecs-task-execution-role",
        "academy-video-batch-job-role",
        "academy-eventbridge-batch-video-role"
    )
    $script:SSOT_InstanceProfile = @("academy-batch-ecs-instance-profile")
    $script:SSOT_ECSClusterPatterns = @("*academy-v1-video-batch-ce*", "*academy-v1-video-ops-ce*")
}
