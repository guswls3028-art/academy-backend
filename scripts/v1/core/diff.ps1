# Drift: structure comparison. SSOT expected vs actual. Used by -Plan and deploy.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
# Existence = same Describe as Evidence; filter by resource name for correct detection.
$ErrorActionPreference = "Stop"

function Get-ExpectedApiImageUriForDrift {
    if (-not $script:EcrApiRepo -or -not $script:AccountId -or -not $script:Region) { return "" }
    if ($script:EcrUseLatestTag) {
        return "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$($script:EcrApiRepo):latest"
    }

    $list = Invoke-AwsJson @("ecr", "describe-images", "--repository-name", $script:EcrApiRepo, "--region", $script:Region, "--output", "json") 2>$null
    if (-not $list -or -not $list.imageDetails -or $list.imageDetails.Count -eq 0) { return "" }
    $nonLatest = @($list.imageDetails | Where-Object { $_.imageTags -and ($_.imageTags | Where-Object { $_ -ne "latest" }) } | ForEach-Object {
        $tag = ($_.imageTags | Where-Object { $_ -ne "latest" } | Select-Object -First 1)
        if ($tag) { [PSCustomObject]@{ Tag = $tag; Pushed = $_.imagePushedAt } }
    } | Where-Object { $_ })
    if ($nonLatest.Count -gt 0) {
        $latest = $nonLatest | Sort-Object { $_.Pushed } -Descending | Select-Object -First 1
        return "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$($script:EcrApiRepo):$($latest.Tag)"
    }
    return "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$($script:EcrApiRepo):latest"
}

function ConvertFrom-LaunchTemplateUserData {
    param([string]$UserData)
    if (-not $UserData) { return "" }
    try {
        return [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($UserData))
    } catch {
        return ""
    }
}

function Test-GeneratedApiUserDataMatchesSSOT {
    param([string]$ActualUserData)
    $raw = ConvertFrom-LaunchTemplateUserData -UserData $ActualUserData
    if (-not $raw) { return $false }

    $expectedImage = Get-ExpectedApiImageUriForDrift
    if (-not $expectedImage) { return $false }

    return (
        $raw.Contains($expectedImage) -and
        $raw.Contains("export AWS_REGION=`"$($script:Region)`"") -and
        $raw.Contains("aws ssm get-parameter --name `"$($script:SsmApiEnv)`"") -and
        $raw.Contains("--name academy-api") -and
        $raw.Contains("-p 8000:8000")
    )
}

function Add-UnexpectedBatchResourceRows {
    param(
        [System.Collections.ArrayList]$Rows,
        [string]$ResourceType,
        [array]$ActualNames,
        [array]$ExpectedNames,
        [scriptblock]$ActualLabel
    )

    $expectedSet = @{}
    foreach ($name in @($ExpectedNames | Where-Object { $_ })) {
        $expectedSet[$name] = $true
    }

    foreach ($name in @($ActualNames | Where-Object { $_ })) {
        if ($name -notlike "academy-*") { continue }
        if ($expectedSet.ContainsKey($name)) { continue }

        $actual = & $ActualLabel $name
        [void]$Rows.Add([PSCustomObject]@{
            ResourceType = $ResourceType
            Name = $name
            Expected = "not in SSOT"
            Actual = $actual
            Action = "Review"
        })
    }
}

function Get-ExpectedApiSecurityGroupForDrift {
    if ($script:ApiSecurityGroupId) { return ($script:ApiSecurityGroupId -split '#')[0].Trim() }
    if ($script:SecurityGroupApp) { return ($script:SecurityGroupApp -split '#')[0].Trim() }
    if (-not $script:SgAppName) { return "" }

    $args = @("ec2", "describe-security-groups", "--filters", "Name=group-name,Values=$($script:SgAppName)")
    if ($script:VpcId) { $args += "Name=vpc-id,Values=$($script:VpcId)" }
    $args += @("--region", $script:Region, "--output", "json")
    try {
        $sg = Invoke-AwsJson $args
        if ($sg -and $sg.SecurityGroups -and $sg.SecurityGroups.Count -gt 0) {
            return $sg.SecurityGroups[0].GroupId
        }
    } catch { }
    return ""
}

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
    Add-UnexpectedBatchResourceRows `
        -Rows $rows `
        -ResourceType "Batch CE" `
        -ActualNames $allCeNames `
        -ExpectedNames $script:SSOT_CE `
        -ActualLabel {
            param($name)
            $ce = $ceArr | Where-Object { $_.computeEnvironmentName -eq $name } | Select-Object -First 1
            if (-not $ce) { return "exists" }
            return "$($ce.status)/$($ce.state)"
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
    Add-UnexpectedBatchResourceRows `
        -Rows $rows `
        -ResourceType "Batch Queue" `
        -ActualNames $allQueueNames `
        -ExpectedNames $script:SSOT_Queue `
        -ActualLabel {
            param($name)
            $queue = $qArr | Where-Object { $_.jobQueueName -eq $name } | Select-Object -First 1
            if (-not $queue) { return "exists" }
            return "$($queue.status)/$($queue.state)"
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
        $script:ToolsASGName = @{ Min = $script:ToolsMinSize; Max = $script:ToolsMaxSize; Desired = $script:ToolsDesiredCapacity }
    }
    foreach ($asgName in $script:SSOT_ASG) {
        Write-Host "  [DRIFT-DEBUG] ASG ExpectedName: $asgName" -ForegroundColor DarkGray
        $matched = $asgArr | Where-Object { $_.AutoScalingGroupName -eq $asgName }
        if (-not $matched -or @($matched).Count -eq 0) {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "ASG"; Name = $asgName; Expected = "exists"; Actual = "missing"; Action = "Create" })
        } else {
            $a = @($matched)[0]
            $exp = $asgExpected[$asgName]
            $desiredOutOfRange = ($a.DesiredCapacity -lt $exp.Min) -or ($a.DesiredCapacity -gt $exp.Max)
            $capDrift = ($a.MinSize -ne $exp.Min) -or ($a.MaxSize -ne $exp.Max) -or $desiredOutOfRange
            if ($capDrift) {
                [void]$rows.Add([PSCustomObject]@{ ResourceType = "ASG"; Name = $asgName; Expected = "Min=$($exp.Min) Max=$($exp.Max) Desired in range (baseline $($exp.Desired))"; Actual = "Min=$($a.MinSize) Max=$($a.MaxSize) Desired=$($a.DesiredCapacity)"; Action = "Update" })
            } else {
                [void]$rows.Add([PSCustomObject]@{ ResourceType = "ASG"; Name = $asgName; Expected = "exists"; Actual = "exists"; Action = "NoOp" })
            }
        }
    }

    # API Launch Template drift: AMI, SG, UserData, InstanceProfile
    $apiLtName = $script:ApiLaunchTemplateName
    $apiLtR = Invoke-AwsJson @("ec2", "describe-launch-templates", "--launch-template-names", $apiLtName, "--region", $R, "--output", "json")
    if (-not $apiLtR -or -not $apiLtR.LaunchTemplates -or $apiLtR.LaunchTemplates.Count -eq 0) {
        [void]$rows.Add([PSCustomObject]@{ ResourceType = "API LT"; Name = $apiLtName; Expected = "exists"; Actual = "missing"; Action = "Create" })
    } else {
        $apiLtId = $apiLtR.LaunchTemplates[0].LaunchTemplateId
        $apiVerR = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $apiLtId, "--versions", '$Default', "--region", $R, "--output", "json")
        $apiLtDrift = $false
        if ($apiVerR -and $apiVerR.LaunchTemplateVersions -and $apiVerR.LaunchTemplateVersions.Count -gt 0) {
            $d = $apiVerR.LaunchTemplateVersions[0].LaunchTemplateData
            $actualAmi = if ($d.PSObject.Properties['ImageId']) { $d.ImageId } else { $null }
            $actualSg = $null; if ($d.PSObject.Properties['SecurityGroupIds'] -and $d.SecurityGroupIds -and $d.SecurityGroupIds.Count -gt 0) { $actualSg = $d.SecurityGroupIds[0] }
            $actualProfile = $null; if ($d.PSObject.Properties['IamInstanceProfile'] -and $d.IamInstanceProfile) { $actualProfile = $d.IamInstanceProfile.Name }
            $expectedSg = Get-ExpectedApiSecurityGroupForDrift
            $actualUserData = if ($d.PSObject.Properties['UserData']) { $d.UserData } else { "" }
            $userDataDrift = $false
            if ($script:ApiUserData) {
                $expectedUserData = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($script:ApiUserData))
                $userDataDrift = $actualUserData -ne $expectedUserData
            } else {
                $userDataDrift = -not (Test-GeneratedApiUserDataMatchesSSOT -ActualUserData $actualUserData)
            }
            $apiLtDrift = ($actualAmi -ne $script:ApiAmiId) -or ($actualSg -ne $expectedSg) -or ($actualProfile -ne $script:ApiInstanceProfile) -or $userDataDrift
        }
        if ($apiLtDrift) {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "API LT"; Name = $apiLtName; Expected = "AMI/SG/Profile/UserData SSOT"; Actual = "drift"; Action = "NewVersion" })
        } else {
            [void]$rows.Add([PSCustomObject]@{ ResourceType = "API LT"; Name = $apiLtName; Expected = "exists"; Actual = "exists"; Action = "NoOp" })
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
