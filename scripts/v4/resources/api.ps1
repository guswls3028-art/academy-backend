# API: EIP instance + /health. Ensure-API: create/recreate to SSOT; health/AMI/SSM checks.
function Get-APIInstanceByEIP {
    $addr = Invoke-AwsJson @("ec2", "describe-addresses", "--allocation-ids", $script:ApiAllocationId, "--region", $script:Region, "--output", "json")
    if (-not $addr -or -not $addr.Addresses -or $addr.Addresses.Count -eq 0 -or -not $addr.Addresses[0].InstanceId) {
        return $null
    }
    return $addr.Addresses[0].InstanceId
}

function Get-APIInstanceByTag {
    $res = Invoke-AwsJson @("ec2", "describe-instances",
        "--filters", "Name=tag:$($script:ApiInstanceTagKey),Values=$($script:ApiInstanceTagValue)", "Name=instance-state-name,Values=running,pending,stopped",
        "--region", $script:Region, "--output", "json")
    $inst = $null
    if ($res -and $res.Reservations -and $res.Reservations.Count -gt 0) {
        $inst = $res.Reservations[0].Instances | Select-Object -First 1
    }
    return $inst
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

function New-APIInstanceAndAttachEIP {
    # Run instance with SSOT AMI, profile, subnet, SG; tag Name=academy-api; then associate EIP.
    $runArgs = @("ec2", "run-instances",
        "--image-id", $script:ApiAmiId,
        "--instance-type", $script:ApiInstanceType,
        "--iam-instance-profile", "Name=$($script:ApiInstanceProfile)",
        "--subnet-id", $script:ApiSubnetId,
        "--security-group-ids", $script:ApiSecurityGroupId,
        "--tag-specifications", "ResourceType=instance,Tags=[{Key=Name,Value=$($script:ApiInstanceTagValue)}]",
        "--region", $script:Region, "--output", "json")
    $run = Invoke-AwsJson $runArgs
    if (-not $run -or -not $run.Instances -or $run.Instances.Count -eq 0) { throw "run-instances returned no instance" }
    $newId = $run.Instances[0].InstanceId
    Write-Ok "Created API instance $newId"
    $script:ChangesMade = $true

    Wait-InstanceRunning -InstanceId $newId -Reg $script:Region -TimeoutSec 300
    Invoke-Aws @("ec2", "associate-address", "--instance-id", $newId, "--allocation-id", $script:ApiAllocationId, "--region", $script:Region) -ErrorMessage "associate-address failed" | Out-Null
    Write-Ok "EIP associated to $newId"
    Wait-SSMOnline -InstanceId $newId -Reg $script:Region -TimeoutSec 300
    Wait-ApiHealth200 -ApiBaseUrl $script:ApiBaseUrl -TimeoutSec 300
    return $newId
}

function Remove-APIInstance {
    param([string]$InstanceId)
    Invoke-Aws @("ec2", "terminate-instances", "--instance-ids", $InstanceId, "--region", $script:Region) -ErrorMessage "terminate-instances failed" | Out-Null
    Write-Ok "Terminated API instance $InstanceId"
    $script:ChangesMade = $true
    Wait-InstanceTerminated -InstanceId $InstanceId -Reg $script:Region -TimeoutSec 300
}

function Confirm-APIHealth {
    Write-Step "API health"
    if ($script:PlanMode) { Write-Ok "API check skipped (Plan)"; return }
    try {
        $r = Invoke-WebRequest -Uri "$($script:ApiBaseUrl)/health" -UseBasicParsing -TimeoutSec 10
        if ($r.StatusCode -eq 200) {
            Write-Ok "GET $($script:ApiBaseUrl)/health -> 200"
        } else {
            Write-Fail "API health returned $($r.StatusCode); expected 200. Infra alignment failure."
            throw "API health check failed: status=$($r.StatusCode)"
        }
    } catch {
        Write-Fail "API health check failed: $_"
        throw "API health check failed: $_"
    }
}

function Ensure-API {
    Write-Step "Ensure API ($($script:ApiInstanceTagValue))"
    if ($script:PlanMode) { Write-Ok "Ensure-API skipped (Plan)"; return }

    # Describe: find instance by tag
    $inst = Get-APIInstanceByTag
    $needCreate = $false
    $needRecreate = $false
    $reason = ""

    if (-not $inst) {
        $needCreate = $true
        $reason = "no instance with tag $($script:ApiInstanceTagKey)=$($script:ApiInstanceTagValue)"
    } else {
        $instId = $inst.InstanceId
        # AMI drift
        if ($inst.ImageId -ne $script:ApiAmiId) {
            $needRecreate = $true
            $reason = "AMI drift: current=$($inst.ImageId) SSOT=$($script:ApiAmiId)"
        }
        # SSM offline
        if (-not $needRecreate -and -not (Test-APIInstanceSSMOnline -InstanceId $instId)) {
            $needRecreate = $true
            $reason = "SSM agent not online"
        }
        # Health != 200
        if (-not $needRecreate -and -not (Test-APIHealth200)) {
            $needRecreate = $true
            $reason = "health != 200"
        }
    }

    if ($needCreate) {
        Write-Host "  Decision: create ($reason)" -ForegroundColor Yellow
        New-APIInstanceAndAttachEIP | Out-Null
        Write-Ok "Ensure-API complete (created)"
        return
    }

    if ($needRecreate) {
        Write-Host "  Decision: recreate ($reason)" -ForegroundColor Yellow
        Remove-APIInstance -InstanceId $inst.InstanceId
        New-APIInstanceAndAttachEIP | Out-Null
        Write-Ok "Ensure-API complete (recreated)"
        return
    }

    Write-Ok "Ensure-API idempotent (instance $($inst.InstanceId) AMI=$($inst.ImageId) health=200)"
}
