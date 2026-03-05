# ASG Messaging: Ensure academy-messaging-worker-asg exists; LT + capacity drift → update / instance-refresh.
$ErrorActionPreference = "Stop"

function Get-MessagingLaunchTemplate {
    $r = Invoke-AwsJson @("ec2", "describe-launch-templates", "--launch-template-names", $script:MessagingLaunchTemplateName, "--region", $script:Region, "--output", "json")
    if (-not $r -or -not $r.LaunchTemplates -or $r.LaunchTemplates.Count -eq 0) { return $null }
    return $r.LaunchTemplates[0]
}

function Get-MessagingLaunchTemplateDefaultVersion {
    param([string]$LtId)
    $r = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $LtId, "--versions", '$Default', "--region", $script:Region, "--output", "json")
    if (-not $r -or -not $r.LaunchTemplateVersions -or $r.LaunchTemplateVersions.Count -eq 0) { return $null }
    return $r.LaunchTemplateVersions[0]
}

function Ensure-MessagingLaunchTemplate {
    $sg = $script:SecurityGroupApp
    if (-not $sg) { $sg = $script:BatchSecurityGroupId }
    $data = "ImageId=$($script:MessagingAmiId),InstanceType=$($script:MessagingInstanceType)"
    if ($sg) { $data += ",SecurityGroupIds=$sg" }
    $lt = Get-MessagingLaunchTemplate
    if (-not $lt) {
        $create = Invoke-AwsJson @("ec2", "create-launch-template",
            "--launch-template-name", $script:MessagingLaunchTemplateName,
            "--version-description", "SSOT v1",
            "--launch-template-data", $data,
            "--region", $script:Region, "--output", "json")
        if (-not $create -or -not $create.LaunchTemplate) { throw "create-launch-template failed for $($script:MessagingLaunchTemplateName)" }
        Write-Ok "LaunchTemplate $($script:MessagingLaunchTemplateName) created"
        $script:ChangesMade = $true
        return @{ LtId = $create.LaunchTemplate.LaunchTemplateId; LtVersion = $create.LaunchTemplate.LatestVersionNumber; Updated = $true }
    }
    $ltId = $lt.LaunchTemplateId
    $defVer = Get-MessagingLaunchTemplateDefaultVersion -LtId $ltId
    if (-not $defVer) { return @{ LtId = $ltId; LtVersion = 1; Updated = $false } }
    $verData = $defVer.LaunchTemplateData
    $currentAmi = if ($verData.PSObject.Properties['ImageId']) { $verData.ImageId } else { $null }
    $currentType = if ($verData.PSObject.Properties['InstanceType']) { $verData.InstanceType } else { $null }
    $currentSg = $null
    if ($verData.PSObject.Properties['SecurityGroupIds'] -and $verData.SecurityGroupIds -and $verData.SecurityGroupIds.Count -gt 0) { $currentSg = $verData.SecurityGroupIds[0] }
    if ($currentAmi -ne $script:MessagingAmiId -or $currentType -ne $script:MessagingInstanceType -or $currentSg -ne $sg) {
        $newVer = Invoke-AwsJson @("ec2", "create-launch-template-version",
            "--launch-template-id", $ltId,
            "--version-description", "SSOT v1 drift",
            "--launch-template-data", $data,
            "--region", $script:Region, "--output", "json")
        if (-not $newVer -or -not $newVer.LaunchTemplateVersion) { throw "create-launch-template-version failed" }
        $newVersion = $newVer.LaunchTemplateVersion.VersionNumber
        Invoke-Aws @("ec2", "modify-launch-template", "--launch-template-id", $ltId, "--default-version", $newVersion.ToString(), "--region", $script:Region) -ErrorMessage "modify-launch-template set default failed" | Out-Null
        Write-Ok "LaunchTemplate $($script:MessagingLaunchTemplateName) new default version $newVersion"
        $script:ChangesMade = $true
        return @{ LtId = $ltId; LtVersion = $newVersion; Updated = $true }
    }
    return @{ LtId = $ltId; LtVersion = $defVer.VersionNumber; Updated = $false }
}

function Ensure-MessagingSqsScaling {
    if ($script:PlanMode) { return }
    $relaxed = $script:RelaxedValidation

    $url = if ($script:MessagingSqsQueueUrl) { $script:MessagingSqsQueueUrl.Trim() } else { "" }
    $queueName = if ($script:MessagingSqsQueueName) { $script:MessagingSqsQueueName.Trim() } else { "" }

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
            Write-Warn "Messaging SQS scaling skipped: sqsQueueName empty and queue name could not be parsed from URL"
            return
        }
        throw "Messaging SQS scaling: sqsQueueName is empty and queue name could not be parsed from sqsQueueUrl (last path segment). Set sqsQueueName in params or fix URL."
    }

    $doScaling = {
        $resourceId = "auto-scaling-group/$($script:MessagingASGName)"
        $region = $script:Region
        $ns = "ec2"
        $dim = "ec2:autoScalingGroup:DesiredCapacity"
        $scaleOutPolicyName = "$($script:MessagingASGName)-sqs-scale-out"
        $scaleInPolicyName = "$($script:MessagingASGName)-sqs-scale-in"
        $alarmOutName = "$($script:MessagingASGName)-sqs-scale-out"
        $alarmInName = "$($script:MessagingASGName)-sqs-scale-in"
        $scaleOutThreshold = $script:MessagingScaleOutThreshold
        $scaleInThreshold = $script:MessagingScaleInThreshold
        $treatMissing = "notBreaching"

        try {
            Invoke-Aws @("application-autoscaling", "register-scalable-target",
                "--service-namespace", $ns,
                "--resource-id", $resourceId,
                "--scalable-dimension", $dim,
                "--min-capacity", $script:MessagingMinSize.ToString(),
                "--max-capacity", $script:MessagingMaxSize.ToString(),
                "--region", $region) -ErrorMessage "register-scalable-target messaging" | Out-Null
        } catch {
            if ($_.Exception.Message -match "scalableDimension|ValidationException|ec2:autoScalingGroup") {
                Write-Warn "Application Auto Scaling does not support EC2 ASG; SQS-based scaling skipped. Use ASG min/max/desired or EC2 scaling policies."
                $script:SqsScalingNotEnforced = $true
                return
            }
            throw
        }

        $targets = Invoke-AwsJson @("application-autoscaling", "describe-scalable-targets",
            "--service-namespace", $ns, "--resource-ids", $resourceId, "--region", $region, "--output", "json")
        $st = $targets.ScalableTargets | Where-Object { $_.ResourceId -eq $resourceId } | Select-Object -First 1
        if (-not $st -or [int]$st.MinCapacity -ne $script:MessagingMinSize -or [int]$st.MaxCapacity -ne $script:MessagingMaxSize) {
            throw "ScalableTarget min/max mismatch: expected Min=$($script:MessagingMinSize) Max=$($script:MessagingMaxSize)"
        }

        $stepOut = '{"AdjustmentType":"ChangeInCapacity","MetricAggregationType":"Average","Cooldown":' + $script:MessagingScaleOutCooldown + ',"StepAdjustments":[{"MetricIntervalLowerBound":0,"ScalingAdjustment":1}]}'
        $putOut = Invoke-AwsJson @("application-autoscaling", "put-scaling-policy",
            "--service-namespace", $ns, "--resource-id", $resourceId, "--scalable-dimension", $dim,
            "--policy-name", $scaleOutPolicyName, "--policy-type", "StepScaling",
            "--step-scaling-policy-configuration", $stepOut,
            "--region", $region, "--output", "json")
        $policyOutArn = $putOut.PolicyARN

        $stepIn = '{"AdjustmentType":"ChangeInCapacity","MetricAggregationType":"Average","Cooldown":' + $script:MessagingScaleInCooldown + ',"StepAdjustments":[{"MetricIntervalUpperBound":0,"ScalingAdjustment":-1}]}'
        $putIn = Invoke-AwsJson @("application-autoscaling", "put-scaling-policy",
            "--service-namespace", $ns, "--resource-id", $resourceId, "--scalable-dimension", $dim,
            "--policy-name", $scaleInPolicyName, "--policy-type", "StepScaling",
            "--step-scaling-policy-configuration", $stepIn,
            "--region", $region, "--output", "json")
        $policyInArn = $putIn.PolicyARN

        Invoke-Aws @("cloudwatch", "put-metric-alarm",
            "--alarm-name", $alarmOutName,
            "--metric-name", "ApproximateNumberOfMessagesVisible",
            "--namespace", "AWS/SQS",
            "--dimensions", "Name=QueueName,Value=$queueName",
            "--statistic", "Average", "--period", "60", "--evaluation-periods", "1",
            "--threshold", $scaleOutThreshold.ToString(),
            "--comparison-operator", "GreaterThanThreshold",
            "--treat-missing-data", $treatMissing,
            "--alarm-actions", $policyOutArn,
            "--region", $region) -ErrorMessage "put-metric-alarm scale-out" | Out-Null
        Invoke-Aws @("cloudwatch", "put-metric-alarm",
            "--alarm-name", $alarmInName,
            "--metric-name", "ApproximateNumberOfMessagesVisible",
            "--namespace", "AWS/SQS",
            "--dimensions", "Name=QueueName,Value=$queueName",
            "--statistic", "Average", "--period", "60", "--evaluation-periods", "1",
            "--threshold", $scaleInThreshold.ToString(),
            "--comparison-operator", "LessThanOrEqualToThreshold",
            "--treat-missing-data", $treatMissing,
            "--alarm-actions", $policyInArn,
            "--region", $region) -ErrorMessage "put-metric-alarm scale-in" | Out-Null

        $descOut = Invoke-AwsJson @("cloudwatch", "describe-alarms", "--alarm-names", $alarmOutName, "--region", $region, "--output", "json")
        $descIn = Invoke-AwsJson @("cloudwatch", "describe-alarms", "--alarm-names", $alarmInName, "--region", $region, "--output", "json")
        $aOut = $descOut.MetricAlarms | Where-Object { $_.AlarmName -eq $alarmOutName } | Select-Object -First 1
        $aIn = $descIn.MetricAlarms | Where-Object { $_.AlarmName -eq $alarmInName } | Select-Object -First 1
        if (-not $aOut -or -not $aOut.AlarmActions -or $aOut.AlarmActions -notcontains $policyOutArn) {
            throw "Alarm $alarmOutName alarm-actions does not reference policy ARN"
        }
        if (-not $aIn -or -not $aIn.AlarmActions -or $aIn.AlarmActions -notcontains $policyInArn) {
            throw "Alarm $alarmInName alarm-actions does not reference policy ARN"
        }

        Write-Ok "Messaging SQS scaling ensured (queue=$queueName)"
    }

    if ($relaxed) {
        try {
            & $doScaling
        } catch {
            $script:SqsScalingNotEnforced = $true
            Write-Warn "Messaging SQS scaling skipped (RelaxedValidation): $($_.Exception.Message)"
        }
    } else {
        & $doScaling
    }
}

function Ensure-ASGMessaging {
    Write-Step "Ensure ASG $($script:MessagingASGName)"
    if ($script:PlanMode) { Write-Ok "ASG Messaging check skipped (Plan)"; return }

    $ltResult = Ensure-MessagingLaunchTemplate
    $subnets = @($script:PrivateSubnets | Where-Object { $_ })
    if (-not $subnets -or $subnets.Count -eq 0) { $subnets = @($script:PublicSubnets | Where-Object { $_ }) }
    $vpcZone = ($subnets -join ",")
    if (-not $vpcZone) { throw "PublicSubnets or PrivateSubnets empty; cannot create ASG" }

    $asgList = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $script:Region, "--output", "json")
    $asgArr = if ($asgList -and $asgList.PSObject.Properties['AutoScalingGroups']) { @($asgList.AutoScalingGroups) } else { @() }
    $asg = $asgArr | Where-Object { $_.AutoScalingGroupName -eq $script:MessagingASGName } | Select-Object -First 1

    if (-not $asg) {
        $ltSpec = "LaunchTemplateId=$($ltResult.LtId),Version='`$Latest'"
        $createArgs = @("autoscaling", "create-auto-scaling-group",
            "--auto-scaling-group-name", $script:MessagingASGName,
            "--launch-template", $ltSpec,
            "--min-size", $script:MessagingMinSize.ToString(),
            "--max-size", $script:MessagingMaxSize.ToString(),
            "--desired-capacity", $script:MessagingDesiredCapacity.ToString(),
            "--vpc-zone-identifier", $vpcZone,
            "--region", $script:Region)
        if ($script:MessagingScaleInProtection) { $createArgs += "--new-instances-protected-from-scale-in" }
        Invoke-Aws $createArgs -ErrorMessage "create-auto-scaling-group failed" | Out-Null
        Write-Ok "ASG $($script:MessagingASGName) created"
        $script:ChangesMade = $true
        if ($script:MessagingSqsQueueUrl -or $script:MessagingSqsQueueName) { Ensure-MessagingSqsScaling }
        return
    }

    $capacityDrift = ($asg.MinSize -ne $script:MessagingMinSize) -or ($asg.MaxSize -ne $script:MessagingMaxSize)
    $clampedDesired = [Math]::Max($script:MessagingMinSize, [Math]::Min($script:MessagingMaxSize, $asg.DesiredCapacity))
    if ($capacityDrift -or $asg.DesiredCapacity -ne $clampedDesired) {
        Invoke-Aws @("autoscaling", "update-auto-scaling-group",
            "--auto-scaling-group-name", $script:MessagingASGName,
            "--min-size", $script:MessagingMinSize.ToString(),
            "--max-size", $script:MessagingMaxSize.ToString(),
            "--desired-capacity", $clampedDesired.ToString(),
            "--new-instances-protected-from-scale-in",
            "--region", $script:Region) -ErrorMessage "update-auto-scaling-group failed" | Out-Null
        Write-Ok "ASG $($script:MessagingASGName) min=$($script:MessagingMinSize) max=$($script:MessagingMaxSize) desired(clamp)=$clampedDesired protection=ON"
        $script:ChangesMade = $true
    }
    if ($script:MessagingScaleInProtection -and -not $asg.NewInstancesProtectedFromScaleIn) {
        Invoke-Aws @("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", $script:MessagingASGName, "--new-instances-protected-from-scale-in", "--region", $script:Region) -ErrorMessage "set scale-in protection" | Out-Null
        Write-Ok "ASG $($script:MessagingASGName) scale-in protection enabled"
        $script:ChangesMade = $true
    }

    if ($ltResult.Updated) {
        Invoke-Aws @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $script:MessagingASGName, "--region", $script:Region) -ErrorMessage "start-instance-refresh failed" | Out-Null
        Write-Ok "ASG $($script:MessagingASGName) instance-refresh started (LT drift)"
        $script:ChangesMade = $true
    }

    if ($script:MessagingSqsQueueUrl -or $script:MessagingSqsQueueName) { Ensure-MessagingSqsScaling }

    if (-not $capacityDrift -and -not $ltResult.Updated) {
        Write-Ok "ASG $($script:MessagingASGName) idempotent Desired(clamp)=$clampedDesired Min=$($asg.MinSize) Max=$($asg.MaxSize)"
    }
}
