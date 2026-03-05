# Wait loops — describe-based polling only. No fixed sleep after delete.
$ErrorActionPreference = "Stop"

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

function Wait-ECSClusterDeleted {
    param([string]$ClusterName, [string]$Reg, [int]$TimeoutSec = 120)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $r = Invoke-AwsJson @("ecs", "describe-clusters", "--clusters", $ClusterName, "--region", $Reg, "--output", "json")
        if (-not $r -or -not $r.clusters -or $r.clusters.Count -eq 0) { Write-Ok "ECS cluster $ClusterName deleted"; return }
        $c = $r.clusters[0]
        if ($c.status -eq "INACTIVE") { Write-Ok "ECS cluster $ClusterName INACTIVE"; return }
        Write-Host "  Waiting for ECS cluster $ClusterName..." -ForegroundColor Gray
        Start-Sleep -Seconds 5
        $elapsed += 5
    }
    throw "Timeout waiting for ECS cluster $ClusterName (${TimeoutSec}s)"
}

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
    throw "Timeout waiting for IAM role $RoleName (${TimeoutSec}s)"
}

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

function Wait-InstanceTerminated {
    param([string]$InstanceId, [string]$Reg, [int]$TimeoutSec = 300)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $r = Invoke-AwsJson @("ec2", "describe-instances", "--instance-ids", $InstanceId, "--region", $Reg, "--output", "json")
        if (-not $r -or -not $r.Reservations -or $r.Reservations.Count -eq 0) {
            Write-Ok "Instance $InstanceId no longer found (terminated)"
            return
        }
        $state = $r.Reservations[0].Instances[0].State.Name
        if ($state -eq "terminated") {
            Write-Ok "Instance $InstanceId terminated"
            return
        }
        Write-Host "  Waiting for instance $InstanceId to terminate (state=$state)..." -ForegroundColor Gray
        Start-Sleep -Seconds 10
        $elapsed += 10
    }
    throw "Timeout waiting for instance $InstanceId to terminate (${TimeoutSec}s)"
}

function Wait-InstanceRunning {
    param([string]$InstanceId, [string]$Reg, [int]$TimeoutSec = 300)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $r = Invoke-AwsJson @("ec2", "describe-instances", "--instance-ids", $InstanceId, "--region", $Reg, "--output", "json")
        if (-not $r -or -not $r.Reservations -or $r.Reservations.Count -eq 0) { Start-Sleep -Seconds 10; $elapsed += 10; continue }
        $state = $r.Reservations[0].Instances[0].State.Name
        if ($state -eq "running") {
            Write-Ok "Instance $InstanceId running"
            return
        }
        Write-Host "  Waiting for instance $InstanceId to run (state=$state)..." -ForegroundColor Gray
        Start-Sleep -Seconds 10
        $elapsed += 10
    }
    throw "Timeout waiting for instance $InstanceId to run (${TimeoutSec}s)"
}

function Wait-SSMOnline {
    param([string]$InstanceId, [string]$Reg, [int]$TimeoutSec = 300)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $r = Invoke-AwsJson @("ssm", "describe-instance-information", "--filters", "Key=InstanceIds,Values=$InstanceId", "--region", $Reg, "--output", "json")
        if ($r -and $r.InstanceInformationList -and $r.InstanceInformationList.Count -gt 0) {
            Write-Ok "SSM online for instance $InstanceId"
            return
        }
        Write-Host "  Waiting for SSM agent on $InstanceId..." -ForegroundColor Gray
        Start-Sleep -Seconds 10
        $elapsed += 10
    }
    throw "Timeout waiting for SSM on instance $InstanceId (${TimeoutSec}s)"
}

function Wait-ApiHealth200 {
    param([string]$ApiBaseUrl, [int]$TimeoutSec = 300)
    $elapsed = 0
    $uri = "$ApiBaseUrl/health"
    while ($elapsed -lt $TimeoutSec) {
        try {
            $r = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 15
            if ($r.StatusCode -eq 200) {
                Write-Ok "GET $uri -> 200"
                return
            }
        } catch {}
        Write-Host "  Waiting for API health 200..." -ForegroundColor Gray
        Start-Sleep -Seconds 10
        $elapsed += 10
    }
    throw "Timeout waiting for API health 200 at $uri (${TimeoutSec}s)"
}
