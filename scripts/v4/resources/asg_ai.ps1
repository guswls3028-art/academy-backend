# ASG AI: Ensure academy-ai-worker-asg exists; LT + capacity drift → update / instance-refresh.
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
    # Returns: @{ LtId = $id; LtVersion = $ver; Updated = $true/$false }
    $lt = Get-AiLaunchTemplate
    if (-not $lt) {
        $data = "ImageId=$($script:AiAmiId),InstanceType=$($script:AiInstanceType)"
        $create = Invoke-AwsJson @("ec2", "create-launch-template",
            "--launch-template-name", $script:AiLaunchTemplateName,
            "--version-description", "SSOT v4",
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
    if ($currentAmi -ne $script:AiAmiId -or $currentType -ne $script:AiInstanceType) {
        $data = "ImageId=$($script:AiAmiId),InstanceType=$($script:AiInstanceType)"
        $newVer = Invoke-AwsJson @("ec2", "create-launch-template-version",
            "--launch-template-id", $ltId,
            "--version-description", "SSOT v4 drift",
            "--launch-template-data", $data,
            "--region", $script:Region, "--output", "json")
        if (-not $newVer -or -not $newVer.LaunchTemplateVersion) { throw "create-launch-template-version failed" }
        $newVersion = $newVer.LaunchTemplateVersion.VersionNumber
        Invoke-Aws @("ec2", "modify-launch-template", "--launch-template-id", $ltId, "--default-version", $newVersion.ToString(), "--region", $script:Region) -ErrorMessage "modify-launch-template set default failed" | Out-Null
        Write-Ok "LaunchTemplate $($script:AiLaunchTemplateName) new default version $newVersion (AMI/type drift)"
        $script:ChangesMade = $true
        return @{ LtId = $ltId; LtVersion = $newVersion; Updated = $true }
    }
    return @{ LtId = $ltId; LtVersion = $defVer.VersionNumber; Updated = $false }
}

function Ensure-ASGAi {
    Write-Step "Ensure ASG $($script:AiASGName)"
    if ($script:PlanMode) { Write-Ok "ASG AI check skipped (Plan)"; return }

    $ltResult = Ensure-AiLaunchTemplate
    $vpcZone = ($script:PublicSubnets -join ",")
    if (-not $vpcZone) { throw "PublicSubnets empty; cannot create ASG" }

    $asgList = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $script:Region, "--output", "json")
    $asgArr = if ($asgList -and $asgList.PSObject.Properties['AutoScalingGroups']) { @($asgList.AutoScalingGroups) } else { @() }
    $asg = $asgArr | Where-Object { $_.AutoScalingGroupName -eq $script:AiASGName } | Select-Object -First 1

    if (-not $asg) {
        $ltSpec = "LaunchTemplateId=$($ltResult.LtId),Version='`$Latest'"
        Invoke-Aws @("autoscaling", "create-auto-scaling-group",
            "--auto-scaling-group-name", $script:AiASGName,
            "--launch-template", $ltSpec,
            "--min-size", $script:AiMinSize.ToString(),
            "--max-size", $script:AiMaxSize.ToString(),
            "--desired-capacity", $script:AiDesiredCapacity.ToString(),
            "--vpc-zone-identifier", $vpcZone,
            "--region", $script:Region) -ErrorMessage "create-auto-scaling-group failed" | Out-Null
        Write-Ok "ASG $($script:AiASGName) created"
        $script:ChangesMade = $true
        return
    }

    $capacityDrift = ($asg.MinSize -ne $script:AiMinSize) -or ($asg.MaxSize -ne $script:AiMaxSize) -or ($asg.DesiredCapacity -ne $script:AiDesiredCapacity)
    if ($capacityDrift) {
        Invoke-Aws @("autoscaling", "update-auto-scaling-group",
            "--auto-scaling-group-name", $script:AiASGName,
            "--min-size", $script:AiMinSize.ToString(),
            "--max-size", $script:AiMaxSize.ToString(),
            "--desired-capacity", $script:AiDesiredCapacity.ToString(),
            "--region", $script:Region) -ErrorMessage "update-auto-scaling-group failed" | Out-Null
        Write-Ok "ASG $($script:AiASGName) capacity updated to Min=$($script:AiMinSize) Max=$($script:AiMaxSize) Desired=$($script:AiDesiredCapacity)"
        $script:ChangesMade = $true
    }

    if ($ltResult.Updated) {
        Invoke-Aws @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $script:AiASGName, "--region", $script:Region) -ErrorMessage "start-instance-refresh failed" | Out-Null
        Write-Ok "ASG $($script:AiASGName) instance-refresh started (LT drift)"
        $script:ChangesMade = $true
    }

    if (-not $capacityDrift -and -not $ltResult.Updated) {
        Write-Ok "ASG $($script:AiASGName) idempotent Desired=$($asg.DesiredCapacity) Min=$($asg.MinSize) Max=$($asg.MaxSize)"
    }
}
