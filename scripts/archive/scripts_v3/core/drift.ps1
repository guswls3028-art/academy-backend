# Drift: structure-based comparison. CE/Queue/JobDef/ASG Expected vs Actual.
$ErrorActionPreference = "Stop"
$R = $script:Region

function Get-StructuralDrift {
    Set-SSOTCanonicalLists | Out-Null
    $rows = [System.Collections.ArrayList]::new()

    # CE: instanceTypes, maxvCpus, subnets, securityGroupIds
    foreach ($ceName in $script:SSOT_CE) {
        $r = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $ceName, "--region", $R, "--output", "json")
        $exp = $script:SSOT_CE_Expected[$ceName]
        if (-not $exp) { continue }
        if (-not $r -or -not $r.computeEnvironments -or $r.computeEnvironments.Count -eq 0) {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "Batch CE"; Name = $ceName; Expected = "exists"; Actual = "missing"; Action = "Create" })
            continue
        }
        $ce = $r.computeEnvironments[0]
        $cr = $ce.computeResources
        $actTypes = if ($cr.instanceTypes) { [string[]]$cr.instanceTypes } else { @() }
        $expTypes = $exp.instanceTypes
        $typeMatch = (($actTypes | Sort-Object) -join ",") -eq (($expTypes | Sort-Object) -join ",")
        $maxMatch = $cr.maxvCpus -eq $exp.maxvCpus
        $subnetMatch = $true
        if ($cr.subnets -and $exp.subnets) {
            $sAct = ($cr.subnets | Sort-Object) -join ","
            $sExp = ($exp.subnets | Sort-Object) -join ","
            $subnetMatch = $sAct -eq $sExp
        }
        $sgMatch = $true
        if ($cr.securityGroupIds -and $exp.securityGroupIds) {
            $gAct = ($cr.securityGroupIds | Sort-Object) -join ","
            $gExp = ($exp.securityGroupIds | Sort-Object) -join ","
            $sgMatch = $gAct -eq $gExp
        }
        if (-not ($typeMatch -and $maxMatch -and $subnetMatch -and $sgMatch)) {
            $expStr = "instanceTypes=$($expTypes -join ','), maxvCpus=$($exp.maxvCpus)"
            $actStr = "instanceTypes=$($actTypes -join ','), maxvCpus=$($cr.maxvCpus)"
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "Batch CE"; Name = $ceName; Expected = $expStr; Actual = $actStr; Action = "Recreate" })
        }
    }

    # Queue: computeEnvironmentOrder (CE ARN), priority
    foreach ($qName in $script:SSOT_Queue) {
        $r = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $qName, "--region", $R, "--output", "json")
        if (-not $r -or -not $r.jobQueues -or $r.jobQueues.Count -eq 0) {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "Batch Queue"; Name = $qName; Expected = "exists"; Actual = "missing"; Action = "Create" })
            continue
        }
        $q = $r.jobQueues[0]
        $priOk = $q.priority -eq $script:SSOT_Queue_Priority
        $orderOk = $q.computeEnvironmentOrder -and $q.computeEnvironmentOrder.Count -ge 1
        if (-not $priOk -or -not $orderOk) {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "Batch Queue"; Name = $qName; Expected = "priority=1, order"; Actual = "priority=$($q.priority)"; Action = "Recreate" })
        }
    }

    # JobDef: vcpus, memory (latest revision only)
    foreach ($jdName in $script:SSOT_JobDef) {
        $r = Invoke-AwsJson @("batch", "describe-job-definitions", "--job-definition-name", $jdName, "--status", "ACTIVE", "--region", $R, "--output", "json")
        $exp = $script:SSOT_JobDef_Expected[$jdName]
        if (-not $exp) { continue }
        if (-not $r -or -not $r.jobDefinitions -or $r.jobDefinitions.Count -eq 0) {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "Batch JobDef"; Name = $jdName; Expected = "exists"; Actual = "missing"; Action = "Create" })
            continue
        }
        $latest = $r.jobDefinitions | Sort-Object -Property revision -Descending | Select-Object -First 1
        $cp = $latest.containerProperties
        $vcOk = $cp.vcpus -eq $exp.vcpus
        $memOk = $cp.memory -eq $exp.memory
        if (-not $vcOk -or -not $memOk) {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "Batch JobDef"; Name = $jdName; Expected = "vcpus=$($exp.vcpus) memory=$($exp.memory)"; Actual = "vcpus=$($cp.vcpus) memory=$($cp.memory)"; Action = "Recreate" })
        }
    }

    # ASG: MinSize, MaxSize, LaunchTemplate present
    foreach ($asgName in $script:SSOT_ASG) {
        $r = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $asgName, "--region", $R, "--output", "json")
        if (-not $r -or -not $r.AutoScalingGroups -or $r.AutoScalingGroups.Count -eq 0) {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "ASG"; Name = $asgName; Expected = "exists"; Actual = "missing"; Action = "Create" })
            continue
        }
        $a = $r.AutoScalingGroups[0]
        if (-not $a.LaunchTemplate) {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "ASG"; Name = $asgName; Expected = "LaunchTemplate"; Actual = "none"; Action = "Update" })
        }
    }

    return $rows
}

function Show-StructuralDriftTable {
    param([System.Collections.ArrayList]$Rows)
    Write-Host "`n=== DRIFT (structure comparison) ===" -ForegroundColor Cyan
    Write-Host "| ResourceType | Name | Expected | Actual | Action |"
    Write-Host "|--------------|------|----------|--------|--------|"
    if ($Rows -and $Rows.Count -gt 0) {
        foreach ($row in $Rows) {
            Write-Host "| $($row.ResourceType) | $($row.Name) | $($row.Expected) | $($row.Actual) | $($row.Action) |"
        }
    } else {
        Write-Host "| (none) | - | - | - | NoOp |" -ForegroundColor Green
    }
    Write-Host "=== END DRIFT ===`n"
    return $Rows.Count
}
