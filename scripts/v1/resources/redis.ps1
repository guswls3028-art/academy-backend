# Redis: Ensure ElastiCache replication group in private subnets + sg-data. No delete; Strict 게이트.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"

function Get-RedisSubnetGroupName {
    if (-not $script:RedisSubnetGroupName) {
        throw "redis.subnetGroupName is required in params.yaml for Redis subnet group."
    }
    return $script:RedisSubnetGroupName
}

function Ensure-RedisSubnetGroup {
    if ($script:PlanMode) { Write-Ok "Redis subnet group ensure skipped (Plan)"; return }
    $name = Get-RedisSubnetGroupName
    $existing = $null
    try {
        $r = Invoke-AwsJson @("elasticache", "describe-cache-subnet-groups", "--cache-subnet-group-name", $name, "--region", $script:Region, "--output", "json")
        if ($r -and $r.CacheSubnetGroups -and $r.CacheSubnetGroups.Count -gt 0) { $existing = $r.CacheSubnetGroups[0] }
    } catch {
        if ($_.Exception.Message -notmatch "CacheSubnetGroupNotFoundFault|CacheSubnetGroupNotFound") { throw }
    }
    if ($existing) {
        Write-Ok "Redis cache subnet group $name exists"
        return
    }
    $subnetIds = @($script:PrivateSubnets | Where-Object { $_ })
    if (-not $subnetIds -or $subnetIds.Count -lt 2) {
        throw "At least 2 private subnets required for Redis cache subnet group (have $($subnetIds.Count)). Ensure-Network must run first."
    }
    $args = @(
        "elasticache", "create-cache-subnet-group",
        "--cache-subnet-group-name", $name,
        "--cache-subnet-group-description", "Academy v1 Redis",
        "--subnet-ids"
    ) + $subnetIds + @(
        "--region", $script:Region, "--output", "json"
    )
    $create = Invoke-AwsJson $args
    if (-not $create -or -not $create.CacheSubnetGroup) { throw "create-cache-subnet-group failed for $name" }
    Write-Ok "Redis cache subnet group $name created"
    $script:ChangesMade = $true
}

function Ensure-RedisSecurityGroup {
    if ($script:PlanMode) { return }
    if (-not $script:RedisReplicationGroupId) { return }
    if (-not $script:SecurityGroupData) { return }
    $redis = Invoke-AwsJson @("elasticache", "describe-replication-groups", "--replication-group-id", $script:RedisReplicationGroupId, "--region", $script:Region, "--output", "json")
    if (-not $redis -or -not $redis.ReplicationGroups -or $redis.ReplicationGroups.Count -eq 0) { return }
    $rg = $redis.ReplicationGroups[0]
    $sgIds = @($rg.SecurityGroups | ForEach-Object { $_.SecurityGroupId })
    if (-not $sgIds) { $sgIds = @() }
    if ($sgIds -contains $script:SecurityGroupData) {
        Write-Ok "Redis SGs already include sg-data $($script:SecurityGroupData)"
    } else {
        $newSgs = @($sgIds + $script:SecurityGroupData | Select-Object -Unique)
        Write-Host "  Updating Redis SGs to include sg-data $($script:SecurityGroupData)" -ForegroundColor Yellow
        try {
            Invoke-Aws @(
                "elasticache", "modify-replication-group",
                "--replication-group-id", $script:RedisReplicationGroupId,
                "--security-group-ids"
            ) + $newSgs + @(
                "--apply-immediately",
                "--region", $script:Region
            ) -ErrorMessage "modify Redis SGs" | Out-Null
            $script:ChangesMade = $true
        } catch {
            if ($_.Exception.Message -match "No modifications were requested|InvalidParameterCombination") {
                Write-Ok "Redis SGs unchanged (already include sg-data or same set)"
            } else { throw }
        }
    }
    # Redis SG(s)에 Batch SG, App SG 6379 인바운드 보장 (프로그래스바: 워커 기록, API 조회)
    Ensure-RedisSg6379FromWorkersAndApi -SgIds $sgIds
}

function Ensure-RedisSg6379FromWorkersAndApi {
    param([string[]]$SgIds)
    if ($script:PlanMode -or -not $SgIds) { return }
    $sourceSgs = @()
    if ($script:BatchSecurityGroupId) { $sourceSgs += $script:BatchSecurityGroupId }
    if ($script:SecurityGroupApp) { $sourceSgs += $script:SecurityGroupApp }
    if ($sourceSgs.Count -eq 0) { return }
    foreach ($sgId in $SgIds) {
        if (-not $sgId -or $sgId -notmatch '^sg-') { continue }
        $desc = Invoke-AwsJson @("ec2", "describe-security-groups", "--group-ids", $sgId, "--region", $script:Region, "--output", "json") 2>$null
        if (-not $desc -or -not $desc.SecurityGroups -or $desc.SecurityGroups.Count -eq 0) { continue }
        $existingRefs = @()
        foreach ($perm in $desc.SecurityGroups[0].IpPermissions) {
            if ($perm.FromPort -eq 6379 -and $perm.ToPort -eq 6379) {
                foreach ($ref in $perm.UserIdGroupPairs) { $existingRefs += $ref.GroupId }
            }
        }
        foreach ($srcSg in $sourceSgs) {
            if ($existingRefs -contains $srcSg) { continue }
            try {
                Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $sgId, "--protocol", "tcp", "--port", "6379", "--source-group", $srcSg, "--region", $script:Region) -ErrorMessage "Redis SG $sgId 6379 from $srcSg" | Out-Null
                Write-Ok "Redis SG ${sgId}: added 6379 from $srcSg"
                $script:ChangesMade = $true
            } catch { if ($_.Exception.Message -notmatch "Duplicate|InvalidPermission.Duplicate") { throw } }
        }
    }
}

function Confirm-RedisState {
    Write-Step "Ensure Redis $($script:RedisReplicationGroupId)"
    if ($script:PlanMode) {
        Write-Ok "Redis ensure skipped (Plan)"
        return
    }
    if (-not $script:RedisReplicationGroupId) {
        throw "redis.replicationGroupId is required in params.yaml."
    }
    if (-not $script:SecurityGroupData) {
        throw "SecurityGroupData (sg-data) must be set before Redis ensure. Ensure-Network must run first."
    }

    Ensure-RedisSubnetGroup

    $rg = $null
    try {
        $redis = Invoke-AwsJson @("elasticache", "describe-replication-groups", "--replication-group-id", $script:RedisReplicationGroupId, "--region", $script:Region, "--output", "json")
        if ($redis -and $redis.ReplicationGroups -and $redis.ReplicationGroups.Count -gt 0) { $rg = $redis.ReplicationGroups[0] }
    } catch {
        if ($_.Exception.Message -notmatch "ReplicationGroupNotFound") { throw }
    }

    if (-not $rg) {
        $nodeType = $script:RedisNodeType
        $engineArgs = @()
        if ($script:RedisEngineVersion) { $engineArgs += @("--engine-version", $script:RedisEngineVersion) }
        $createArgs = @(
            "elasticache", "create-replication-group",
            "--replication-group-id", $script:RedisReplicationGroupId,
            "--replication-group-description", "Academy v1 Redis",
            "--engine", "redis",
            "--cache-node-type", $nodeType,
            "--cache-subnet-group-name", (Get-RedisSubnetGroupName),
            "--security-group-ids", $script:SecurityGroupData,
            "--num-cache-clusters", "1"
        ) + $engineArgs + @(
            "--region", $script:Region, "--output", "json"
        )
        $create = Invoke-AwsJson $createArgs
        if (-not $create -or -not $create.ReplicationGroup) {
            throw "create-replication-group failed for $($script:RedisReplicationGroupId)"
        }
        $rg = $create.ReplicationGroup
        Write-Ok "Redis $($script:RedisReplicationGroupId) creating (status=$($rg.Status))"
        $script:ChangesMade = $true
    }

    $timeoutSec = 1800
    $elapsed = 0
    while ($elapsed -lt $timeoutSec) {
        $redis = Invoke-AwsJson @("elasticache", "describe-replication-groups", "--replication-group-id", $script:RedisReplicationGroupId, "--region", $script:Region, "--output", "json")
        if ($redis -and $redis.ReplicationGroups -and $redis.ReplicationGroups.Count -gt 0) {
            $rg = $redis.ReplicationGroups[0]
            $status = $rg.Status
            Write-Host "  Redis status=$status" -ForegroundColor Gray
            if ($status -eq "available") {
                $ep = $rg.NodeGroups[0].PrimaryEndpoint
                Write-Ok "Redis $($script:RedisReplicationGroupId) available (primary: $($ep.Address):$($ep.Port))"
                Ensure-RedisSecurityGroup
                return
            }
        }
        Start-Sleep -Seconds 30
        $elapsed += 30
    }
    throw "Redis $($script:RedisReplicationGroupId) did not reach available status in ${timeoutSec}s."
}
