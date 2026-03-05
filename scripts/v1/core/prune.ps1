# Prune: delete academy-* resources not in SSOT canonical. Order + Wait per state-contract.
$ErrorActionPreference = "Stop"
$R = $script:Region

function Get-AllAwsResourcesForPrune {
    $all = [ordered]@{}
    $r = Invoke-AwsJson @("batch", "describe-compute-environments", "--region", $R, "--output", "json")
    $all["Batch CE"] = if ($r -and $r.computeEnvironments) { $r.computeEnvironments | ForEach-Object { $_.computeEnvironmentName } } else { @() }
    $r = Invoke-AwsJson @("batch", "describe-job-queues", "--region", $R, "--output", "json")
    $all["Batch Queue"] = if ($r -and $r.jobQueues) { $r.jobQueues | ForEach-Object { $_.jobQueueName } } else { @() }
    $r = Invoke-AwsJson @("batch", "describe-job-definitions", "--status", "ACTIVE", "--region", $R, "--output", "json")
    $all["Batch JobDef"] = @()
    $all["Batch JobDefArn"] = @()
    if ($r -and $r.jobDefinitions) {
        $all["Batch JobDef"] = $r.jobDefinitions | ForEach-Object { "$($_.jobDefinitionName):$($_.revision)" }
        $all["Batch JobDefArn"] = $r.jobDefinitions
    }
    $r = Invoke-AwsJson @("events", "list-rules", "--region", $R, "--output", "json")
    $all["EventBridge Rule"] = if ($r -and $r.Rules) { $r.Rules | ForEach-Object { $_.Name } } else { @() }
    $r = Invoke-AwsJson @("iam", "list-roles", "--output", "json")
    $all["IAM Role"] = if ($r -and $r.Roles) { $r.Roles | Where-Object { $_.RoleName -like "academy-*" } | ForEach-Object { $_.RoleName } } else { @() }
    $r = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $R, "--output", "json")
    $all["ASG"] = if ($r -and $r.AutoScalingGroups) { $r.AutoScalingGroups | ForEach-Object { $_.AutoScalingGroupName } } else { @() }
    $r = Invoke-AwsJson @("ecs", "list-clusters", "--region", $R, "--output", "json")
    $all["ECS Cluster"] = if ($r -and $r.clusterArns) { $r.clusterArns | ForEach-Object { $_ -replace ".*/", "" } } else { @() }
    $r = Invoke-AwsJson @("ec2", "describe-addresses", "--region", $R, "--output", "json")
    $all["EIP"] = if ($r -and $r.Addresses) { $r.Addresses | Where-Object { $_.AllocationId -notin $script:SSOT_EIP -and -not $_.AssociationId } | ForEach-Object { $_.AllocationId } } else { @() }
    try {
        $r = Invoke-AwsJson @("ssm", "get-parameters-by-path", "--path", "/academy", "--recursive", "--region", $R, "--output", "json")
        $all["SSM"] = if ($r -and $r.Parameters) { $r.Parameters | ForEach-Object { $_.Name } } else { @() }
    }
    catch { $all["SSM"] = @() }
    $r = Invoke-AwsJson @("ecr", "describe-repositories", "--region", $R, "--output", "json")
    $all["ECR"] = if ($r -and $r.repositories) { $r.repositories | ForEach-Object { $_.repositoryName } } else { @() }
    return $all
}

function Get-DeleteCandidates {
    param([hashtable]$All)
    $cand = [ordered]@{}
    $cand["Batch CE"] = $All["Batch CE"] | Where-Object { $_ -notin $script:SSOT_CE }
    $cand["Batch Queue"] = $All["Batch Queue"] | Where-Object { $_ -notin $script:SSOT_Queue }
    $cand["Batch JobDef"] = @()
    if ($All["Batch JobDefArn"]) {
        $namesToDel = $All["Batch JobDefArn"] | Group-Object -Property jobDefinitionName | Where-Object { $_.Name -notin $script:SSOT_JobDef } | ForEach-Object { $_.Name }
        $cand["Batch JobDef"] = $All["Batch JobDefArn"] | Where-Object { $_.jobDefinitionName -in $namesToDel } | ForEach-Object { $_.jobDefinitionArn }
    }
    $cand["EventBridge Rule"] = $All["EventBridge Rule"] | Where-Object { $_ -notin $script:SSOT_EventBridgeRule }
    $cand["IAM Role"] = $All["IAM Role"] | Where-Object { 
        $_ -notin $script:SSOT_IAMRoles -and 
        $_ -notin @("academy-ec2-role", "academy-v1-eventbridge-batch-video-role")
    }
    $cand["ASG"] = $All["ASG"] | Where-Object {
        $_ -notin $script:SSOT_ASG -and $_ -notlike "*academy-video-batch-ce-final*" -and $_ -notlike "*academy-video-ops-ce*"
    }
    $cand["ECS Cluster"] = $All["ECS Cluster"] | Where-Object {
        $name = $_
        ($script:SSOT_ECSClusterPatterns | Where-Object { $name -like $_ }).Count -eq 0
    }
    $cand["EIP"] = $All["EIP"]
    $cand["SSM"] = $All["SSM"] | Where-Object { $_ -notin $script:SSOT_SSM }
    $cand["ECR"] = $All["ECR"] | Where-Object { $_ -notin $script:SSOT_ECR }
    return $cand
}

function Show-DeleteCandidateTable {
    param([hashtable]$Candidates)
    Write-Host "`n=== DELETE CANDIDATE (non-SSOT) ===" -ForegroundColor Red
    Write-Host "| ResourceType | Identifier |"
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
    Write-Host "=== END DELETE CANDIDATE ===`n"
    return $total
}

function Invoke-PruneLegacyDeletes {
    param([hashtable]$Candidates)
    $R = $script:Region
    # 1) EventBridge: remove targets then delete rule
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
        }
        catch { Write-Warn "    $_" }
    }
    # 2) Batch Queue
    foreach ($qName in $Candidates["Batch Queue"]) {
        Write-Host "  [Prune] Batch queue: $qName" -ForegroundColor Yellow
        try {
            Invoke-Aws @("batch", "update-job-queue", "--job-queue", $qName, "--state", "DISABLED", "--region", $R) -ErrorMessage "disable queue $qName" 2>$null | Out-Null
            $wait = 0; while ($wait -lt 90) {
                $d = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $qName, "--region", $R, "--output", "json")
                if (-not $d -or -not $d.jobQueues -or $d.jobQueues[0].state -eq "DISABLED") { break }
                Start-Sleep -Seconds 10; $wait += 10
            }
            Invoke-Aws @("batch", "delete-job-queue", "--job-queue", $qName, "--region", $R) -ErrorMessage "delete-job-queue $qName" | Out-Null
            Wait-QueueDeleted -QueueName $qName -Reg $R -TimeoutSec 180
        }
        catch { Write-Warn "    $_" }
    }
    # 3) Batch CE
    foreach ($ceName in $Candidates["Batch CE"]) {
        Write-Host "  [Prune] Batch CE: $ceName" -ForegroundColor Yellow
        try {
            Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $ceName, "--state", "DISABLED", "--region", $R) -ErrorMessage "disable CE $ceName" 2>$null | Out-Null
            $wait = 0; while ($wait -lt 120) {
                $d = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $ceName, "--region", $R, "--output", "json")
                if (-not $d -or -not $d.computeEnvironments -or $d.computeEnvironments[0].state -eq "DISABLED") { break }
                Start-Sleep -Seconds 10; $wait += 10
            }
            Invoke-Aws @("batch", "delete-compute-environment", "--compute-environment", $ceName, "--region", $R) -ErrorMessage "delete CE $ceName" | Out-Null
            Wait-CEDeleted -CEName $ceName -Reg $R -TimeoutSec 300
        }
        catch { Write-Warn "    $_" }
    }
    # 4) JobDef deregister
    foreach ($arn in $Candidates["Batch JobDef"]) {
        Write-Host "  [Prune] Batch JobDef: $arn" -ForegroundColor Yellow
        try { Invoke-Aws @("batch", "deregister-job-definition", "--job-definition", $arn, "--region", $R) -ErrorMessage "deregister $arn" | Out-Null } catch { Write-Warn "    $_" }
    }
    # 5) ASG
    foreach ($asgName in $Candidates["ASG"]) {
        Write-Host "  [Prune] ASG: $asgName" -ForegroundColor Yellow
        try {
            Invoke-Aws @("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", $asgName, "--min-size", "0", "--max-size", "0", "--desired-capacity", "0", "--region", $R) -ErrorMessage "update ASG $asgName" 2>$null | Out-Null
            $wait = 0; while ($wait -lt 120) {
                $d = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $asgName, "--region", $R, "--output", "json")
                if (-not $d -or -not $d.AutoScalingGroups -or $d.AutoScalingGroups[0].DesiredCapacity -eq 0) { break }
                Start-Sleep -Seconds 10; $wait += 10
            }
            Invoke-Aws @("autoscaling", "delete-auto-scaling-group", "--auto-scaling-group-name", $asgName, "--force-delete", "--region", $R) -ErrorMessage "delete ASG $asgName" | Out-Null
            Wait-ASGDeleted -ASGName $asgName -Reg $R -TimeoutSec 300
        }
        catch { Write-Warn "    $_" }
    }
    # 6) ECS cluster
    foreach ($clusterName in $Candidates["ECS Cluster"]) {
        Write-Host "  [Prune] ECS cluster: $clusterName" -ForegroundColor Yellow
        try {
            Invoke-Aws @("ecs", "delete-cluster", "--cluster", $clusterName, "--region", $R) -ErrorMessage "delete-cluster $clusterName" 2>$null | Out-Null
            Wait-ECSClusterDeleted -ClusterName $clusterName -Reg $R -TimeoutSec 120
        }
        catch { Write-Warn "    $_" }
    }
    # 7) IAM
    foreach ($roleName in $Candidates["IAM Role"]) {
        Write-Host "  [Prune] IAM role: $roleName" -ForegroundColor Yellow
        try {
            $attached = Invoke-AwsJson @("iam", "list-attached-role-policies", "--role-name", $roleName, "--output", "json")
            if ($attached -and $attached.AttachedPolicies) {
                foreach ($p in $attached.AttachedPolicies) {
                    Invoke-Aws @("iam", "detach-role-policy", "--role-name", $roleName, "--policy-arn", $p.PolicyArn) -ErrorMessage "detach" 2>$null | Out-Null
                }
            }
            $inline = Invoke-AwsJson @("iam", "list-role-policies", "--role-name", $roleName, "--output", "json")
            if ($inline -and $inline.PolicyNames) {
                foreach ($pn in $inline.PolicyNames) {
                    Invoke-Aws @("iam", "delete-role-policy", "--role-name", $roleName, "--policy-name", $pn) -ErrorMessage "delete inline" 2>$null | Out-Null
                }
            }
            $profiles = Invoke-AwsJson @("iam", "list-instance-profiles-for-role", "--role-name", $roleName, "--output", "json")
            if ($profiles -and $profiles.InstanceProfiles) {
                foreach ($ip in $profiles.InstanceProfiles) {
                    Invoke-Aws @("iam", "remove-role-from-instance-profile", "--instance-profile-name", $ip.InstanceProfileName, "--role-name", $roleName) 2>$null | Out-Null
                }
            }
            Invoke-Aws @("iam", "delete-role", "--role-name", $roleName) -ErrorMessage "delete-role $roleName" | Out-Null
            Wait-IAMRoleDeleted -RoleName $roleName -TimeoutSec 60
        }
        catch { Write-Warn "    $_" }
    }
    # 8) ECR (SSOT external only - already in candidates)
    foreach ($repo in $Candidates["ECR"]) {
        Write-Host "  [Prune] ECR repo: $repo" -ForegroundColor Yellow
        try { Invoke-Aws @("ecr", "delete-repository", "--repository-name", $repo, "--force", "--region", $R) -ErrorMessage "delete-repository $repo" | Out-Null } catch { Write-Warn "    $_" }
    }
    # 9) SSM (SSOT external only)
    foreach ($paramName in $Candidates["SSM"]) {
        Write-Host "  [Prune] SSM: $paramName" -ForegroundColor Yellow
        try { Invoke-Aws @("ssm", "delete-parameter", "--name", $paramName, "--region", $R) -ErrorMessage "delete-parameter $paramName" | Out-Null } catch { Write-Warn "    $_" }
    }
    # 10) EIP unassociated only
    foreach ($allocId in $Candidates["EIP"]) {
        Write-Host "  [Prune] EIP: $allocId" -ForegroundColor Yellow
        try { Invoke-Aws @("ec2", "release-address", "--allocation-id", $allocId, "--region", $R) -ErrorMessage "release-address $allocId" | Out-Null } catch { Write-Warn "    $_" }
    }
}

# Purge (SSOT scope): disable+delete in order, then caller runs full Ensure.
function Get-PurgePlan {
    $R = $script:Region
    $plan = [ordered]@{}
    $plan["EventBridge Rules"] = @($script:SSOT_EventBridgeRule)
    $plan["Batch Queues"] = @($script:SSOT_Queue)
    $plan["Batch CEs"] = @($script:SSOT_CE)
    $plan["Batch JobDefs (deregister ACTIVE)"] = @($script:SSOT_JobDef)
    $plan["API ASG"] = @($script:ApiASGName)
    return $plan
}

function Invoke-PurgeAndRecreate {
    param([switch]$IncludePruneLegacy = $false)
    $R = $script:Region
    Write-Host "`n=== PURGE (SSOT scope) ===" -ForegroundColor Yellow
    # 1) EventBridge: disable + remove targets
    foreach ($ruleName in $script:SSOT_EventBridgeRule) {
        Write-Host "  [Purge] EventBridge: $ruleName" -ForegroundColor Yellow
        try {
            $targets = Invoke-AwsJson @("events", "list-targets-by-rule", "--rule", $ruleName, "--region", $R, "--output", "json")
            if ($targets -and $targets.Targets -and $targets.Targets.Count -gt 0) {
                $ids = $targets.Targets | ForEach-Object { $_.Id }
                $args = @("events", "remove-targets", "--rule", $ruleName, "--ids") + [string[]]$ids + @("--region", $R)
                Invoke-Aws $args -ErrorMessage "remove-targets $ruleName" 2>$null | Out-Null
            }
            Invoke-Aws @("events", "put-rule", "--name", $ruleName, "--state", "DISABLED", "--region", $R) -ErrorMessage "disable rule $ruleName" 2>$null | Out-Null
        }
        catch { Write-Warn "    $_" }
    }
    # 2) Queues disable + delete + wait
    foreach ($qName in $script:SSOT_Queue) {
        Write-Host "  [Purge] Queue: $qName" -ForegroundColor Yellow
        try {
            Invoke-Aws @("batch", "update-job-queue", "--job-queue", $qName, "--state", "DISABLED", "--region", $R) -ErrorMessage "disable queue $qName" 2>$null | Out-Null
            $wait = 0; while ($wait -lt 90) {
                $d = Invoke-AwsJson @("batch", "describe-job-queues", "--region", $R, "--output", "json")
                $arr = if ($d -and $d.jobQueues) { @($d.jobQueues) } else { @() }
                $q = $arr | Where-Object { $_.jobQueueName -eq $qName } | Select-Object -First 1
                if (-not $q -or $q.state -eq "DISABLED") { break }
                Start-Sleep -Seconds 10; $wait += 10
            }
            Invoke-Aws @("batch", "delete-job-queue", "--job-queue", $qName, "--region", $R) -ErrorMessage "delete-job-queue $qName" | Out-Null
            Wait-QueueDeleted -QueueName $qName -Reg $R -TimeoutSec 180
        }
        catch { Write-Warn "    $_" }
    }
    # 3) CEs disable + delete + wait
    foreach ($ceName in $script:SSOT_CE) {
        Write-Host "  [Purge] CE: $ceName" -ForegroundColor Yellow
        try {
            Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $ceName, "--state", "DISABLED", "--region", $R) -ErrorMessage "disable CE $ceName" 2>$null | Out-Null
            $wait = 0; while ($wait -lt 120) {
                $d = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $ceName, "--region", $R, "--output", "json")
                $arr = if ($d -and $d.computeEnvironments) { @($d.computeEnvironments) } else { @() }
                $c = $arr | Where-Object { $_.computeEnvironmentName -eq $ceName } | Select-Object -First 1
                if (-not $c -or $c.state -eq "DISABLED") { break }
                Start-Sleep -Seconds 10; $wait += 10
            }
            Invoke-Aws @("batch", "delete-compute-environment", "--compute-environment", $ceName, "--region", $R) -ErrorMessage "delete CE $ceName" | Out-Null
            Wait-CEDeleted -CEName $ceName -Reg $R -TimeoutSec 300
        }
        catch { Write-Warn "    $_" }
    }
    # 4) JobDef deregister ACTIVE for SSOT names
    foreach ($jdName in $script:SSOT_JobDef) {
        $list = Invoke-AwsJson @("batch", "describe-job-definitions", "--job-definition-name", $jdName, "--status", "ACTIVE", "--region", $R, "--output", "json")
        if ($list -and $list.jobDefinitions) {
            foreach ($jd in $list.jobDefinitions) {
                Write-Host "  [Purge] JobDef: $($jd.jobDefinitionArn)" -ForegroundColor Yellow
                try { Invoke-Aws @("batch", "deregister-job-definition", "--job-definition", $jd.jobDefinitionArn, "--region", $R) -ErrorMessage "deregister $jdName" | Out-Null } catch { Write-Warn "    $_" }
            }
        }
    }
    # 5) API ASG: scale to 0, delete
    Write-Host "  [Purge] API ASG: $($script:ApiASGName)" -ForegroundColor Yellow
    try {
        Invoke-Aws @("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", $script:ApiASGName, "--min-size", "0", "--max-size", "0", "--desired-capacity", "0", "--region", $R) -ErrorMessage "scale API ASG to 0" 2>$null | Out-Null
        $wait = 0
        while ($wait -lt 120) {
            $d = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $script:ApiASGName, "--region", $R, "--output", "json")
            $arr = if ($d -and $d.AutoScalingGroups) { @($d.AutoScalingGroups) } else { @() }
            $a = $arr | Where-Object { $_.AutoScalingGroupName -eq $script:ApiASGName } | Select-Object -First 1
            if (-not $a -or $a.DesiredCapacity -eq 0) { break }
            Start-Sleep -Seconds 10
            $wait += 10
        }
        Invoke-Aws @("autoscaling", "delete-auto-scaling-group", "--auto-scaling-group-name", $script:ApiASGName, "--force-delete", "--region", $R) -ErrorMessage "delete API ASG" | Out-Null
        Wait-ASGDeleted -ASGName $script:ApiASGName -Reg $R -TimeoutSec 300
    }
    catch { Write-Warn "    $_" }
    if ($IncludePruneLegacy) {
        $all = Get-AllAwsResourcesForPrune
        $candidates = Get-DeleteCandidates -All $all
        $count = Show-DeleteCandidateTable -Candidates $candidates
        if ($count -gt 0) { Invoke-PruneLegacyDeletes -Candidates $candidates }
    }
    Write-Host "=== PURGE done; running full Ensure ===`n" -ForegroundColor Green
}
