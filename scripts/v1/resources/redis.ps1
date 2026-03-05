# Redis: Ensure ElastiCache replication group in private subnets + sg-data. No delete; Strict 게이트.
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
        "--cache-subnet-group-description", "Academy v4 Redis (FD1)",
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
        return
    }
    $newSgs = @($sgIds + $script:SecurityGroupData | Select-Object -Unique)
    Write-Host "  Updating Redis SGs to include sg-data $($script:SecurityGroupData)" -ForegroundColor Yellow
    Invoke-Aws @(
        "elasticache", "modify-replication-group",
        "--replication-group-id", $script:RedisReplicationGroupId,
        "--security-group-ids"
    ) + $newSgs + @(
        "--apply-immediately",
        "--region", $script:Region
    ) -ErrorMessage "modify Redis SGs" | Out-Null
    $script:ChangesMade = $true
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
            "--replication-group-description", "Academy v4 Redis (FD1)",
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
