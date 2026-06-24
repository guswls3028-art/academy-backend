# ASG Tools: lightweight worker for document conversion jobs (PPT/PDF).
$ErrorActionPreference = "Stop"

function Get-ToolsLaunchTemplate {
    $r = Invoke-AwsJson @("ec2", "describe-launch-templates", "--launch-template-names", $script:ToolsLaunchTemplateName, "--region", $script:Region, "--output", "json")
    if (-not $r -or -not $r.LaunchTemplates -or $r.LaunchTemplates.Count -eq 0) { return $null }
    return $r.LaunchTemplates[0]
}

function Get-ToolsLaunchTemplateDefaultVersion {
    param([string]$LtId)
    $r = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $LtId, "--versions", '$Default', "--region", $script:Region, "--output", "json")
    if (-not $r -or -not $r.LaunchTemplateVersions -or $r.LaunchTemplateVersions.Count -eq 0) { return $null }
    return $r.LaunchTemplateVersions[0]
}

function Ensure-ToolsLaunchTemplate {
    $sg = $script:SecurityGroupApp
    if (-not $sg) { $sg = $script:BatchSecurityGroupId }
    $profile = if ($script:ApiInstanceProfile) { $script:ApiInstanceProfile } else { "academy-ec2-role" }
    $tagValue = if ($script:ToolsInstanceTagValue) { $script:ToolsInstanceTagValue } else { "academy-v1-tools-worker" }
    $userDataB64 = ""
    if (-not $script:PlanMode) {
        $imgUri = Get-LatestWorkerImageUri -RepoName $script:EcrToolsRepo
        if ($imgUri) {
            $userDataRaw = Get-WorkerLaunchTemplateUserData -ImageUri $imgUri -Region $script:Region -SsmParam $script:SsmWorkersEnv -ContainerName "academy-tools-worker"
            if ($userDataRaw) { $userDataB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($userDataRaw)) }
        }
    }

    $data = "ImageId=$($script:ToolsAmiId),InstanceType=$($script:ToolsInstanceType),IamInstanceProfile={Name=$profile}"
    if ($sg) { $data += ",SecurityGroupIds=$sg" }
    $data += ",TagSpecifications=[{ResourceType=instance,Tags=[{Key=Name,Value=$tagValue}]}]"
    if ($userDataB64) { $data += ",UserData=$userDataB64" }

    $lt = Get-ToolsLaunchTemplate
    if (-not $lt) {
        $create = Invoke-AwsJson @("ec2", "create-launch-template",
            "--launch-template-name", $script:ToolsLaunchTemplateName,
            "--version-description", "SSOT v1 tools worker",
            "--launch-template-data", $data,
            "--region", $script:Region, "--output", "json")
        if (-not $create -or -not $create.LaunchTemplate) { throw "create-launch-template failed for $($script:ToolsLaunchTemplateName)" }
        Write-Ok "LaunchTemplate $($script:ToolsLaunchTemplateName) created"
        $script:ChangesMade = $true
        return @{ LtId = $create.LaunchTemplate.LaunchTemplateId; LtVersion = $create.LaunchTemplate.LatestVersionNumber; Updated = $true }
    }

    $ltId = $lt.LaunchTemplateId
    $defVer = Get-ToolsLaunchTemplateDefaultVersion -LtId $ltId
    if (-not $defVer) { return @{ LtId = $ltId; LtVersion = 1; Updated = $false } }
    $verData = $defVer.LaunchTemplateData
    $currentAmi = if ($verData.PSObject.Properties['ImageId']) { $verData.ImageId } else { $null }
    $currentType = if ($verData.PSObject.Properties['InstanceType']) { $verData.InstanceType } else { $null }
    $currentSg = $null
    if ($verData.PSObject.Properties['SecurityGroupIds'] -and $verData.SecurityGroupIds -and $verData.SecurityGroupIds.Count -gt 0) { $currentSg = $verData.SecurityGroupIds[0] }
    $currentProfile = $null
    if ($verData.PSObject.Properties['IamInstanceProfile'] -and $verData.IamInstanceProfile) { $currentProfile = $verData.IamInstanceProfile.Name }
    $currentTag = $null
    if ($verData.PSObject.Properties['TagSpecifications'] -and $verData.TagSpecifications) {
        $instTag = $verData.TagSpecifications | Where-Object { $_.ResourceType -eq "instance" } | Select-Object -First 1
        if ($instTag -and $instTag.Tags) {
            $nameTag = $instTag.Tags | Where-Object { $_.Key -eq "Name" } | Select-Object -First 1
            if ($nameTag) { $currentTag = $nameTag.Value }
        }
    }
    $currentUserData = if ($verData.PSObject.Properties['UserData']) { $verData.UserData } else { "" }

    if ($currentAmi -ne $script:ToolsAmiId -or $currentType -ne $script:ToolsInstanceType -or $currentSg -ne $sg -or $currentProfile -ne $profile -or $currentTag -ne $tagValue -or $currentUserData -ne $userDataB64) {
        $newVer = Invoke-AwsJson @("ec2", "create-launch-template-version",
            "--launch-template-id", $ltId,
            "--version-description", "SSOT v1 tools drift",
            "--launch-template-data", $data,
            "--region", $script:Region, "--output", "json")
        if (-not $newVer -or -not $newVer.LaunchTemplateVersion) { throw "create-launch-template-version failed for tools worker" }
        $newVersion = $newVer.LaunchTemplateVersion.VersionNumber
        Invoke-Aws @("ec2", "modify-launch-template", "--launch-template-id", $ltId, "--default-version", $newVersion.ToString(), "--region", $script:Region) -ErrorMessage "modify tools launch-template default failed" | Out-Null
        Write-Ok "LaunchTemplate $($script:ToolsLaunchTemplateName) new default version $newVersion"
        $script:ChangesMade = $true
        return @{ LtId = $ltId; LtVersion = $newVersion; Updated = $true }
    }
    return @{ LtId = $ltId; LtVersion = $defVer.VersionNumber; Updated = $false }
}

function Ensure-ToolsSqsScaling {
    if ($script:PlanMode) { return }
    $queueName = if ($script:ToolsSqsQueueName) { $script:ToolsSqsQueueName.Trim() } else { "academy-v1-tools-queue" }
    $region = $script:Region
    $queueDimension = "Name=QueueName,Value=$queueName"
    $scaleOutPolicyName = "$($script:ToolsASGName)-sqs-scale-out"
    $scaleInPolicyName = "$($script:ToolsASGName)-sqs-scale-in"
    $alarmOutName = "$($script:ToolsASGName)-sqs-scale-out"
    $alarmInName = "$($script:ToolsASGName)-sqs-scale-in"

    $putOut = Invoke-AwsJson @("autoscaling", "put-scaling-policy",
        "--auto-scaling-group-name", $script:ToolsASGName,
        "--policy-name", $scaleOutPolicyName,
        "--policy-type", "StepScaling",
        "--adjustment-type", "ChangeInCapacity",
        "--metric-aggregation-type", "Average",
        "--step-adjustments", '[{"MetricIntervalLowerBound":0,"ScalingAdjustment":1}]',
        "--cooldown", $script:ToolsScaleOutCooldown.ToString(),
        "--region", $region, "--output", "json")
    $putIn = Invoke-AwsJson @("autoscaling", "put-scaling-policy",
        "--auto-scaling-group-name", $script:ToolsASGName,
        "--policy-name", $scaleInPolicyName,
        "--policy-type", "StepScaling",
        "--adjustment-type", "ChangeInCapacity",
        "--metric-aggregation-type", "Average",
        "--step-adjustments", '[{"MetricIntervalUpperBound":0,"ScalingAdjustment":-1}]',
        "--cooldown", $script:ToolsScaleInCooldown.ToString(),
        "--region", $region, "--output", "json")

    Invoke-Aws @("cloudwatch", "put-metric-alarm",
        "--alarm-name", $alarmOutName,
        "--metric-name", "ApproximateNumberOfMessagesVisible",
        "--namespace", "AWS/SQS",
        "--dimensions", $queueDimension,
        "--statistic", "Average", "--period", "60", "--evaluation-periods", "1",
        "--threshold", $script:ToolsScaleOutThreshold.ToString(),
        "--comparison-operator", "GreaterThanThreshold",
        "--treat-missing-data", "notBreaching",
        "--alarm-actions", $putOut.PolicyARN,
        "--region", $region) -ErrorMessage "put tools worker scale-out alarm" | Out-Null
    $scaleInMetrics = @(
        @{
            Id = "visible"
            MetricStat = @{
                Metric = @{
                    Namespace = "AWS/SQS"
                    MetricName = "ApproximateNumberOfMessagesVisible"
                    Dimensions = @(@{ Name = "QueueName"; Value = $queueName })
                }
                Period = 60
                Stat = "Average"
            }
            ReturnData = $false
        },
        @{
            Id = "inflight"
            MetricStat = @{
                Metric = @{
                    Namespace = "AWS/SQS"
                    MetricName = "ApproximateNumberOfMessagesNotVisible"
                    Dimensions = @(@{ Name = "QueueName"; Value = $queueName })
                }
                Period = 60
                Stat = "Average"
            }
            ReturnData = $false
        },
        @{
            Id = "delayed"
            MetricStat = @{
                Metric = @{
                    Namespace = "AWS/SQS"
                    MetricName = "ApproximateNumberOfMessagesDelayed"
                    Dimensions = @(@{ Name = "QueueName"; Value = $queueName })
                }
                Period = 60
                Stat = "Average"
            }
            ReturnData = $false
        },
        @{
            Id = "backlog"
            Expression = "visible+inflight+delayed"
            Label = "Tools SQS backlog"
            ReturnData = $true
        }
    ) | ConvertTo-Json -Depth 8 -Compress
    $scaleInMetricsRef = Convert-JsonArgToFileRef $scaleInMetrics
    Invoke-Aws @("cloudwatch", "put-metric-alarm",
        "--alarm-name", $alarmInName,
        "--evaluation-periods", "5",
        "--threshold", $script:ToolsScaleInThreshold.ToString(),
        "--comparison-operator", "LessThanOrEqualToThreshold",
        "--treat-missing-data", "notBreaching",
        "--alarm-actions", $putIn.PolicyARN,
        "--metrics", $scaleInMetricsRef,
        "--region", $region) -ErrorMessage "put tools worker scale-in alarm" | Out-Null
    Write-Ok "Tools SQS scaling ensured (queue=$queueName, scale-in uses visible+inflight+delayed backlog)"
}

function Ensure-ASGTools {
    Write-Step "Ensure ASG $($script:ToolsASGName)"
    if ($script:PlanMode) { Write-Ok "ASG Tools check skipped (Plan)"; return }

    $ltResult = Ensure-ToolsLaunchTemplate
    $subnets = if (-not $script:NatEnabled) { @($script:PublicSubnets | Where-Object { $_ }) } else { @($script:PrivateSubnets | Where-Object { $_ }) }
    if (-not $subnets -or $subnets.Count -eq 0) { $subnets = @(($script:PrivateSubnets + $script:PublicSubnets) | Where-Object { $_ }) }
    $vpcZone = ($subnets -join ",")
    if (-not $vpcZone) { throw "PublicSubnets or PrivateSubnets empty; cannot create tools ASG" }

    $asgList = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $script:Region, "--output", "json")
    $asgArr = if ($asgList -and $asgList.PSObject.Properties['AutoScalingGroups']) { @($asgList.AutoScalingGroups) } else { @() }
    $asg = $asgArr | Where-Object { $_.AutoScalingGroupName -eq $script:ToolsASGName } | Select-Object -First 1

    if (-not $asg) {
        $ltSpec = "LaunchTemplateId=$($ltResult.LtId),Version='`$Latest'"
        $createArgs = @("autoscaling", "create-auto-scaling-group",
            "--auto-scaling-group-name", $script:ToolsASGName,
            "--launch-template", $ltSpec,
            "--min-size", $script:ToolsMinSize.ToString(),
            "--max-size", $script:ToolsMaxSize.ToString(),
            "--desired-capacity", $script:ToolsDesiredCapacity.ToString(),
            "--vpc-zone-identifier", $vpcZone,
            "--region", $script:Region)
        if ($script:ToolsScaleInProtection) { $createArgs += "--new-instances-protected-from-scale-in" }
        Invoke-Aws $createArgs -ErrorMessage "create tools auto-scaling-group failed" | Out-Null
        Write-Ok "ASG $($script:ToolsASGName) created"
        $script:ChangesMade = $true
        Ensure-ToolsSqsScaling
        return
    }

    $capacityDrift = ($asg.MinSize -ne $script:ToolsMinSize) -or ($asg.MaxSize -ne $script:ToolsMaxSize)
    $clampedDesired = [Math]::Max($script:ToolsMinSize, [Math]::Min($script:ToolsMaxSize, $asg.DesiredCapacity))
    if ($capacityDrift -or $asg.DesiredCapacity -ne $clampedDesired) {
        $updateArgs = @("autoscaling", "update-auto-scaling-group",
            "--auto-scaling-group-name", $script:ToolsASGName,
            "--min-size", $script:ToolsMinSize.ToString(),
            "--max-size", $script:ToolsMaxSize.ToString(),
            "--desired-capacity", $clampedDesired.ToString(),
            "--region", $script:Region)
        if ($script:ToolsScaleInProtection) {
            $updateArgs += "--new-instances-protected-from-scale-in"
        } else {
            $updateArgs += "--no-new-instances-protected-from-scale-in"
        }
        Invoke-Aws $updateArgs -ErrorMessage "update tools auto-scaling-group failed" | Out-Null
        Write-Ok "ASG $($script:ToolsASGName) min=$($script:ToolsMinSize) max=$($script:ToolsMaxSize) desired=$clampedDesired"
        $script:ChangesMade = $true
    }

    if ($ltResult.Updated) {
        try {
            Invoke-Aws @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $script:ToolsASGName, "--region", $script:Region) -ErrorMessage "start tools instance-refresh failed" | Out-Null
            Write-Ok "ASG $($script:ToolsASGName) instance-refresh started (LT drift)"
        } catch {
            if ($_.Exception.Message -match "InstanceRefreshInProgress") {
                Write-Warn "ASG $($script:ToolsASGName) instance-refresh already in progress"
            } else { throw }
        }
        $script:ChangesMade = $true
    }

    Ensure-ToolsSqsScaling
    if (-not $capacityDrift -and -not $ltResult.Updated) {
        Write-Ok "ASG $($script:ToolsASGName) idempotent Desired=$clampedDesired Min=$($asg.MinSize) Max=$($asg.MaxSize)"
    }
}
