# API: ASG(1) + EIP + /health. Ensure-API-LaunchTemplate, Ensure-API-ASG, Ensure-API-Instance.
$ErrorActionPreference = "Stop"

function Get-APIInstanceByEIP {
    $addr = Invoke-AwsJson @("ec2", "describe-addresses", "--allocation-ids", $script:ApiAllocationId, "--region", $script:Region, "--output", "json")
    if (-not $addr -or -not $addr.Addresses -or $addr.Addresses.Count -eq 0 -or -not $addr.Addresses[0].InstanceId) {
        return $null
    }
    return $addr.Addresses[0].InstanceId
}

function Get-APIASGInstanceIds {
    $r = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $script:ApiASGName, "--region", $script:Region, "--output", "json")
    if (-not $r -or -not $r.AutoScalingGroups -or $r.AutoScalingGroups.Count -eq 0) { return @() }
    $instances = $r.AutoScalingGroups[0].Instances | Where-Object { $_.LifecycleState -eq "InService" -or $_.LifecycleState -eq "Pending" } | ForEach-Object { $_.InstanceId }
    return @($instances)
}

function Test-APIHealth200 {
    try {
        $r = Invoke-WebRequest -Uri "$($script:ApiBaseUrl)/health" -UseBasicParsing -TimeoutSec 10
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Test-APIInstanceSSMOnline {
    param([string]$InstanceId)
    $r = Invoke-AwsJson @("ssm", "describe-instance-information", "--filters", "Key=InstanceIds,Values=$InstanceId", "--region", $script:Region, "--output", "json")
    return ($r -and $r.InstanceInformationList -and $r.InstanceInformationList.Count -gt 0)
}

# Ensure API Launch Template: drift on AMI, SG, UserData, InstanceProfile → new version.
function Ensure-API-LaunchTemplate {
    if ($script:PlanMode) { return @{ LtId = $null; Updated = $false } }
    $ltName = $script:ApiLaunchTemplateName
    $r = Invoke-AwsJson @("ec2", "describe-launch-templates", "--launch-template-names", $ltName, "--region", $script:Region, "--output", "json")
    $currentAmi = $script:ApiAmiId
    $currentType = $script:ApiInstanceType
    $currentSg = $script:ApiSecurityGroupId
    $currentProfile = $script:ApiInstanceProfile
    $currentUserData = if ($script:ApiUserData) { [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($script:ApiUserData)) } else { "" }

    $tagSpec = "TagSpecifications=[{ResourceType=instance,Tags=[{Key=$($script:ApiInstanceTagKey),Value=$($script:ApiInstanceTagValue)}]}]"
    $baseData = "ImageId=$currentAmi,InstanceType=$currentType,SecurityGroupIds=$currentSg,IamInstanceProfile=Name=$currentProfile,$tagSpec"
    if ($currentUserData) { $baseData += ",UserData=$currentUserData" }

    if (-not $r -or -not $r.LaunchTemplates -or $r.LaunchTemplates.Count -eq 0) {
        $create = Invoke-AwsJson @("ec2", "create-launch-template", "--launch-template-name", $ltName, "--version-description", "SSOT v4", "--launch-template-data", $baseData, "--region", $script:Region, "--output", "json")
        if (-not $create -or -not $create.LaunchTemplate) { throw "create-launch-template failed for $ltName" }
        Write-Ok "LaunchTemplate $ltName created"
        $script:ChangesMade = $true
        return @{ LtId = $create.LaunchTemplate.LaunchTemplateId; Updated = $true }
    }

    $ltId = $r.LaunchTemplates[0].LaunchTemplateId
    $verR = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $ltId, "--versions", '$Default', "--region", $script:Region, "--output", "json")
    if (-not $verR -or -not $verR.LaunchTemplateVersions -or $verR.LaunchTemplateVersions.Count -eq 0) { return @{ LtId = $ltId; Updated = $false } }
    $data = $verR.LaunchTemplateVersions[0].LaunchTemplateData
    $actualAmi = if ($data.PSObject.Properties['ImageId']) { $data.ImageId } else { $null }
    $actualType = if ($data.PSObject.Properties['InstanceType']) { $data.InstanceType } else { $null }
    $actualSg = $null
    if ($data.PSObject.Properties['SecurityGroupIds'] -and $data.SecurityGroupIds -and $data.SecurityGroupIds.Count -gt 0) { $actualSg = $data.SecurityGroupIds[0] }
    elseif ($data.PSObject.Properties['SecurityGroupIds']) { $actualSg = $data.SecurityGroupIds }
    $actualProfile = $null
    if ($data.PSObject.Properties['IamInstanceProfile'] -and $data.IamInstanceProfile) { $actualProfile = $data.IamInstanceProfile.Name }
    $actualUserData = if ($data.PSObject.Properties['UserData']) { $data.UserData } else { "" }

    $drift = ($actualAmi -ne $currentAmi) -or ($actualType -ne $currentType) -or ($actualSg -ne $currentSg) -or ($actualProfile -ne $currentProfile) -or ($actualUserData -ne $currentUserData)
    if (-not $drift) { return @{ LtId = $ltId; Updated = $false } }

    $newVer = Invoke-AwsJson @("ec2", "create-launch-template-version", "--launch-template-id", $ltId, "--version-description", "SSOT v4 drift", "--launch-template-data", $baseData, "--region", $script:Region, "--output", "json")
    if (-not $newVer -or -not $newVer.LaunchTemplateVersion) { throw "create-launch-template-version failed" }
    $newVersion = $newVer.LaunchTemplateVersion.VersionNumber
    Invoke-Aws @("ec2", "modify-launch-template", "--launch-template-id", $ltId, "--default-version", $newVersion.ToString(), "--region", $script:Region) -ErrorMessage "modify-launch-template set default failed" | Out-Null
    Write-Ok "LaunchTemplate $ltName new default version $newVersion (drift)"
    $script:ChangesMade = $true
    return @{ LtId = $ltId; Updated = $true }
}

# Ensure API ASG: create if missing; capacity drift → update; LT updated → instance-refresh.
function Ensure-API-ASG {
    Write-Step "Ensure API ASG $($script:ApiASGName)"
    if ($script:PlanMode) { Write-Ok "Ensure-API-ASG skipped (Plan)"; return }

    $ltResult = Ensure-API-LaunchTemplate
    $vpcZone = ($script:PublicSubnets -join ",")
    if (-not $vpcZone) { throw "PublicSubnets empty" }

    $asgList = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $script:Region, "--output", "json")
    $asgArr = if ($asgList -and $asgList.PSObject.Properties['AutoScalingGroups']) { @($asgList.AutoScalingGroups) } else { @() }
    $asg = $asgArr | Where-Object { $_.AutoScalingGroupName -eq $script:ApiASGName } | Select-Object -First 1

    if (-not $asg) {
        $ltSpec = "LaunchTemplateId=$($ltResult.LtId),Version=`$Latest"
        Invoke-Aws @("autoscaling", "create-auto-scaling-group", "--auto-scaling-group-name", $script:ApiASGName, "--launch-template", $ltSpec, "--min-size", $script:ApiASGMinSize.ToString(), "--max-size", $script:ApiASGMaxSize.ToString(), "--desired-capacity", $script:ApiASGDesiredCapacity.ToString(), "--vpc-zone-identifier", $vpcZone, "--region", $script:Region) -ErrorMessage "create-auto-scaling-group API ASG failed" | Out-Null
        Write-Ok "ASG $($script:ApiASGName) created"
        $script:ChangesMade = $true
        return
    }

    $capacityDrift = ($asg.MinSize -ne $script:ApiASGMinSize) -or ($asg.MaxSize -ne $script:ApiASGMaxSize) -or ($asg.DesiredCapacity -ne $script:ApiASGDesiredCapacity)
    if ($capacityDrift) {
        Invoke-Aws @("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", $script:ApiASGName, "--min-size", $script:ApiASGMinSize.ToString(), "--max-size", $script:ApiASGMaxSize.ToString(), "--desired-capacity", $script:ApiASGDesiredCapacity.ToString(), "--region", $script:Region) -ErrorMessage "update-auto-scaling-group API ASG failed" | Out-Null
        Write-Ok "ASG $($script:ApiASGName) capacity updated"
        $script:ChangesMade = $true
    }
    if ($ltResult.Updated) {
        Invoke-Aws @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $script:ApiASGName, "--region", $script:Region) -ErrorMessage "start-instance-refresh API ASG failed" | Out-Null
        Write-Ok "ASG $($script:ApiASGName) instance-refresh started"
        $script:ChangesMade = $true
    }
    if (-not $capacityDrift -and -not $ltResult.Updated) {
        Write-Ok "ASG $($script:ApiASGName) idempotent"
    }
}

# EIP reassociate → SSM → health 200; on failure start-instance-refresh.
function Ensure-API-Instance {
    Write-Step "Ensure API Instance (EIP + SSM + health)"
    if ($script:PlanMode) { Write-Ok "Ensure-API-Instance skipped (Plan)"; return }

    $maxWait = 300
    $elapsed = 0
    $instanceId = $null
    while ($elapsed -lt $maxWait) {
        $ids = Get-APIASGInstanceIds
        if ($ids -and $ids.Count -gt 0) {
            $currentEip = Get-APIInstanceByEIP
            if ($currentEip -and $ids -contains $currentEip) {
                if (Test-APIHealth200) {
                    Write-Ok "EIP already on ASG instance $currentEip, health 200"
                    return
                }
                $instanceId = $currentEip
                break
            }
            $instanceId = $ids[0]
            break
        }
        Write-Host "  Waiting for API ASG instance..." -ForegroundColor Gray
        Start-Sleep -Seconds 15
        $elapsed += 15
    }
    if (-not $instanceId) { throw "No API ASG instance after ${maxWait}s" }

    Invoke-Aws @("ec2", "associate-address", "--instance-id", $instanceId, "--allocation-id", $script:ApiAllocationId, "--allow-reassociation", "--region", $script:Region) -ErrorMessage "associate-address failed" | Out-Null
    Write-Ok "EIP associated to $instanceId"
    $script:ChangesMade = $true

    Wait-SSMOnline -InstanceId $instanceId -Reg $script:Region -TimeoutSec 300
    try {
        Wait-ApiHealth200 -ApiBaseUrl $script:ApiBaseUrl -TimeoutSec 300
    } catch {
        Write-Warn "API health 200 timeout; starting instance-refresh"
        Invoke-Aws @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $script:ApiASGName, "--region", $script:Region) -ErrorMessage "start-instance-refresh failed" | Out-Null
        $script:ChangesMade = $true
        throw "API health check failed; instance-refresh started. Re-run deploy."
    }
}

function Confirm-APIHealth {
    Write-Step "API health"
    if ($script:PlanMode) { Write-Ok "API check skipped (Plan)"; return }
    try {
        $r = Invoke-WebRequest -Uri "$($script:ApiBaseUrl)/health" -UseBasicParsing -TimeoutSec 10
        if ($r.StatusCode -eq 200) { Write-Ok "GET $($script:ApiBaseUrl)/health -> 200" } else { throw "status=$($r.StatusCode)" }
    } catch {
        Write-Fail "API health check failed: $_"
        throw "API health check failed: $_"
    }
}

function Ensure-API {
    Ensure-API-ASG
    Ensure-API-Instance
}
