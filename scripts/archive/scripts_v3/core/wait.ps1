# Wait until CE is deleted (not in describe list). Timeout 300s, poll 10s. Poll-based only, no fixed sleep.
function Wait-CEDeleted {
    param([string]$CEName, [string]$Reg, [int]$TimeoutSec = 300)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $r = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $CEName, "--region", $Reg, "--output", "json")
        if (-not $r -or -not $r.computeEnvironments -or $r.computeEnvironments.Count -eq 0) {
            Write-Ok "CE $CEName deleted"
            return
        }
        Write-Host "  Waiting for CE $CEName to be deleted..." -ForegroundColor Gray
        Start-Sleep -Seconds 10
        $elapsed += 10
    }
    throw "Timeout waiting for CE $CEName to be deleted (${TimeoutSec}s)"
}

# Wait until EventBridge rule no longer exists. Poll describe-rule until NotFound.
function Wait-EventBridgeRuleDeleted {
    param([string]$RuleName, [string]$Reg, [int]$TimeoutSec = 120)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        try {
            $r = Invoke-AwsJson @("events", "describe-rule", "--name", $RuleName, "--region", $Reg, "--output", "json")
            if (-not $r) { Write-Ok "EventBridge rule $RuleName deleted"; return }
        } catch {
            if ($_.Exception.Message -match "ResourceNotFoundException|not found") { Write-Ok "EventBridge rule $RuleName deleted"; return }
        }
        Write-Host "  Waiting for EventBridge rule $RuleName to be deleted..." -ForegroundColor Gray
        Start-Sleep -Seconds 5
        $elapsed += 5
    }
    throw "Timeout waiting for EventBridge rule $RuleName to be deleted (${TimeoutSec}s)"
}

# Wait until Batch job queue no longer in describe list.
function Wait-QueueDeleted {
    param([string]$QueueName, [string]$Reg, [int]$TimeoutSec = 180)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $r = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $QueueName, "--region", $Reg, "--output", "json")
        if (-not $r -or -not $r.jobQueues -or $r.jobQueues.Count -eq 0) {
            Write-Ok "Queue $QueueName deleted"
            return
        }
        Write-Host "  Waiting for Queue $QueueName to be deleted..." -ForegroundColor Gray
        Start-Sleep -Seconds 10
        $elapsed += 10
    }
    throw "Timeout waiting for Queue $QueueName to be deleted (${TimeoutSec}s)"
}

# Wait until ASG no longer in describe list.
function Wait-ASGDeleted {
    param([string]$ASGName, [string]$Reg, [int]$TimeoutSec = 300)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $r = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $ASGName, "--region", $Reg, "--output", "json")
        if (-not $r -or -not $r.AutoScalingGroups -or $r.AutoScalingGroups.Count -eq 0) {
            Write-Ok "ASG $ASGName deleted"
            return
        }
        Write-Host "  Waiting for ASG $ASGName to be deleted..." -ForegroundColor Gray
        Start-Sleep -Seconds 10
        $elapsed += 10
    }
    throw "Timeout waiting for ASG $ASGName to be deleted (${TimeoutSec}s)"
}

# Wait until ECS cluster status INACTIVE or not in list.
function Wait-ECSClusterDeleted {
    param([string]$ClusterName, [string]$Reg, [int]$TimeoutSec = 120)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $r = Invoke-AwsJson @("ecs", "describe-clusters", "--clusters", $ClusterName, "--region", $Reg, "--output", "json")
        if (-not $r -or -not $r.clusters -or $r.clusters.Count -eq 0) { Write-Ok "ECS cluster $ClusterName deleted"; return }
        $c = $r.clusters[0]
        if ($c.status -eq "INACTIVE") { Write-Ok "ECS cluster $ClusterName INACTIVE"; return }
        Write-Host "  Waiting for ECS cluster $ClusterName to be deleted..." -ForegroundColor Gray
        Start-Sleep -Seconds 5
        $elapsed += 5
    }
    throw "Timeout waiting for ECS cluster $ClusterName to be deleted (${TimeoutSec}s)"
}

# Wait until IAM role no longer exists.
function Wait-IAMRoleDeleted {
    param([string]$RoleName, [int]$TimeoutSec = 60)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        try {
            Invoke-AwsJson @("iam", "get-role", "--role-name", $RoleName, "--output", "json") | Out-Null
        } catch {
            if ($_.Exception.Message -match "NoSuchEntity|not found") { Write-Ok "IAM role $RoleName deleted"; return }
        }
        Write-Host "  Waiting for IAM role $RoleName to be deleted..." -ForegroundColor Gray
        Start-Sleep -Seconds 5
        $elapsed += 5
    }
    throw "Timeout waiting for IAM role $RoleName to be deleted (${TimeoutSec}s)"
}

# Wait until CE status=VALID and state=ENABLED. Timeout 600s.
function Wait-CEValidEnabled {
    param([string]$CEName, [string]$Reg, [int]$TimeoutSec = 600)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $r = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $CEName, "--region", $Reg, "--output", "json")
        if (-not $r -or -not $r.computeEnvironments -or $r.computeEnvironments.Count -eq 0) {
            Start-Sleep -Seconds 15
            $elapsed += 15
            continue
        }
        $ce = $r.computeEnvironments[0]
        $status = $ce.status
        $state = $ce.state
        Write-Host "  CE $CEName status=$status state=$state" -ForegroundColor Gray
        if ($ce.statusReason -and $ce.statusReason -like "*INVALID*") {
            throw "CE $CEName statusReason indicates INVALID: $($ce.statusReason)"
        }
        if ($status -eq "VALID" -and $state -eq "ENABLED") {
            Write-Ok "CE $CEName VALID and ENABLED"
            return
        }
        if ($status -eq "INVALID") {
            throw "CE $CEName is INVALID. statusReason: $($ce.statusReason)"
        }
        Start-Sleep -Seconds 15
        $elapsed += 15
    }
    throw "Timeout waiting for CE $CEName VALID/ENABLED (${TimeoutSec}s)"
}
