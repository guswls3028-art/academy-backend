# params.yaml loader — sets script: variables. Single source of truth; no env/prod.ps1.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
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
            if ($val -match '#') { $val = ($val -split '#')[0].Trim() }
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
    function Coerce-Int { param($val, $default = 0); if ($val -ne $null -and $val -ne "") { [int]$val } else { [int]$default } }
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
    switch ($true) {
        { $p["networkPublicSubnets"] -and $p["networkPublicSubnets"]["_list"] } { $script:PublicSubnets = @($p["networkPublicSubnets"]["_list"]) }
        { $n["_list"] } { $script:PublicSubnets = @($n["_list"]) }
    }
    $script:PrivateSubnets = @()
    switch ($true) {
        { $p["networkPrivateSubnets"] -and $p["networkPrivateSubnets"]["_list"] } { $script:PrivateSubnets = @($p["networkPrivateSubnets"]["_list"]) }
    }
    $script:NatEnabled = ($n["natEnabled"] -eq "true")
    $script:AlbEnabled = ($n["albEnabled"] -eq "true")
    $script:NatGatewayId = if ($n["natGatewayId"]) { $n["natGatewayId"] } else { "" }
    $script:SecurityGroupApp = if ($n["securityGroupApp"]) { $n["securityGroupApp"] } else { "" }
    $script:BatchSecurityGroupId = if ($n["securityGroupBatch"]) { $n["securityGroupBatch"] } else { "" }
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
    $script:EcrUseLatestTag = ($p["ecr"]["useLatestTag"] -eq "true")

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
    $script:ApiASGMinSize = 2
    $script:ApiASGMaxSize = 4
    $script:ApiASGDesiredCapacity = 2
    if ($p["api"]["asgMinSize"]) { $script:ApiASGMinSize = [int]$p["api"]["asgMinSize"] }
    if ($p["api"]["asgMaxSize"]) { $script:ApiASGMaxSize = [int]$p["api"]["asgMaxSize"] }
    if ($p["api"]["asgDesiredCapacity"]) { $script:ApiASGDesiredCapacity = [int]$p["api"]["asgDesiredCapacity"] }
    $script:ApiInstanceRefreshMinHealthyPercentage = Coerce-Int $p["api"]["instanceRefreshMinHealthyPercentage"] 100
    $script:ApiInstanceRefreshInstanceWarmup = Coerce-Int $p["api"]["instanceRefreshInstanceWarmup"] 300

    # Build server DEPRECATED: 빌드는 GitHub Actions에서만 수행한다.
    $script:BuildTagKey = ""
    $script:BuildTagValue = ""
    $script:BuildAmiId = ""
    $script:BuildInstanceProfile = ""
    $script:BuildSubnetId = ""
    $script:BuildSecurityGroupId = ""
    $script:BuildInstanceType = ""
    $script:BuildRepoPath = ""

    $script:MessagingASGName = $p["messagingWorker"]["asgName"]
    $script:MessagingLaunchTemplateName = $p["messagingWorker"]["launchTemplateName"]
    $script:MessagingInstanceTagValue = if ($p["messagingWorker"]["instanceTagValue"]) { $p["messagingWorker"]["instanceTagValue"] } else { "academy-v1-messaging-worker" }
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
    $script:MessagingVisibilityTimeoutSeconds = Coerce-Int $p["messagingWorker"]["visibilityTimeoutSeconds"] 900
    $script:AiASGName = $p["aiWorker"]["asgName"]
    $script:AiLaunchTemplateName = $p["aiWorker"]["launchTemplateName"]
    $script:AiInstanceTagValue = if ($p["aiWorker"]["instanceTagValue"]) { $p["aiWorker"]["instanceTagValue"] } else { "academy-v1-ai-worker" }
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
    $script:AiVisibilityTimeoutSeconds = Coerce-Int $p["aiWorker"]["visibilityTimeoutSeconds"] 1800

    $vb = $p["videoBatch"]
    $vbs = if ($vb["standard"]) { $vb["standard"] } else { $vb }
    $vbl = $vb["long"]
    $script:VideoCEName = if ($vbs["computeEnvironmentName"]) { $vbs["computeEnvironmentName"] } else { "academy-v1-video-batch-ce" }
    $script:VideoQueueName = if ($vbs["videoQueueName"]) { $vbs["videoQueueName"] } else { "academy-v1-video-batch-queue" }
    $script:VideoJobDefName = if ($vbs["workerJobDefName"]) { $vbs["workerJobDefName"] } else { "academy-v1-video-batch-jobdef" }
    $script:VideoCEMinvCpus = Coerce-Int $(if ($vbs["minvCpus"]) { $vbs["minvCpus"] } else { 0 }) 0
    $script:VideoCEMaxvCpus = Coerce-Int $(if ($vbs["maxvCpus"]) { $vbs["maxvCpus"] } else { 40 }) 40
    $script:VideoCEInstanceType = if ($vbs["instanceType"]) { $vbs["instanceType"] } else { "c6g.xlarge" }
    $script:VideoCERootVolumeSizeGb = Coerce-Int $(if ($vbs["rootVolumeSizeGb"]) { $vbs["rootVolumeSizeGb"] } else { 200 }) 200
    $script:VideoJobTimeoutStandardSeconds = Coerce-Int $(if ($vb["jobTimeoutStandardSeconds"]) { $vb["jobTimeoutStandardSeconds"] } elseif ($vbs["jobTimeoutSeconds"]) { $vbs["jobTimeoutSeconds"] } else { 21600 }) 21600
    $script:VideoStuckHeartbeatAgeStandardMinutes = Coerce-Int $(if ($vb["stuckHeartbeatAgeStandardMinutes"]) { $vb["stuckHeartbeatAgeStandardMinutes"] } elseif ($vbs["stuckHeartbeatAgeMinutes"]) { $vbs["stuckHeartbeatAgeMinutes"] } else { 20 }) 20
    $script:VideoUseSpot = ($vbs["useSpot"] -eq $true -or $vbs["useSpot"] -eq "true")
    if ($vbl) {
        $script:VideoLongCEName = $vbl["computeEnvironmentName"]
        $script:VideoLongQueueName = $vbl["videoQueueName"]
        $script:VideoLongJobDefName = $vbl["workerJobDefName"]
        $script:VideoLongMinvCpus = Coerce-Int $vbl["minvCpus"] 0
        $script:VideoLongMaxvCpus = Coerce-Int $vbl["maxvCpus"] 80
        $script:VideoLongInstanceType = if ($vbl["instanceType"]) { $vbl["instanceType"] } else { "c6g.xlarge" }
        $script:VideoLongRootVolumeSizeGb = Coerce-Int $(if ($vbl["rootVolumeSizeGb"]) { $vbl["rootVolumeSizeGb"] } else { 300 }) 300
        $script:VideoJobTimeoutLongSeconds = Coerce-Int $(if ($vb["jobTimeoutLongSeconds"]) { $vb["jobTimeoutLongSeconds"] } elseif ($vbl["jobTimeoutSeconds"]) { $vbl["jobTimeoutSeconds"] } else { 43200 }) 43200
        $script:VideoStuckHeartbeatAgeLongMinutes = Coerce-Int $(if ($vb["stuckHeartbeatAgeLongMinutes"]) { $vb["stuckHeartbeatAgeLongMinutes"] } elseif ($vbl["stuckHeartbeatAgeMinutes"]) { $vbl["stuckHeartbeatAgeMinutes"] } else { 45 }) 45
        $script:VideoLongUseSpot = ($vbl["useSpot"] -eq $true -or $vbl["useSpot"] -eq "true")
    } else {
        $script:VideoLongCEName = if ($vb["longComputeEnvironmentName"]) { $vb["longComputeEnvironmentName"] } else { $null }
        $script:VideoLongQueueName = if ($vb["longQueueName"]) { $vb["longQueueName"] } else { $null }
        $script:VideoLongJobDefName = if ($vb["longWorkerJobDefName"]) { $vb["longWorkerJobDefName"] } else { $null }
    }
    $script:OpsCEName = $p["videoBatch"]["opsComputeEnvironmentName"]
    $script:OpsQueueName = $p["videoBatch"]["opsQueueName"]
    $script:OpsCEInstanceType = if ($p["videoBatch"]["opsInstanceType"]) { $p["videoBatch"]["opsInstanceType"] } else { "t4g.medium" }
    $script:OpsCEMaxvCpus = Coerce-Int $p["videoBatch"]["opsMaxvCpus"] 2
    $script:OpsJobDefReconcile = "academy-v1-video-ops-reconcile"
    $script:OpsJobDefScanStuck = "academy-v1-video-ops-scanstuck"
    $script:OpsJobDefNetprobe = "academy-v1-video-ops-netprobe"
    if ($raw -match 'reconcile:\s*([a-zA-Z0-9-]+)') { $script:OpsJobDefReconcile = $matches[1] }
    if ($raw -match 'scanstuck:\s*([a-zA-Z0-9-]+)') { $script:OpsJobDefScanStuck = $matches[1] }
    if ($raw -match 'netprobe:\s*([a-zA-Z0-9-]+)') { $script:OpsJobDefNetprobe = $matches[1] }

    $script:EventBridgeReconcileRule = $p["eventBridge"]["reconcileRuleName"]
    $script:EventBridgeScanStuckRule = $p["eventBridge"]["scanStuckRuleName"]
    $script:EventBridgeRoleName = $p["eventBridge"]["roleName"]
    $script:EventBridgeReconcileSchedule = if ($p["eventBridge"]["reconcileSchedule"]) { $p["eventBridge"]["reconcileSchedule"] } else { "rate(15 minutes)" }
    $script:EventBridgeScanStuckSchedule = if ($p["eventBridge"]["scanStuckSchedule"]) { $p["eventBridge"]["scanStuckSchedule"] } else { "rate(5 minutes)" }
    $script:EventBridgeReconcileState = if ($p["eventBridge"]["reconcileState"]) { $p["eventBridge"]["reconcileState"] } else { "ENABLED" }
    $script:EventBridgeScanStuckState = if ($p["eventBridge"]["scanStuckState"]) { $p["eventBridge"]["scanStuckState"] } else { "ENABLED" }

    $script:DynamoLockTableName = if ($p["dynamodb"]["lockTableName"]) { $p["dynamodb"]["lockTableName"] } else { "video_job_lock" }
    $script:DynamoLockTtlAttribute = if ($p["dynamodb"]["lockTableTtlAttribute"]) { $p["dynamodb"]["lockTableTtlAttribute"] } else { "ttl" }
    $script:DynamoUploadCheckpointTableName = if ($p["dynamodb"]["uploadCheckpointTableName"]) { $p["dynamodb"]["uploadCheckpointTableName"] } else { "academy-v1-video-upload-checkpoints" }

    # videoBatch.observability.logRetentionDays (nested key; fallback from raw)
    $script:VideoBatchLogRetentionDays = 30
    if ($raw -match 'logRetentionDays:\s*(\d+)') { $script:VideoBatchLogRetentionDays = [int]$matches[1] }

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
    $script:RdsPerformanceInsightsEnabled = ($p["rds"]["performanceInsightsEnabled"] -eq "true")
    $script:RdsPerformanceInsightsRetentionDays = Coerce-Int $p["rds"]["performanceInsightsRetentionDays"] 7
    $script:RdsMultiAz = ($p["rds"]["multiAz"] -eq "true")
    $script:RedisReplicationGroupId = $p["redis"]["replicationGroupId"]
    if (-not $script:RedisReplicationGroupId) { $script:RedisReplicationGroupId = Get-ParamFromRaw $raw "replicationGroupId" }
    $script:RedisSubnetGroupName = $p["redis"]["subnetGroupName"]
    $script:RedisSecurityGroupId = $p["redis"]["securityGroupId"]
    $script:RedisNodeType = if ($p["redis"]["nodeType"]) { $p["redis"]["nodeType"] } else { "cache.t4g.small" }
    $script:RedisEngineVersion = if ($p["redis"]["engineVersion"]) { $p["redis"]["engineVersion"] } else { "" }

    $script:R2Bucket = if ($p["r2"]) { $p["r2"]["bucket"] } else { "" }
    $script:R2PublicBaseUrl = if ($p["r2"]) { $p["r2"]["publicBaseUrl"] } else { "" }
    $script:FrontDomainApp = ""
    $script:FrontDomainApi = ""
    $script:FrontCorsAllowedOrigins = @()
    $script:FrontR2StaticBucket = ""
    $script:FrontR2StaticPrefix = "static/front"
    $script:FrontPurgeOnDeploy = $false
    if ($p["front"]) {
        # flat 키 우선 (2-level 파서에서 domains.app/api 미파싱 대비)
        if ($p["front"]["domainsApi"] -and $p["front"]["domainsApi"].Trim() -ne "") { $script:FrontDomainApi = $p["front"]["domainsApi"].Trim() }
        if ($p["front"]["domainsApp"] -and $p["front"]["domainsApp"].Trim() -ne "") { $script:FrontDomainApp = $p["front"]["domainsApp"].Trim() }
        if ($p["front"]["domains"]) {
            if (-not $script:FrontDomainApp -and $p["front"]["domains"]["app"]) { $script:FrontDomainApp = $p["front"]["domains"]["app"] }
            if (-not $script:FrontDomainApi -and $p["front"]["domains"]["api"]) { $script:FrontDomainApi = $p["front"]["domains"]["api"] }
        }
        $script:FrontR2StaticBucket = if ($p["front"]["r2StaticBucket"]) { $p["front"]["r2StaticBucket"] } else { "" }
        $script:FrontR2StaticPrefix = if ($p["front"]["r2StaticPrefix"]) { $p["front"]["r2StaticPrefix"] } else { "static/front" }
        $script:FrontPurgeOnDeploy = ($p["front"]["purgeOnDeploy"] -eq $true -or $p["front"]["purgeOnDeploy"] -eq "true")
        if ($p["front"]["cors"] -and $p["front"]["cors"]["_list"]) { $script:FrontCorsAllowedOrigins = @($p["front"]["cors"]["_list"]) }
        elseif ($p["front"]["cors"] -and $p["front"]["cors"]["allowedOrigins"]) { $script:FrontCorsAllowedOrigins = @($p["front"]["cors"]["allowedOrigins"]) }
    }
    $script:MessagingDlqSuffix = if ($p["messagingWorker"]["dlqSuffix"]) { $p["messagingWorker"]["dlqSuffix"] } else { "-dlq" }
    $script:AiDlqSuffix = if ($p["aiWorker"]["dlqSuffix"]) { $p["aiWorker"]["dlqSuffix"] } else { "-dlq" }

    $obs = $p["observability"]
    if (-not $obs) { $obs = @{} }
    $script:ObservabilityLogRetentionDays = Coerce-Int $obs["logRetentionDays"] 30
    $script:ObservabilityAlarmPeriodSeconds = Coerce-Int $obs["alarmPeriodSeconds"] 300
    $script:ObservabilityAlarmEvaluationPeriods = Coerce-Int $obs["alarmEvaluationPeriods"] 2
    $script:ObservabilityApiAlb5xxThreshold = Coerce-Int $obs["apiAlb5xxThreshold"] 10
    $script:ObservabilitySqsQueueDepthThreshold = Coerce-Int $obs["sqsQueueDepthThreshold"] 100
    $script:ObservabilitySqsDlqDepthThreshold = Coerce-Int $obs["sqsDlqDepthThreshold"] 5
    $script:ObservabilityRdsCpuThresholdPercent = Coerce-Int $obs["rdsCpuThresholdPercent"] 80
    $script:ObservabilityRdsFreeStorageGbThreshold = Coerce-Int $obs["rdsFreeStorageGbThreshold"] 5
    $script:ObservabilityRdsConnectionsThreshold = Coerce-Int $obs["rdsConnectionsThreshold"] 90
    $script:ObservabilityRedisCpuThresholdPercent = Coerce-Int $obs["redisCpuThresholdPercent"] 75

    $script:VideoLogGroup = "/aws/batch/academy-video-worker"
    $script:OpsLogGroup = "/aws/batch/academy-video-ops"

    $script:SSOT_CE = @($script:VideoCEName, $script:OpsCEName)
    $script:SSOT_Queue = @($script:VideoQueueName, $script:OpsQueueName)
    $script:SSOT_JobDef = @($script:VideoJobDefName, $script:OpsJobDefReconcile, $script:OpsJobDefScanStuck, $script:OpsJobDefNetprobe)
    if ($script:VideoLongCEName) {
        $script:SSOT_CE = @($script:VideoCEName, $script:VideoLongCEName, $script:OpsCEName)
        $script:SSOT_Queue = @($script:VideoQueueName, $script:VideoLongQueueName, $script:OpsQueueName)
        $script:SSOT_JobDef = @($script:VideoJobDefName, $script:VideoLongJobDefName, $script:OpsJobDefReconcile, $script:OpsJobDefScanStuck, $script:OpsJobDefNetprobe)
    }
    $script:SSOT_EventBridgeRule = @($script:EventBridgeReconcileRule, $script:EventBridgeScanStuckRule)
    $script:SSOT_ASG = @($script:ApiASGName, $script:MessagingASGName, $script:AiASGName)
    $script:SSOT_RDS = @($script:RdsDbIdentifier)
    $script:SSOT_Redis = @($script:RedisReplicationGroupId)
    $script:SSOT_ECR = @($script:EcrApiRepo, $script:VideoWorkerRepo, $script:EcrMessagingRepo, $script:EcrAiRepo)
    $script:SSOT_SSM = @($script:SsmApiEnv, $script:SsmWorkersEnv)
    if ($script:RdsMasterPasswordSsmParam -and $script:RdsMasterPasswordSsmParam.Trim() -ne "" -and $script:RdsMasterPasswordSsmParam -notin $script:SSOT_SSM) {
        $script:SSOT_SSM = @($script:SsmApiEnv, $script:SsmWorkersEnv, $script:RdsMasterPasswordSsmParam)
    }
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
