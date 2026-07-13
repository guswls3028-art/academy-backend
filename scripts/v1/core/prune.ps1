# Prune: delete only explicitly retired resources owned by this stack. Order + Wait per state-contract.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"
$R = $script:Region

function Get-AllAwsResourcesForPrune {
    $all = [ordered]@{
        "Batch CE"         = @()
        "Batch Queue"      = @()
        "Batch JobDef"     = @()
        "Batch JobDefArn"  = @()
        "EventBridge Rule" = @()
        "IAM Role"         = @()
        "ASG"              = @()
        "ECS Cluster"      = @()
        "EIP"              = @()
        "SSM"              = @()
        "ECR"              = @()
    }

    # Every query is constrained by the closed-world allowlist. PruneLegacy must
    # never enumerate an AWS account and derive candidates by subtraction.
    $names = @($script:PruneLegacyAllowlist["Batch CE"] | Where-Object { $_ })
    if ($names.Count -gt 0) {
        $args = @("batch", "describe-compute-environments", "--compute-environments") + [string[]]$names + @("--region", $R, "--output", "json")
        $r = Invoke-AwsJson $args
        $all["Batch CE"] = if ($r -and $r.computeEnvironments) { @($r.computeEnvironments | ForEach-Object { $_.computeEnvironmentName }) } else { @() }
    }

    $names = @($script:PruneLegacyAllowlist["Batch Queue"] | Where-Object { $_ })
    if ($names.Count -gt 0) {
        $args = @("batch", "describe-job-queues", "--job-queues") + [string[]]$names + @("--region", $R, "--output", "json")
        $r = Invoke-AwsJson $args
        $all["Batch Queue"] = if ($r -and $r.jobQueues) { @($r.jobQueues | ForEach-Object { $_.jobQueueName }) } else { @() }
    }

    foreach ($jobDefName in @($script:PruneLegacyAllowlist["Batch JobDef"] | Where-Object { $_ })) {
        $r = Invoke-AwsJson @("batch", "describe-job-definitions", "--job-definition-name", $jobDefName, "--status", "ACTIVE", "--region", $R, "--output", "json")
        if ($r -and $r.jobDefinitions) {
            $all["Batch JobDef"] += @($r.jobDefinitions | ForEach-Object { "$($_.jobDefinitionName):$($_.revision)" })
            $all["Batch JobDefArn"] += @($r.jobDefinitions)
        }
    }

    foreach ($ruleName in @($script:PruneLegacyAllowlist["EventBridge Rule"] | Where-Object { $_ })) {
        $r = Invoke-AwsJson @("events", "describe-rule", "--name", $ruleName, "--region", $R, "--output", "json")
        if ($r -and $r.Name -eq $ruleName) {
            $all["EventBridge Rule"] += $r.Name
        }
    }
    return $all
}

function Get-PruneCandidateName {
    param([string]$ResourceType, [object]$Identifier)
    $name = [string]$Identifier
    if ($ResourceType -eq "Batch JobDef") {
        if ($name -match 'job-definition/([^:]+):\d+$') { return $matches[1] }
        if ($name -match '^([^:]+):\d+$') { return $matches[1] }
    }
    return $name
}

function Test-PruneCandidateProtected {
    param([string]$ResourceType, [string]$Name)
    switch ($ResourceType) {
        "Batch CE" { return $Name -in $script:SSOT_CE }
        "Batch Queue" { return $Name -in $script:SSOT_Queue }
        "Batch JobDef" { return $Name -in $script:SSOT_JobDef }
        "EventBridge Rule" { return $Name -in $script:SSOT_ProtectedEventBridgeRule }
        "IAM Role" { return $Name -in $script:SSOT_IAMRoles }
        "ASG" { return $Name -in $script:SSOT_ASG }
        "ECS Cluster" { return @($script:SSOT_ECSClusterPatterns | Where-Object { $Name -like $_ }).Count -gt 0 }
        "EIP" { return $Name -in $script:SSOT_EIP }
        "SSM" { return $Name -in $script:SSOT_SSM }
        "ECR" { return $Name -in $script:SSOT_ECR }
        default { return $false }
    }
}

function Assert-PruneCandidatesAllowlisted {
    param([hashtable]$Candidates)
    foreach ($resourceType in $Candidates.Keys) {
        foreach ($identifier in @($Candidates[$resourceType])) {
            $name = Get-PruneCandidateName -ResourceType $resourceType -Identifier $identifier
            $allowed = @($script:PruneLegacyAllowlist[$resourceType])
            if ($name -notin $allowed) {
                throw "PruneLegacy safety violation: '$resourceType' '$name' is not explicitly allowlisted."
            }
            if (Test-PruneCandidateProtected -ResourceType $resourceType -Name $name) {
                throw "PruneLegacy safety violation: '$resourceType' '$name' is protected by SSOT."
            }
        }
    }
}

function Get-DeleteCandidates {
    param([hashtable]$All)
    $cand = [ordered]@{}
    $cand["Batch CE"] = @($All["Batch CE"] | Where-Object { $_ -in $script:PruneLegacyAllowlist["Batch CE"] -and $_ -notin $script:SSOT_CE })
    $cand["Batch Queue"] = @($All["Batch Queue"] | Where-Object { $_ -in $script:PruneLegacyAllowlist["Batch Queue"] -and $_ -notin $script:SSOT_Queue })
    $cand["Batch JobDef"] = @()
    if ($All["Batch JobDefArn"]) {
        $namesToDel = $All["Batch JobDefArn"] | Group-Object -Property jobDefinitionName | Where-Object {
            $_.Name -in $script:PruneLegacyAllowlist["Batch JobDef"] -and $_.Name -notin $script:SSOT_JobDef
        } | ForEach-Object { $_.Name }
        $cand["Batch JobDef"] = $All["Batch JobDefArn"] | Where-Object { $_.jobDefinitionName -in $namesToDel } | ForEach-Object { $_.jobDefinitionArn }
    }
    $cand["EventBridge Rule"] = @($All["EventBridge Rule"] | Where-Object {
        $_ -in $script:PruneLegacyAllowlist["EventBridge Rule"] -and $_ -notin $script:SSOT_ProtectedEventBridgeRule
    })
    foreach ($resourceType in @("IAM Role", "ASG", "ECS Cluster", "EIP", "SSM", "ECR")) {
        $cand[$resourceType] = @($All[$resourceType] | Where-Object {
            $name = [string]$_
            $name -in $script:PruneLegacyAllowlist[$resourceType] -and -not (Test-PruneCandidateProtected -ResourceType $resourceType -Name $name)
        })
    }
    Assert-PruneCandidatesAllowlisted -Candidates $cand
    return $cand
}

function Show-DeleteCandidateTable {
    param([hashtable]$Candidates)
    Write-Host "`n=== DELETE CANDIDATE (explicit retired-resource allowlist) ===" -ForegroundColor Red
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
    Assert-PruneCandidatesAllowlisted -Candidates $Candidates
    $failures = [System.Collections.Generic.List[string]]::new()
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
        catch {
            $message = "EventBridge Rule '$ruleName': $($_.Exception.Message)"
            [void]$failures.Add($message)
            Write-Warn "    $message"
        }
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
        catch {
            $message = "Batch Queue '$qName': $($_.Exception.Message)"
            [void]$failures.Add($message)
            Write-Warn "    $message"
        }
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
        catch {
            $message = "Batch CE '$ceName': $($_.Exception.Message)"
            [void]$failures.Add($message)
            Write-Warn "    $message"
        }
    }
    # 4) JobDef deregister
    foreach ($arn in $Candidates["Batch JobDef"]) {
        Write-Host "  [Prune] Batch JobDef: $arn" -ForegroundColor Yellow
        try { Invoke-Aws @("batch", "deregister-job-definition", "--job-definition", $arn, "--region", $R) -ErrorMessage "deregister $arn" | Out-Null }
        catch {
            $message = "Batch JobDef '$arn': $($_.Exception.Message)"
            [void]$failures.Add($message)
            Write-Warn "    $message"
        }
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
        catch {
            $message = "ASG '$asgName': $($_.Exception.Message)"
            [void]$failures.Add($message)
            Write-Warn "    $message"
        }
    }
    # 6) ECS cluster
    foreach ($clusterName in $Candidates["ECS Cluster"]) {
        Write-Host "  [Prune] ECS cluster: $clusterName" -ForegroundColor Yellow
        try {
            Invoke-Aws @("ecs", "delete-cluster", "--cluster", $clusterName, "--region", $R) -ErrorMessage "delete-cluster $clusterName" 2>$null | Out-Null
            Wait-ECSClusterDeleted -ClusterName $clusterName -Reg $R -TimeoutSec 120
        }
        catch {
            $message = "ECS Cluster '$clusterName': $($_.Exception.Message)"
            [void]$failures.Add($message)
            Write-Warn "    $message"
        }
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
        catch {
            $message = "IAM Role '$roleName': $($_.Exception.Message)"
            [void]$failures.Add($message)
            Write-Warn "    $message"
        }
    }
    # 8) ECR (SSOT external only - already in candidates)
    foreach ($repo in $Candidates["ECR"]) {
        Write-Host "  [Prune] ECR repo: $repo" -ForegroundColor Yellow
        try { Invoke-Aws @("ecr", "delete-repository", "--repository-name", $repo, "--force", "--region", $R) -ErrorMessage "delete-repository $repo" | Out-Null }
        catch {
            $message = "ECR '$repo': $($_.Exception.Message)"
            [void]$failures.Add($message)
            Write-Warn "    $message"
        }
    }
    # 9) SSM (SSOT external only)
    foreach ($paramName in $Candidates["SSM"]) {
        Write-Host "  [Prune] SSM: $paramName" -ForegroundColor Yellow
        try { Invoke-Aws @("ssm", "delete-parameter", "--name", $paramName, "--region", $R) -ErrorMessage "delete-parameter $paramName" | Out-Null }
        catch {
            $message = "SSM '$paramName': $($_.Exception.Message)"
            [void]$failures.Add($message)
            Write-Warn "    $message"
        }
    }
    # 10) EIP unassociated only
    foreach ($allocId in $Candidates["EIP"]) {
        Write-Host "  [Prune] EIP: $allocId" -ForegroundColor Yellow
        try { Invoke-Aws @("ec2", "release-address", "--allocation-id", $allocId, "--region", $R) -ErrorMessage "release-address $allocId" | Out-Null }
        catch {
            $message = "EIP '$allocId': $($_.Exception.Message)"
            [void]$failures.Add($message)
            Write-Warn "    $message"
        }
    }
    if ($failures.Count -gt 0) {
        throw "PruneLegacy failed to delete $($failures.Count) resource(s): $($failures -join '; ')"
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
    # 1) EventBridge: disable + remove targets (put-rule requires ScheduleExpression)
    $scheduleMap = @{
        "academy-v1-reconcile-video-jobs" = if ($script:EventBridgeReconcileSchedule) { $script:EventBridgeReconcileSchedule } else { "rate(1 hour)" }
        "academy-v1-video-scan-stuck-rate" = if ($script:EventBridgeScanStuckSchedule) { $script:EventBridgeScanStuckSchedule } else { "rate(1 hour)" }
    }
    foreach ($ruleName in $script:SSOT_EventBridgeRule) {
        Write-Host "  [Purge] EventBridge: $ruleName" -ForegroundColor Yellow
        try {
            $targets = Invoke-AwsJson @("events", "list-targets-by-rule", "--rule", $ruleName, "--region", $R, "--output", "json")
            if ($targets -and $targets.Targets -and $targets.Targets.Count -gt 0) {
                $ids = $targets.Targets | ForEach-Object { $_.Id }
                $args = @("events", "remove-targets", "--rule", $ruleName, "--ids") + [string[]]$ids + @("--region", $R)
                Invoke-Aws $args -ErrorMessage "remove-targets $ruleName" 2>$null | Out-Null
            }
            $sched = if ($scheduleMap[$ruleName]) { $scheduleMap[$ruleName] } else { "rate(1 hour)" }
            Invoke-Aws @("events", "put-rule", "--name", $ruleName, "--schedule-expression", $sched, "--state", "DISABLED", "--region", $R) -ErrorMessage "disable rule $ruleName" 2>$null | Out-Null
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
