# Drift: structure comparison. SSOT expected vs actual. Used by -Plan and deploy.
# Existence = same Describe as Evidence; filter by resource name for correct detection.
$ErrorActionPreference = "Stop"

function Get-StructuralDrift {
    $R = $script:Region
    $rows = [System.Collections.ArrayList]::new()
    Write-Host "`n  [DRIFT-DEBUG] --- ExpectedName vs ActualNames from AWS ---" -ForegroundColor DarkGray

    # Batch CE: full list describe-compute-environments, then filter by computeEnvironmentName
    $ceResult = Invoke-AwsJson @("batch", "describe-compute-environments", "--region", $R, "--output", "json")
    $ceArr = if ($ceResult -and $ceResult.PSObject.Properties['computeEnvironments']) { @($ceResult.computeEnvironments) } else { @() }
    $allCeNames = @($ceArr | ForEach-Object { $_.computeEnvironmentName } | Where-Object { $_ })
    Write-Host "  [DRIFT-DEBUG] Batch CE ActualNames from AWS: ($($allCeNames -join ', '))" -ForegroundColor DarkGray
    foreach ($ceName in $script:SSOT_CE) {
        Write-Host "  [DRIFT-DEBUG] Batch CE ExpectedName: $ceName" -ForegroundColor DarkGray
        $matched = $ceArr | Where-Object { $_.computeEnvironmentName -eq $ceName }
        if (-not $matched -or @($matched).Count -eq 0) {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "Batch CE"; Name = $ceName; Expected = "exists"; Actual = "missing"; Action = "Create" })
            continue
        }
        $ce = @($matched)[0]
        $status = $ce.status
        if ($status -eq "INVALID") {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "Batch CE"; Name = $ceName; Expected = "VALID"; Actual = "INVALID"; Action = "Recreate" })
        } else {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "Batch CE"; Name = $ceName; Expected = "exists"; Actual = "exists"; Action = "NoOp" })
        }
    }

    # Batch Queue: full list describe-job-queues, then filter by jobQueueName
    $qResult = Invoke-AwsJson @("batch", "describe-job-queues", "--region", $R, "--output", "json")
    $qArr = if ($qResult -and $qResult.PSObject.Properties['jobQueues']) { @($qResult.jobQueues) } else { @() }
    $allQueueNames = @($qArr | ForEach-Object { $_.jobQueueName } | Where-Object { $_ })
    Write-Host "  [DRIFT-DEBUG] Batch Queue ActualNames from AWS: ($($allQueueNames -join ', '))" -ForegroundColor DarkGray
    foreach ($qName in $script:SSOT_Queue) {
        Write-Host "  [DRIFT-DEBUG] Batch Queue ExpectedName: $qName" -ForegroundColor DarkGray
        $matched = $qArr | Where-Object { $_.jobQueueName -eq $qName }
        if (-not $matched -or @($matched).Count -eq 0) {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "Batch Queue"; Name = $qName; Expected = "exists"; Actual = "missing"; Action = "Create" })
        } else {
            $q = @($matched)[0]
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "Batch Queue"; Name = $qName; Expected = "exists"; Actual = "exists"; Action = "NoOp" })
        }
    }

    # EventBridge: describe-rule; exception => missing, else exists
    foreach ($ruleName in $script:SSOT_EventBridgeRule) {
        Write-Host "  [DRIFT-DEBUG] EventBridge ExpectedName (describe-rule --name): $ruleName" -ForegroundColor DarkGray
        try {
            $rule = Invoke-AwsJson @("events", "describe-rule", "--name", $ruleName, "--region", $R, "--output", "json")
            $actualName = if ($rule -and $rule.PSObject.Properties['Name']) { $rule.Name } else { "(null or no Name)" }
            Write-Host "  [DRIFT-DEBUG] EventBridge ActualName from AWS: $actualName" -ForegroundColor DarkGray
            if (-not $rule) {
                [void]$rows.Add([PSCustomObject]@{ ResourceType = "EventBridge"; Name = $ruleName; Expected = "exists"; Actual = "missing"; Action = "Create" })
            } else {
                [void]$rows.Add([PSCustomObject]@{ ResourceType = "EventBridge"; Name = $ruleName; Expected = "exists"; Actual = "exists"; Action = "NoOp" })
            }
        } catch {
            Write-Host "  [DRIFT-DEBUG] EventBridge describe-rule threw: $($_.Exception.Message)" -ForegroundColor DarkGray
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "EventBridge"; Name = $ruleName; Expected = "exists"; Actual = "missing"; Action = "Create" })
        }
    }

    # ASG: full list describe-auto-scaling-groups (no name filter), then filter by AutoScalingGroupName
    $asgResult = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $R, "--output", "json")
    $asgArr = if ($asgResult -and $asgResult.PSObject.Properties['AutoScalingGroups']) { @($asgResult.AutoScalingGroups) } else { @() }
    $allAsgNames = @($asgArr | ForEach-Object { $_.AutoScalingGroupName } | Where-Object { $_ })
    Write-Host "  [DRIFT-DEBUG] ASG ActualNames from AWS: ($($allAsgNames -join ', '))" -ForegroundColor DarkGray
    $asgExpected = @{
        $script:ApiASGName = @{ Min = $script:ApiASGMinSize; Max = $script:ApiASGMaxSize; Desired = $script:ApiASGDesiredCapacity }
        $script:MessagingASGName = @{ Min = $script:MessagingMinSize; Max = $script:MessagingMaxSize; Desired = $script:MessagingDesiredCapacity }
        $script:AiASGName = @{ Min = $script:AiMinSize; Max = $script:AiMaxSize; Desired = $script:AiDesiredCapacity }
    }
    foreach ($asgName in $script:SSOT_ASG) {
        Write-Host "  [DRIFT-DEBUG] ASG ExpectedName: $asgName" -ForegroundColor DarkGray
        $matched = $asgArr | Where-Object { $_.AutoScalingGroupName -eq $asgName }
        if (-not $matched -or @($matched).Count -eq 0) {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "ASG"; Name = $asgName; Expected = "exists"; Actual = "missing"; Action = "Create" })
        } else {
            $a = @($matched)[0]
            $exp = $asgExpected[$asgName]
            $capDrift = ($a.MinSize -ne $exp.Min) -or ($a.MaxSize -ne $exp.Max) -or ($a.DesiredCapacity -ne $exp.Desired)
            if ($capDrift) {
                [void]$rows.Add([PSCustomObject]@{ ResourceType = "ASG"; Name = $asgName; Expected = "Min=$($exp.Min) Max=$($exp.Max) Desired=$($exp.Desired)"; Actual = "Min=$($a.MinSize) Max=$($a.MaxSize) Desired=$($a.DesiredCapacity)"; Action = "Update" })
            } else {
                [void]$rows.Add([PSCustomObject]@{ ResourceType = "ASG"; Name = $asgName; Expected = "exists"; Actual = "exists"; Action = "NoOp" })
            }
        }
    }
    return $rows
}

function Show-DriftTable {
    param([System.Collections.ArrayList]$Rows)
    Write-Host "`n=== DRIFT ===" -ForegroundColor Cyan
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
}
