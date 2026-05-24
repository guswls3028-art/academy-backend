# ASG AI: Ensure academy-ai-worker-asg exists; LT + capacity drift → update / instance-refresh.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"

function Get-AiLaunchTemplate {
    $r = Invoke-AwsJson @("ec2", "describe-launch-templates", "--launch-template-names", $script:AiLaunchTemplateName, "--region", $script:Region, "--output", "json")
    if (-not $r -or -not $r.LaunchTemplates -or $r.LaunchTemplates.Count -eq 0) { return $null }
    return $r.LaunchTemplates[0]
}

function Get-AiLaunchTemplateDefaultVersion {
    param([string]$LtId)
    $r = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $LtId, "--versions", '$Default', "--region", $script:Region, "--output", "json")
    if (-not $r -or -not $r.LaunchTemplateVersions -or $r.LaunchTemplateVersions.Count -eq 0) { return $null }
    return $r.LaunchTemplateVersions[0]
}

function Ensure-AiLaunchTemplate {
    $sg = $script:SecurityGroupApp
    if (-not $sg) { $sg = $script:BatchSecurityGroupId }
    $tagValue = $script:AiInstanceTagValue
    if (-not $tagValue) { $tagValue = "academy-v1-ai-worker" }
    $profile = if ($script:ApiInstanceProfile) { $script:ApiInstanceProfile } else { "academy-ec2-role" }
    $userDataB64 = ""
    if (-not $script:PlanMode) {
        $imgUri = Get-LatestWorkerImageUri -RepoName $script:EcrAiRepo
        if ($imgUri) {
            $userDataRaw = Get-WorkerLaunchTemplateUserData -ImageUri $imgUri -Region $script:Region -SsmParam $script:SsmWorkersEnv -ContainerName "academy-ai-worker-cpu"
            if ($userDataRaw) { $userDataB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($userDataRaw)) }
        }
    }
    $data = "ImageId=$($script:AiAmiId),InstanceType=$($script:AiInstanceType),IamInstanceProfile={Name=$profile}"
    if ($sg) { $data += ",SecurityGroupIds=$sg" }
    $data += ",TagSpecifications=[{ResourceType=instance,Tags=[{Key=Name,Value=$tagValue}]}]"
    if ($userDataB64) { $data += ",UserData=$userDataB64" }
    $lt = Get-AiLaunchTemplate
    if (-not $lt) {
        $create = Invoke-AwsJson @("ec2", "create-launch-template",
            "--launch-template-name", $script:AiLaunchTemplateName,
            "--version-description", "SSOT v1",
            "--launch-template-data", $data,
            "--region", $script:Region, "--output", "json")
        if (-not $create -or -not $create.LaunchTemplate) { throw "create-launch-template failed for $($script:AiLaunchTemplateName)" }
        Write-Ok "LaunchTemplate $($script:AiLaunchTemplateName) created"
        $script:ChangesMade = $true
        return @{ LtId = $create.LaunchTemplate.LaunchTemplateId; LtVersion = $create.LaunchTemplate.LatestVersionNumber; Updated = $true }
    }
    $ltId = $lt.LaunchTemplateId
    $defVer = Get-AiLaunchTemplateDefaultVersion -LtId $ltId
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
        if ($instTag -and $instTag.Tags) { $nameTag = $instTag.Tags | Where-Object { $_.Key -eq "Name" } | Select-Object -First 1; if ($nameTag) { $currentTag = $nameTag.Value } }
    }
    $currentUserData = if ($verData.PSObject.Properties['UserData']) { $verData.UserData } else { "" }
    if ($currentAmi -ne $script:AiAmiId -or $currentType -ne $script:AiInstanceType -or $currentSg -ne $sg -or $currentProfile -ne $profile -or $currentTag -ne $tagValue -or $currentUserData -ne $userDataB64) {
        $newVer = Invoke-AwsJson @("ec2", "create-launch-template-version",
            "--launch-template-id", $ltId,
            "--version-description", "SSOT v1 drift",
            "--launch-template-data", $data,
            "--region", $script:Region, "--output", "json")
        if (-not $newVer -or -not $newVer.LaunchTemplateVersion) { throw "create-launch-template-version failed" }
        $newVersion = $newVer.LaunchTemplateVersion.VersionNumber
        Invoke-Aws @("ec2", "modify-launch-template", "--launch-template-id", $ltId, "--default-version", $newVersion.ToString(), "--region", $script:Region) -ErrorMessage "modify-launch-template set default failed" | Out-Null
        Write-Ok "LaunchTemplate $($script:AiLaunchTemplateName) new default version $newVersion"
        $script:ChangesMade = $true
        return @{ LtId = $ltId; LtVersion = $newVersion; Updated = $true }
    }
    return @{ LtId = $ltId; LtVersion = $defVer.VersionNumber; Updated = $false }
}

function Ensure-AiSqsScaling {
    if ($script:PlanMode) { return }
    $relaxed = $script:RelaxedValidation

    $url = if ($script:AiSqsQueueUrl) { $script:AiSqsQueueUrl.Trim() } else { "" }
    $queueName = if ($script:AiSqsQueueName) { $script:AiSqsQueueName.Trim() } else { "" }

    if (-not $queueName -and $url) {
        if ($url -like "arn:*") {
            $queueName = ($url -split ":")[-1].Trim()
        } else {
            try {
                $u = [Uri]$url
                $path = $u.AbsolutePath.Trim("/")
                $parts = if ($path) { $path -split "/" } else { @() }
                $queueName = if ($parts.Count -gt 0) { $parts[-1].Trim() } else { "" }
            } catch {
                $queueName = ""
            }
        }
    }

    if (-not $queueName) {
        if ($relaxed) {
            Write-Warn "AI SQS scaling skipped: sqsQueueName empty and queue name could not be parsed from URL"
            return
        }
        throw "AI SQS scaling: sqsQueueName is empty and queue name could not be parsed from sqsQueueUrl (last path segment). Set sqsQueueName in params or fix URL."
    }

    $doScaling = {
        $region = $script:Region
        $queueDimension = "Name=QueueName,Value=$queueName"
        $scaleOutPolicyName = "ai-worker-scale-out"
        $ageScaleOutPolicyName = "ai-worker-scale-out-age"
        $scaleInPolicyName = "ai-worker-scale-in"
        $alarmOutName = "ai-worker-queue-high"
        $alarmAgeName = "ai-worker-queue-age-high"
        $alarmInName = "ai-worker-queue-low"
        $scaleOutThreshold = $script:AiScaleOutThreshold
        $scaleInThreshold = $script:AiScaleInThreshold
        $treatMissing = "notBreaching"

        $stepOut = '[{"MetricIntervalLowerBound":0,"MetricIntervalUpperBound":10,"ScalingAdjustment":1},{"MetricIntervalLowerBound":10,"MetricIntervalUpperBound":50,"ScalingAdjustment":3},{"MetricIntervalLowerBound":50,"ScalingAdjustment":5}]'
        $putOut = Invoke-AwsJson @("autoscaling", "put-scaling-policy",
            "--auto-scaling-group-name", $script:AiASGName,
            "--policy-name", $scaleOutPolicyName,
            "--policy-type", "StepScaling",
            "--adjustment-type", "ExactCapacity",
            "--metric-aggregation-type", "Average",
            "--step-adjustments", $stepOut,
            "--cooldown", $script:AiScaleOutCooldown.ToString(),
            "--region", $region, "--output", "json")
        $policyOutArn = $putOut.PolicyARN

        $stepAgeOut = '[{"MetricIntervalLowerBound":0,"ScalingAdjustment":1}]'
        $putAgeOut = Invoke-AwsJson @("autoscaling", "put-scaling-policy",
            "--auto-scaling-group-name", $script:AiASGName,
            "--policy-name", $ageScaleOutPolicyName,
            "--policy-type", "StepScaling",
            "--adjustment-type", "ExactCapacity",
            "--metric-aggregation-type", "Average",
            "--step-adjustments", $stepAgeOut,
            "--cooldown", $script:AiScaleOutCooldown.ToString(),
            "--region", $region, "--output", "json")
        $policyAgeOutArn = $putAgeOut.PolicyARN

        $stepIn = '[{"MetricIntervalUpperBound":0,"ScalingAdjustment":0}]'
        $putIn = Invoke-AwsJson @("autoscaling", "put-scaling-policy",
            "--auto-scaling-group-name", $script:AiASGName,
            "--policy-name", $scaleInPolicyName,
            "--policy-type", "StepScaling",
            "--adjustment-type", "ExactCapacity",
            "--metric-aggregation-type", "Average",
            "--step-adjustments", $stepIn,
            "--cooldown", $script:AiScaleInCooldown.ToString(),
            "--region", $region, "--output", "json")
        $policyInArn = $putIn.PolicyARN

        $ageAlarmActions = @($policyAgeOutArn)
        if ($script:AccountId) {
            $opsTopicArn = "arn:aws:sns:${region}:$($script:AccountId):academy-ops-alerts"
            try {
                Invoke-Aws @("sns", "get-topic-attributes", "--topic-arn", $opsTopicArn, "--region", $region) -ErrorMessage "sns-get-ai-worker-ops-alerts" | Out-Null
                $ageAlarmActions += $opsTopicArn
            } catch {
                Write-Warn "SNS topic academy-ops-alerts not found; AI worker age alarm keeps scaling action only."
            }
        }

        Invoke-Aws @("cloudwatch", "put-metric-alarm",
            "--alarm-name", $alarmOutName,
            "--metric-name", "ApproximateNumberOfMessagesVisible",
            "--namespace", "AWS/SQS",
            "--dimensions", $queueDimension,
            "--statistic", "Average", "--period", "60", "--evaluation-periods", "1",
            "--threshold", $scaleOutThreshold.ToString(),
            "--comparison-operator", "GreaterThanThreshold",
            "--treat-missing-data", $treatMissing,
            "--alarm-actions", $policyOutArn,
            "--region", $region) -ErrorMessage "put-metric-alarm ai scale-out" | Out-Null
        $ageAlarmArgs = @("cloudwatch", "put-metric-alarm",
            "--alarm-name", $alarmAgeName,
            "--metric-name", "ApproximateAgeOfOldestMessage",
            "--namespace", "AWS/SQS",
            "--dimensions", $queueDimension,
            "--statistic", "Average", "--period", "60", "--evaluation-periods", "1",
            "--threshold", "300",
            "--comparison-operator", "GreaterThanOrEqualToThreshold",
            "--treat-missing-data", $treatMissing,
            "--alarm-actions") + $ageAlarmActions + @("--region", $region)
        Invoke-Aws $ageAlarmArgs -ErrorMessage "put-metric-alarm ai age scale-out" | Out-Null
        Invoke-Aws @("cloudwatch", "put-metric-alarm",
            "--alarm-name", $alarmInName,
            "--metric-name", "ApproximateNumberOfMessagesVisible",
            "--namespace", "AWS/SQS",
            "--dimensions", $queueDimension,
            "--statistic", "Average", "--period", "60", "--evaluation-periods", "5",
            "--threshold", $scaleInThreshold.ToString(),
            "--comparison-operator", "LessThanThreshold",
            "--treat-missing-data", $treatMissing,
            "--alarm-actions", $policyInArn,
            "--region", $region) -ErrorMessage "put-metric-alarm ai scale-in" | Out-Null

        $descOut = Invoke-AwsJson @("cloudwatch", "describe-alarms", "--alarm-names", $alarmOutName, $alarmAgeName, "--region", $region, "--output", "json")
        $descIn = Invoke-AwsJson @("cloudwatch", "describe-alarms", "--alarm-names", $alarmInName, "--region", $region, "--output", "json")
        $aOut = $descOut.MetricAlarms | Where-Object { $_.AlarmName -eq $alarmOutName } | Select-Object -First 1
        $aAge = $descOut.MetricAlarms | Where-Object { $_.AlarmName -eq $alarmAgeName } | Select-Object -First 1
        $aIn = $descIn.MetricAlarms | Where-Object { $_.AlarmName -eq $alarmInName } | Select-Object -First 1
        if (-not $aOut -or -not $aOut.AlarmActions -or $aOut.AlarmActions -notcontains $policyOutArn -or $aOut.Dimensions[0].Value -ne $queueName) {
            throw "Alarm $alarmOutName does not reference queue=$queueName and scale-out policy ARN"
        }
        if (-not $aAge -or -not $aAge.AlarmActions -or $aAge.AlarmActions -notcontains $policyAgeOutArn -or $aAge.Dimensions[0].Value -ne $queueName) {
            throw "Alarm $alarmAgeName does not reference queue=$queueName and age scale-out policy ARN"
        }
        if (-not $aIn -or -not $aIn.AlarmActions -or $aIn.AlarmActions -notcontains $policyInArn -or $aIn.Dimensions[0].Value -ne $queueName) {
            throw "Alarm $alarmInName does not reference queue=$queueName and scale-in policy ARN"
        }

        Write-Ok "AI SQS scaling ensured (queue=$queueName)"
    }

    if ($relaxed) {
        try {
            & $doScaling
        } catch {
            $script:SqsScalingNotEnforced = $true
            Write-Warn "AI SQS scaling skipped (RelaxedValidation): $($_.Exception.Message)"
        }
    } else {
        & $doScaling
    }
}

function Ensure-ASGAi {
    Write-Step "Ensure ASG $($script:AiASGName)"
    if ($script:PlanMode) { Write-Ok "ASG AI check skipped (Plan)"; return }

    $ltResult = Ensure-AiLaunchTemplate
    # natEnabled=false: use public subnets for internet (no NAT); else private first
    $subnets = if (-not $script:NatEnabled) { @($script:PublicSubnets | Where-Object { $_ }) } else { @($script:PrivateSubnets | Where-Object { $_ }) }
    if (-not $subnets -or $subnets.Count -eq 0) { $subnets = @(($script:PrivateSubnets + $script:PublicSubnets) | Where-Object { $_ }) }
    $vpcZone = ($subnets -join ",")
    if (-not $vpcZone) { throw "PublicSubnets or PrivateSubnets empty; cannot create ASG" }

    $asgList = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $script:Region, "--output", "json")
    $asgArr = if ($asgList -and $asgList.PSObject.Properties['AutoScalingGroups']) { @($asgList.AutoScalingGroups) } else { @() }
    $asg = $asgArr | Where-Object { $_.AutoScalingGroupName -eq $script:AiASGName } | Select-Object -First 1

    if (-not $asg) {
        $ltSpec = "LaunchTemplateId=$($ltResult.LtId),Version='`$Latest'"
        $createArgs = @("autoscaling", "create-auto-scaling-group",
            "--auto-scaling-group-name", $script:AiASGName,
            "--launch-template", $ltSpec,
            "--min-size", $script:AiMinSize.ToString(),
            "--max-size", $script:AiMaxSize.ToString(),
            "--desired-capacity", $script:AiDesiredCapacity.ToString(),
            "--vpc-zone-identifier", $vpcZone,
            "--region", $script:Region)
        if ($script:AiScaleInProtection) { $createArgs += "--new-instances-protected-from-scale-in" }
        Invoke-Aws $createArgs -ErrorMessage "create-auto-scaling-group failed" | Out-Null
        Write-Ok "ASG $($script:AiASGName) created"
        $script:ChangesMade = $true
        if ($script:AiSqsQueueUrl -or $script:AiSqsQueueName) { Ensure-AiSqsScaling }
        return
    }

    $currentZones = ($asg.VpcZoneIdentifier -split "," | ForEach-Object { $_.Trim() }) -join ","
    $subnetDrift = ($currentZones -ne $vpcZone)
    if ($subnetDrift) {
        Invoke-Aws @("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", $script:AiASGName, "--vpc-zone-identifier", $vpcZone, "--region", $script:Region) -ErrorMessage "update-asg vpc-zone-identifier" | Out-Null
        Write-Ok "ASG $($script:AiASGName) vpc-zone-identifier updated"
        $script:ChangesMade = $true
        Invoke-Aws @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $script:AiASGName, "--region", $script:Region) -ErrorMessage "start-instance-refresh" | Out-Null
    }
    $capacityDrift = ($asg.MinSize -ne $script:AiMinSize) -or ($asg.MaxSize -ne $script:AiMaxSize)
    $clampedDesired = [Math]::Max($script:AiMinSize, [Math]::Min($script:AiMaxSize, $asg.DesiredCapacity))
    if ($capacityDrift -or $asg.DesiredCapacity -ne $clampedDesired) {
        $updateArgs = @("autoscaling", "update-auto-scaling-group",
            "--auto-scaling-group-name", $script:AiASGName,
            "--min-size", $script:AiMinSize.ToString(),
            "--max-size", $script:AiMaxSize.ToString(),
            "--desired-capacity", $clampedDesired.ToString(),
            "--region", $script:Region)
        if ($script:AiScaleInProtection) {
            $updateArgs += "--new-instances-protected-from-scale-in"
        } else {
            $updateArgs += "--no-new-instances-protected-from-scale-in"
        }
        Invoke-Aws $updateArgs -ErrorMessage "update-auto-scaling-group failed" | Out-Null
        $protLabel = if ($script:AiScaleInProtection) { "ON" } else { "OFF" }
        Write-Ok "ASG $($script:AiASGName) min=$($script:AiMinSize) max=$($script:AiMaxSize) desired(clamp)=$clampedDesired protection=$protLabel"
        $script:ChangesMade = $true
    }
    if ($script:AiScaleInProtection -and -not $asg.NewInstancesProtectedFromScaleIn) {
        Invoke-Aws @("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", $script:AiASGName, "--new-instances-protected-from-scale-in", "--region", $script:Region) -ErrorMessage "set scale-in protection" | Out-Null
        Write-Ok "ASG $($script:AiASGName) scale-in protection enabled"
        $script:ChangesMade = $true
    }
    if (-not $script:AiScaleInProtection -and $asg.NewInstancesProtectedFromScaleIn) {
        Invoke-Aws @("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", $script:AiASGName, "--no-new-instances-protected-from-scale-in", "--region", $script:Region) -ErrorMessage "disable scale-in protection" | Out-Null
        Write-Ok "ASG $($script:AiASGName) scale-in protection disabled"
        $script:ChangesMade = $true
    }

    if ($ltResult.Updated) {
        try {
            Invoke-Aws @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $script:AiASGName, "--region", $script:Region) -ErrorMessage "start-instance-refresh failed" | Out-Null
            Write-Ok "ASG $($script:AiASGName) instance-refresh started (LT drift)"
        } catch {
            if ($_.Exception.Message -match "InstanceRefreshInProgress") {
                Write-Warn "ASG $($script:AiASGName) instance-refresh already in progress; LT updated. New instances will use new LT."
            } else { throw }
        }
        $script:ChangesMade = $true
    }

    if ($script:AiSqsQueueUrl -or $script:AiSqsQueueName) { Ensure-AiSqsScaling }

    if (-not $capacityDrift -and -not $ltResult.Updated) {
        Write-Ok "ASG $($script:AiASGName) idempotent Desired(clamp)=$clampedDesired Min=$($asg.MinSize) Max=$($asg.MaxSize)"
    }
}
