# Build: Tag Name=academy-build-arm64. Ensure-Build: create if missing; AMI drift → recreate. Stopped allowed.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"

function Get-BuildInstanceByTag {
    $res = Invoke-AwsJson @("ec2", "describe-instances",
        "--filters", "Name=tag:$($script:BuildTagKey),Values=$($script:BuildTagValue)", "Name=instance-state-name,Values=running,pending,stopped",
        "--region", $script:Region, "--output", "json")
    $inst = $null
    if ($res -and $res.Reservations -and $res.Reservations.Count -gt 0) {
        $inst = $res.Reservations[0].Instances | Select-Object -First 1
    }
    return $inst
}

function New-BuildInstance {
    # Spot 인스턴스로 빌드 서버 기동 (비용 절감). 실패 시 온디맨드 폴백.
    $baseArgs = @(
        "ec2", "run-instances",
        "--image-id", $script:BuildAmiId,
        "--instance-type", $script:BuildInstanceType,
        "--iam-instance-profile", "Name=$($script:BuildInstanceProfile)",
        "--subnet-id", $script:BuildSubnetId,
        "--security-group-ids", $script:BuildSecurityGroupId,
        "--tag-specifications", "ResourceType=instance,Tags=[{Key=Name,Value=$($script:BuildTagValue)}]",
        "--region", $script:Region, "--output", "json"
    )
    $spotArgs = $baseArgs + @("--instance-market-options", "MarketType=spot,SpotOptions={SpotInstanceType=one-time,InstanceInterruptionBehavior=terminate}")
    $run = Invoke-AwsJson $spotArgs
    if (-not $run -or -not $run.Instances -or $run.Instances.Count -eq 0) {
        Write-Host "  Spot capacity not available; falling back to On-Demand" -ForegroundColor Yellow
        $run = Invoke-AwsJson $baseArgs
    }
    if (-not $run -or -not $run.Instances -or $run.Instances.Count -eq 0) { throw "run-instances returned no instance for build" }
    $newId = $run.Instances[0].InstanceId
    Write-Ok "Created Build instance $newId"
    $script:ChangesMade = $true
    return $newId
}

function Remove-BuildInstance {
    param([string]$InstanceId)
    Invoke-Aws @("ec2", "terminate-instances", "--instance-ids", $InstanceId, "--region", $script:Region) -ErrorMessage "terminate-instances failed (build)" | Out-Null
    Write-Ok "Terminated Build instance $InstanceId"
    $script:ChangesMade = $true
    Wait-InstanceTerminated -InstanceId $InstanceId -Reg $script:Region -TimeoutSec 300
}

function Confirm-BuildInstance {
    Write-Step "Build ($($script:BuildTagKey)=$($script:BuildTagValue))"
    if ($script:PlanMode) { Write-Ok "Build check skipped (Plan)"; return }
    $inst = Get-BuildInstanceByTag
    if (-not $inst) {
        Write-Warn "Build instance ($($script:BuildTagValue)) not found"
        return
    }
    Write-Ok "Build InstanceId=$($inst.InstanceId) State=$($inst.State.Name)"
}

function Ensure-Build {
    Write-Step "Ensure Build ($($script:BuildTagValue))"
    if ($script:PlanMode) { Write-Ok "Ensure-Build skipped (Plan)"; return }

    # Ensure-Network 이후 채워진 서브넷/SG 사용 (params는 비어 있을 수 있음)
    if (-not $script:BuildSubnetId -and $script:PrivateSubnets -and $script:PrivateSubnets.Count -gt 0) { $script:BuildSubnetId = $script:PrivateSubnets[0] }
    if (-not $script:BuildSubnetId -and $script:PublicSubnets -and $script:PublicSubnets.Count -gt 0) { $script:BuildSubnetId = $script:PublicSubnets[0] }
    if (-not $script:BuildSecurityGroupId) { $script:BuildSecurityGroupId = $script:SecurityGroupApp }
    if (-not $script:BuildSecurityGroupId) { $script:BuildSecurityGroupId = $script:BatchSecurityGroupId }
    if (-not $script:BuildSubnetId) { throw "Build subnet not set. Ensure-Network must run first and provide PrivateSubnets or PublicSubnets." }
    if (-not $script:BuildSecurityGroupId) { throw "Build security group not set. Ensure-Network must provide sg-app or sg-batch." }

    # Describe: find instance by tag
    $inst = Get-BuildInstanceByTag
    $needCreate = $false
    $needRecreate = $false
    $reason = ""

    if (-not $inst) {
        $needCreate = $true
        $reason = "no instance with tag $($script:BuildTagKey)=$($script:BuildTagValue)"
    } else {
        if ($inst.ImageId -ne $script:BuildAmiId) {
            $needRecreate = $true
            $reason = "AMI drift: current=$($inst.ImageId) SSOT=$($script:BuildAmiId)"
        }
    }

    if ($needCreate) {
        Write-Host "  Decision: create ($reason)" -ForegroundColor Yellow
        New-BuildInstance | Out-Null
        Write-Ok "Ensure-Build complete (created)"
        return
    }

    if ($needRecreate) {
        Write-Host "  Decision: recreate ($reason)" -ForegroundColor Yellow
        Remove-BuildInstance -InstanceId $inst.InstanceId
        New-BuildInstance | Out-Null
        Write-Ok "Ensure-Build complete (recreated)"
        return
    }

    Write-Ok "Ensure-Build idempotent (instance $($inst.InstanceId) State=$($inst.State.Name) AMI=$($inst.ImageId))"
}
