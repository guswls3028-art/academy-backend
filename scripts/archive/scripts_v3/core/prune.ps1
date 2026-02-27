# Prune: list/delete resources outside SSOT Canonical. Order: EventBridge -> Queue -> CE -> JobDef -> ASG -> ECS -> IAM.
# After each delete use describe polling to confirm gone (no fixed sleep).
$ErrorActionPreference = "Stop"
$R = $script:Region

function Get-AllAwsResources {
    $all = [ordered]@{}
    # Batch
    $all["Batch CE"] = @()
    $r = Invoke-AwsJson @("batch", "describe-compute-environments", "--region", $R, "--output", "json")
    if ($r -and $r.computeEnvironments) { $all["Batch CE"] = $r.computeEnvironments | ForEach-Object { $_.computeEnvironmentName } }
    $all["Batch Queue"] = @()
    $r = Invoke-AwsJson @("batch", "describe-job-queues", "--region", $R, "--output", "json")
    if ($r -and $r.jobQueues) { $all["Batch Queue"] = $r.jobQueues | ForEach-Object { $_.jobQueueName } }
    $all["Batch JobDef"] = @()
    $r = Invoke-AwsJson @("batch", "describe-job-definitions", "--status", "ACTIVE", "--region", $R, "--output", "json")
    if ($r -and $r.jobDefinitions) {
        $all["Batch JobDef"] = $r.jobDefinitions | ForEach-Object { "$($_.jobDefinitionName):$($_.revision)" }
        $all["Batch JobDefArn"] = $r.jobDefinitions
    }
    # EventBridge
    $all["EventBridge Rule"] = @()
    $r = Invoke-AwsJson @("events", "list-rules", "--region", $R, "--output", "json")
    if ($r -and $r.Rules) { $all["EventBridge Rule"] = $r.Rules | ForEach-Object { $_.Name } }
    # IAM (academy-* only)
    $all["IAM Role"] = @()
    $r = Invoke-AwsJson @("iam", "list-roles", "--output", "json")
    if ($r -and $r.Roles) { $all["IAM Role"] = $r.Roles | Where-Object { $_.RoleName -like "academy-*" } | ForEach-Object { $_.RoleName } }
    # ASG (all in region)
    $all["ASG"] = @()
    $r = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $R, "--output", "json")
    if ($r -and $r.AutoScalingGroups) { $all["ASG"] = $r.AutoScalingGroups | ForEach-Object { $_.AutoScalingGroupName } }
    # ECS clusters
    $all["ECS Cluster"] = @()
    $r = Invoke-AwsJson @("ecs", "list-clusters", "--region", $R, "--output", "json")
    if ($r -and $r.clusterArns) {
        foreach ($arn in $r.clusterArns) {
            $name = $arn -replace ".*/", ""
            $all["ECS Cluster"] += $name
        }
    }
    # EIP
    $all["EIP"] = @()
    $r = Invoke-AwsJson @("ec2", "describe-addresses", "--region", $R, "--output", "json")
    if ($r -and $r.Addresses) { $all["EIP"] = $r.Addresses | ForEach-Object { $_.AllocationId } }
    # SSM (academy prefix)
    $all["SSM"] = @()
    try {
        $r = Invoke-AwsJson @("ssm", "get-parameters-by-path", "--path", "/academy", "--recursive", "--region", $R, "--output", "json")
        if ($r -and $r.Parameters) { $all["SSM"] = $r.Parameters | ForEach-Object { $_.Name } }
    } catch { }
    # ECR
    $all["ECR"] = @()
    $r = Invoke-AwsJson @("ecr", "describe-repositories", "--region", $R, "--output", "json")
    if ($r -and $r.repositories) { $all["ECR"] = $r.repositories | ForEach-Object { $_.repositoryName } }
    # RDS / Redis / EC2 API,Build — identify only, not delete targets (Canonical 1 each)
    return $all
}

function Get-DeleteCandidates {
    param([hashtable]$All)
    Set-SSOTCanonicalLists | Out-Null
    $cand = [ordered]@{}
    # Batch CE
    $cand["Batch CE"] = $All["Batch CE"] | Where-Object { $_ -notin $script:SSOT_CE }
    # Batch Queue
    $cand["Batch Queue"] = $All["Batch Queue"] | Where-Object { $_ -notin $script:SSOT_Queue }
    # Batch JobDef: name not in SSOT (deregister by name; we'll collect ARNs for non-canonical names)
    $cand["Batch JobDef"] = @()
    if ($All["Batch JobDefArn"]) {
        $namesToDel = $All["Batch JobDefArn"] | Group-Object -Property jobDefinitionName | Where-Object { $_.Name -notin $script:SSOT_JobDef } | ForEach-Object { $_.Name }
        $cand["Batch JobDef"] = $All["Batch JobDefArn"] | Where-Object { $_.jobDefinitionName -in $namesToDel } | ForEach-Object { $_.jobDefinitionArn }
    }
    # EventBridge
    $cand["EventBridge Rule"] = $All["EventBridge Rule"] | Where-Object { $_ -notin $script:SSOT_EventBridgeRule }
    # IAM
    $cand["IAM Role"] = $All["IAM Role"] | Where-Object { $_ -notin $script:SSOT_IAMRoles }
    # ASG: exclude Batch-managed ASG names
    $cand["ASG"] = $All["ASG"] | Where-Object {
        $_ -notin $script:SSOT_ASG -and
        $_ -notlike "*academy-video-batch-ce-final*" -and
        $_ -notlike "*academy-video-ops-ce*"
    }
    # ECS: clusters not matching canonical patterns
    $cand["ECS Cluster"] = $All["ECS Cluster"] | Where-Object {
        $name = $_
        ($script:SSOT_ECSClusterPatterns | Where-Object { $name -like $_ }).Count -eq 0
    }
    # EIP: not in SSOT, only unassociated for safe release
    $cand["EIP"] = @()
    $addrs = Invoke-AwsJson @("ec2", "describe-addresses", "--region", $R, "--output", "json")
    if ($addrs -and $addrs.Addresses) {
        $cand["EIP"] = $addrs.Addresses | Where-Object { $_.AllocationId -notin $script:SSOT_EIP -and -not $_.AssociationId } | ForEach-Object { $_.AllocationId }
    }
    return $cand
}

function Show-DeleteCandidateTable {
    param([hashtable]$Candidates)
    Write-Host "`n=== DELETE CANDIDATE (non-SSOT) ===" -ForegroundColor Red
    Write-Host "| ResourceType | Identifier |" -ForegroundColor Cyan
    Write-Host "|--------------|------------|"
    $total = 0
    foreach ($key in $Candidates.Keys) {
        $arr = $Candidates[$key]
        if ($arr -and $arr.Count -gt 0) {
            foreach ($id in $arr) {
                Write-Host "| $key | $id |"
                $total++
            }
        }
    }
    if ($total -eq 0) { Write-Host "| (none) | Matches SSOT |" -ForegroundColor Green }
    Write-Host "=== END DELETE CANDIDATE ===`n" -ForegroundColor Red
    return $total
}

# Run deletes: follow dependency order. After each delete use describe polling Wait.
function Invoke-PruneLegacyDeletes {
    param([hashtable]$Candidates)
    $R = $script:Region
    # 1) EventBridge: remove targets then delete rule → Wait rule deleted
    foreach ($ruleName in $Candidates["EventBridge Rule"]) {
        Write-Host "  [Prune] EventBridge rule: $ruleName" -ForegroundColor Yellow
        try {
            $targets = Invoke-AwsJson @("events", "list-targets-by-rule", "--rule", $ruleName, "--region", $R, "--output", "json")
            if ($targets -and $targets.Targets -and $targets.Targets.Count -gt 0) {
                $ids = $targets.Targets | ForEach-Object { $_.Id }
                $args = @("events", "remove-targets", "--rule", $ruleName, "--ids") + [string[]]$ids + @("--region", $R)
                Invoke-Aws $args -ErrorMessage "remove-targets $ruleName" 2>$null | Out-Null
            }
            Invoke-Aws @("events", "delete-rule", "--name", $ruleName, "--region", $R) -ErrorMessage "delete-rule $ruleName" | Out-Null
            Wait-EventBridgeRuleDeleted -RuleName $ruleName -Reg $R -TimeoutSec 120
        } catch { Write-Warn "    $_" }
    }
    # 2) Queue: DISABLED then delete → Wait queue deleted
    foreach ($qName in $Candidates["Batch Queue"]) {
        Write-Host "  [Prune] Batch queue: $qName" -ForegroundColor Yellow
        try {
            Invoke-Aws @("batch", "update-job-queue", "--job-queue", $qName, "--state", "DISABLED", "--region", $R) -ErrorMessage "disable queue $qName" 2>$null | Out-Null
            $wait = 0
            while ($wait -lt 90) {
                $d = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $qName, "--region", $R, "--output", "json")
                if (-not $d -or -not $d.jobQueues -or $d.jobQueues[0].state -eq "DISABLED") { break }
                Start-Sleep -Seconds 10
                $wait += 10
            }
            Invoke-Aws @("batch", "delete-job-queue", "--job-queue", $qName, "--region", $R) -ErrorMessage "delete-job-queue $qName" | Out-Null
            Wait-QueueDeleted -QueueName $qName -Reg $R -TimeoutSec 180
        } catch { Write-Warn "    $_" }
    }
    # 3) CE: DISABLED then delete → Wait CE deleted
    foreach ($ceName in $Candidates["Batch CE"]) {
        Write-Host "  [Prune] Batch CE: $ceName" -ForegroundColor Yellow
        try {
            Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $ceName, "--state", "DISABLED", "--region", $R) -ErrorMessage "disable CE $ceName" 2>$null | Out-Null
            $wait = 0
            while ($wait -lt 120) {
                $d = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $ceName, "--region", $R, "--output", "json")
                if (-not $d -or -not $d.computeEnvironments -or $d.computeEnvironments[0].state -eq "DISABLED") { break }
                Start-Sleep -Seconds 10
                $wait += 10
            }
            Invoke-Aws @("batch", "delete-compute-environment", "--compute-environment", $ceName, "--region", $R) -ErrorMessage "delete CE $ceName" | Out-Null
            Wait-CEDeleted -CEName $ceName -Reg $R -TimeoutSec 300
        } catch { Write-Warn "    $_" }
    }
    # 4) JobDef: deregister (no wait needed)
    foreach ($arn in $Candidates["Batch JobDef"]) {
        Write-Host "  [Prune] Batch JobDef: $arn" -ForegroundColor Yellow
        try {
            Invoke-Aws @("batch", "deregister-job-definition", "--job-definition", $arn, "--region", $R) -ErrorMessage "deregister $arn" | Out-Null
        } catch { Write-Warn "    $_" }
    }
    # 5) ASG: set 0 then delete → Wait ASG deleted
    foreach ($asgName in $Candidates["ASG"]) {
        Write-Host "  [Prune] ASG: $asgName" -ForegroundColor Yellow
        try {
            Invoke-Aws @("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", $asgName, "--min-size", "0", "--max-size", "0", "--desired-capacity", "0", "--region", $R) -ErrorMessage "update ASG $asgName" 2>$null | Out-Null
            $wait = 0
            while ($wait -lt 120) {
                $d = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $asgName, "--region", $R, "--output", "json")
                if (-not $d -or -not $d.AutoScalingGroups -or $d.AutoScalingGroups[0].DesiredCapacity -eq 0) { break }
                Start-Sleep -Seconds 10
                $wait += 10
            }
            Invoke-Aws @("autoscaling", "delete-auto-scaling-group", "--auto-scaling-group-name", $asgName, "--force-delete", "--region", $R) -ErrorMessage "delete ASG $asgName" | Out-Null
            Wait-ASGDeleted -ASGName $asgName -Reg $R -TimeoutSec 300
        } catch { Write-Warn "    $_" }
    }
    # 6) ECS cluster → Wait cluster deleted
    foreach ($clusterName in $Candidates["ECS Cluster"]) {
        Write-Host "  [Prune] ECS cluster: $clusterName" -ForegroundColor Yellow
        try {
            Invoke-Aws @("ecs", "delete-cluster", "--cluster", $clusterName, "--region", $R) -ErrorMessage "delete-cluster $clusterName" 2>$null | Out-Null
            Wait-ECSClusterDeleted -ClusterName $clusterName -Reg $R -TimeoutSec 120
        } catch { Write-Warn "    $_" }
    }
    # 7) IAM: detach + delete inline + delete role → Wait role deleted
    foreach ($roleName in $Candidates["IAM Role"]) {
        Write-Host "  [Prune] IAM role: $roleName" -ForegroundColor Yellow
        try {
            $attached = Invoke-AwsJson @("iam", "list-attached-role-policies", "--role-name", $roleName, "--output", "json")
            if ($attached -and $attached.AttachedPolicies) {
                foreach ($p in $attached.AttachedPolicies) {
                    Invoke-Aws @("iam", "detach-role-policy", "--role-name", $roleName, "--policy-arn", $p.PolicyArn) -ErrorMessage "detach $($p.PolicyArn)" 2>$null | Out-Null
                }
            }
            $inline = Invoke-AwsJson @("iam", "list-role-policies", "--role-name", $roleName, "--output", "json")
            if ($inline -and $inline.PolicyNames) {
                foreach ($pn in $inline.PolicyNames) {
                    Invoke-Aws @("iam", "delete-role-policy", "--role-name", $roleName, "--policy-name", $pn) -ErrorMessage "delete inline $pn" 2>$null | Out-Null
                }
            }
            $profiles = Invoke-AwsJson @("iam", "list-instance-profiles-for-role", "--role-name", $roleName, "--output", "json")
            if ($profiles -and $profiles.InstanceProfiles) {
                foreach ($ip in $profiles.InstanceProfiles) {
                    Invoke-Aws @("iam", "remove-role-from-instance-profile", "--instance-profile-name", $ip.InstanceProfileName, "--role-name", $roleName) -ErrorMessage "remove role from profile" 2>$null | Out-Null
                }
            }
            Invoke-Aws @("iam", "delete-role", "--role-name", $roleName) -ErrorMessage "delete-role $roleName" | Out-Null
            Wait-IAMRoleDeleted -RoleName $roleName -TimeoutSec 60
        } catch { Write-Warn "    $_" }
    }
    # 8) EIP: release (unassociated only)
    foreach ($allocId in $Candidates["EIP"]) {
        Write-Host "  [Prune] EIP: $allocId" -ForegroundColor Yellow
        try {
            Invoke-Aws @("ec2", "release-address", "--allocation-id", $allocId, "--region", $R) -ErrorMessage "release-address $allocId" | Out-Null
        } catch { Write-Warn "    $_" }
    }
}
