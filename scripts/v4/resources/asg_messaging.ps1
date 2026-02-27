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
    # Returns: @{ LtId = $id; LtVersion = $ver; Updated = $true/$false }
    $lt = Get-MessagingLaunchTemplate
    if (-not $lt) {
        $data = "ImageId=$($script:MessagingAmiId),InstanceType=$($script:MessagingInstanceType)"
        $create = Invoke-AwsJson @("ec2", "create-launch-template",
            "--launch-template-name", $script:MessagingLaunchTemplateName,
            "--version-description", "SSOT v4",
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
    if ($currentAmi -ne $script:MessagingAmiId -or $currentType -ne $script:MessagingInstanceType) {
        $data = "ImageId=$($script:MessagingAmiId),InstanceType=$($script:MessagingInstanceType)"
        $newVer = Invoke-AwsJson @("ec2", "create-launch-template-version",
            "--launch-template-id", $ltId,
            "--version-description", "SSOT v4 drift",
            "--launch-template-data", $data,
            "--region", $script:Region, "--output", "json")
        if (-not $newVer -or -not $newVer.LaunchTemplateVersion) { throw "create-launch-template-version failed" }
        $newVersion = $newVer.LaunchTemplateVersion.VersionNumber
        Invoke-Aws @("ec2", "modify-launch-template", "--launch-template-id", $ltId, "--default-version", "'`$Version=$newVersion'", "--region", $script:Region) -ErrorMessage "modify-launch-template set default failed" | Out-Null
        Write-Ok "LaunchTemplate $($script:MessagingLaunchTemplateName) new default version $newVersion (AMI/type drift)"
        $script:ChangesMade = $true
        return @{ LtId = $ltId; LtVersion = $newVersion; Updated = $true }
    }
    return @{ LtId = $ltId; LtVersion = $defVer.VersionNumber; Updated = $false }
}

function Ensure-ASGMessaging {
    Write-Step "Ensure ASG $($script:MessagingASGName)"
    if ($script:PlanMode) { Write-Ok "ASG Messaging check skipped (Plan)"; return }

    $ltResult = Ensure-MessagingLaunchTemplate
    $vpcZone = ($script:PublicSubnets -join ",")
    if (-not $vpcZone) { throw "PublicSubnets empty; cannot create ASG" }

    $asgList = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $script:Region, "--output", "json")
    $asgArr = if ($asgList -and $asgList.PSObject.Properties['AutoScalingGroups']) { @($asgList.AutoScalingGroups) } else { @() }
    $asg = $asgArr | Where-Object { $_.AutoScalingGroupName -eq $script:MessagingASGName } | Select-Object -First 1

    if (-not $asg) {
        $ltSpec = "LaunchTemplateId=$($ltResult.LtId),Version='`$Latest'"
        Invoke-Aws @("autoscaling", "create-auto-scaling-group",
            "--auto-scaling-group-name", $script:MessagingASGName,
            "--launch-template", $ltSpec,
            "--min-size", $script:MessagingMinSize.ToString(),
            "--max-size", $script:MessagingMaxSize.ToString(),
            "--desired-capacity", $script:MessagingDesiredCapacity.ToString(),
            "--vpc-zone-identifier", $vpcZone,
            "--region", $script:Region) -ErrorMessage "create-auto-scaling-group failed" | Out-Null
        Write-Ok "ASG $($script:MessagingASGName) created"
        $script:ChangesMade = $true
        return
    }

    $capacityDrift = ($asg.MinSize -ne $script:MessagingMinSize) -or ($asg.MaxSize -ne $script:MessagingMaxSize) -or ($asg.DesiredCapacity -ne $script:MessagingDesiredCapacity)
    if ($capacityDrift) {
        Invoke-Aws @("autoscaling", "update-auto-scaling-group",
            "--auto-scaling-group-name", $script:MessagingASGName,
            "--min-size", $script:MessagingMinSize.ToString(),
            "--max-size", $script:MessagingMaxSize.ToString(),
            "--desired-capacity", $script:MessagingDesiredCapacity.ToString(),
            "--region", $script:Region) -ErrorMessage "update-auto-scaling-group failed" | Out-Null
        Write-Ok "ASG $($script:MessagingASGName) capacity updated to Min=$($script:MessagingMinSize) Max=$($script:MessagingMaxSize) Desired=$($script:MessagingDesiredCapacity)"
        $script:ChangesMade = $true
    }

    if ($ltResult.Updated) {
        Invoke-Aws @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $script:MessagingASGName, "--region", $script:Region) -ErrorMessage "start-instance-refresh failed" | Out-Null
        Write-Ok "ASG $($script:MessagingASGName) instance-refresh started (LT drift)"
        $script:ChangesMade = $true
    }

    if (-not $capacityDrift -and -not $ltResult.Updated) {
        Write-Ok "ASG $($script:MessagingASGName) idempotent Desired=$($asg.DesiredCapacity) Min=$($asg.MinSize) Max=$($asg.MaxSize)"
    }
}
