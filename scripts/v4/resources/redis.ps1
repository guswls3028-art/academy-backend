# Redis: Validate only. SG ensure (Batch -> 6379). No delete.
function Confirm-RedisState {
    Write-Step "Redis $($script:RedisReplicationGroupId)"
    if ($script:PlanMode) { Write-Ok "Redis check skipped (Plan)"; return }
    $redis = Invoke-AwsJson @("elasticache", "describe-replication-groups", "--replication-group-id", $script:RedisReplicationGroupId, "--region", $script:Region, "--output", "json")
    if (-not $redis -or -not $redis.ReplicationGroups -or $redis.ReplicationGroups.Count -eq 0) {
        Write-Warn "Redis $($script:RedisReplicationGroupId) not found"
        return
    }
    $rg = $redis.ReplicationGroups[0]
    $ep = $rg.NodeGroups[0].PrimaryEndpoint
    Write-Ok "Redis $($script:RedisReplicationGroupId) Status=$($rg.Status)"
}

function Ensure-RedisSecurityGroup {
    if ($script:PlanMode) { return }
    $redis = Invoke-AwsJson @("elasticache", "describe-replication-groups", "--replication-group-id", $script:RedisReplicationGroupId, "--region", $script:Region, "--output", "json")
    if (-not $redis -or -not $redis.ReplicationGroups -or $redis.ReplicationGroups.Count -eq 0) { return }
    $sgIds = $redis.ReplicationGroups[0].SecurityGroups | ForEach-Object { $_.SecurityGroupId }
    foreach ($sgId in $sgIds) {
        $rules = Invoke-AwsJson @("ec2", "describe-security-groups", "--group-ids", $sgId, "--region", $script:Region, "--output", "json")
        $hasBatch = $rules.SecurityGroups[0].IpPermissions | Where-Object {
            $_.FromPort -eq 6379 -and $_.UserIdGroupPairs | Where-Object { $_.GroupId -eq $script:BatchSecurityGroupId }
        }
        if (-not $hasBatch -and $script:BatchSecurityGroupId) {
            Write-Host "  Adding Batch SG to Redis SG $sgId (6379)" -ForegroundColor Yellow
            try {
                Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $sgId, "--protocol", "tcp", "--port", "6379", "--source-group", $script:BatchSecurityGroupId, "--region", $script:Region) -ErrorMessage "Redis SG 6379" 2>$null | Out-Null
                $script:ChangesMade = $true
            } catch {
                if ($_.Exception.Message -notmatch "Duplicate|InvalidPermission\.Duplicate") { throw }
            }
        }
    }
}
