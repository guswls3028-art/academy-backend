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
    $data = "ImageId=$($script:AiAmiId),InstanceType=$($script:AiInstanceType)"
    if ($sg) { $data += ",SecurityGroupIds=$sg" }
    $data += ",TagSpecifications=[{ResourceType=instance,Tags=[{Key=Name,Value=$tagValue}]}]"
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
    $currentTag = $null
    if ($verData.PSObject.Properties['TagSpecifications'] -and $verData.TagSpecifications) {
        $instTag = $verData.TagSpecifications | Where-Object { $_.ResourceType -eq "instance" } | Select-Object -First 1
        if ($instTag -and $instTag.Tags) { $nameTag = $instTag.Tags | Where-Object { $_.Key -eq "Name" } | Select-Object -First 1; if ($nameTag) { $currentTag = $nameTag.Value } }
    }
    if ($currentAmi -ne $script:AiAmiId -or $currentType -ne $script:AiInstanceType -or $currentSg -ne $sg -or $currentTag -ne $tagValue) {
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
        $resourceId = "auto-scaling-group/$($script:AiASGName)"
        $region = $script:Region
        $ns = "ec2"
        $dim = "ec2:autoScalingGroup:DesiredCapacity"
        $scaleOutPolicyName = "$($script:AiASGName)-sqs-scale-out"
        $scaleInPolicyName = "$($script:AiASGName)-sqs-scale-in"
        $alarmOutName = "$($script:AiASGName)-sqs-scale-out"
        $alarmInName = "$($script:AiASGName)-sqs-scale-in"
        $scaleOutThreshold = $script:AiScaleOutThreshold
        $scaleInThreshold = $script:AiScaleInThreshold
        $treatMissing = "notBreaching"

        try {
            Invoke-Aws @("application-autoscaling", "register-scalable-target",
                "--service-namespace", $ns,
                "--resource-id", $resourceId,
                "--scalable-dimension", $dim,
                "--min-capacity", $script:AiMinSize.ToString(),
                "--max-capacity", $script:AiMaxSize.ToString(),
                "--region", $region) -ErrorMessage "register-scalable-target ai" | Out-Null
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
        if (-not $st -or [int]$st.MinCapacity -ne $script:AiMinSize -or [int]$st.MaxCapacity -ne $script:AiMaxSize) {
            throw "ScalableTarget min/max mismatch: expected Min=$($script:AiMinSize) Max=$($script:AiMaxSize)"
        }

        $stepOut = '{"AdjustmentType":"ChangeInCapacity","MetricAggregationType":"Average","Cooldown":' + $script:AiScaleOutCooldown + ',"StepAdjustments":[{"MetricIntervalLowerBound":0,"ScalingAdjustment":1}]}'
        $putOut = Invoke-AwsJson @("application-autoscaling", "put-scaling-policy",
            "--service-namespace", $ns, "--resource-id", $resourceId, "--scalable-dimension", $dim,
            "--policy-name", $scaleOutPolicyName, "--policy-type", "StepScaling",
            "--step-scaling-policy-configuration", $stepOut,
            "--region", $region, "--output", "json")
        $policyOutArn = $putOut.PolicyARN

        $stepIn = '{"AdjustmentType":"ChangeInCapacity","MetricAggregationType":"Average","Cooldown":' + $script:AiScaleInCooldown + ',"StepAdjustments":[{"MetricIntervalUpperBound":0,"ScalingAdjustment":-1}]}'
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
            "--region", $region) -ErrorMessage "put-metric-alarm ai scale-out" | Out-Null
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
            "--region", $region) -ErrorMessage "put-metric-alarm ai scale-in" | Out-Null

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
        Invoke-Aws @("autoscaling", "update-auto-scaling-group",
            "--auto-scaling-group-name", $script:AiASGName,
            "--min-size", $script:AiMinSize.ToString(),
            "--max-size", $script:AiMaxSize.ToString(),
            "--desired-capacity", $clampedDesired.ToString(),
            "--new-instances-protected-from-scale-in",
            "--region", $script:Region) -ErrorMessage "update-auto-scaling-group failed" | Out-Null
        Write-Ok "ASG $($script:AiASGName) min=$($script:AiMinSize) max=$($script:AiMaxSize) desired(clamp)=$clampedDesired protection=ON"
        $script:ChangesMade = $true
    }
    if ($script:AiScaleInProtection -and -not $asg.NewInstancesProtectedFromScaleIn) {
        Invoke-Aws @("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", $script:AiASGName, "--new-instances-protected-from-scale-in", "--region", $script:Region) -ErrorMessage "set scale-in protection" | Out-Null
        Write-Ok "ASG $($script:AiASGName) scale-in protection enabled"
        $script:ChangesMade = $true
    }

    if ($ltResult.Updated) {
        Invoke-Aws @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $script:AiASGName, "--region", $script:Region) -ErrorMessage "start-instance-refresh failed" | Out-Null
        Write-Ok "ASG $($script:AiASGName) instance-refresh started (LT drift)"
        $script:ChangesMade = $true
    }

    if ($script:AiSqsQueueUrl -or $script:AiSqsQueueName) { Ensure-AiSqsScaling }

    if (-not $capacityDrift -and -not $ltResult.Updated) {
        Write-Ok "ASG $($script:AiASGName) idempotent Desired(clamp)=$clampedDesired Min=$($asg.MinSize) Max=$($asg.MaxSize)"
    }
}
