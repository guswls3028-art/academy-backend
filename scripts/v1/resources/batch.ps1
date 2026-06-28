# Batch: Video/Ops CE and Queue Ensure. Uses v1/templates/batch. INVALID -> delete+wait+recreate+wait.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"
$V4Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BatchPath = Join-Path $V4Root "templates\batch"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

# Ensure EC2 Launch Template for Batch CE (root volume size + ARM64 settings).
# Returns launch template name. Idempotent: creates if not exists, updates if root volume size changed.
function Ensure-BatchLaunchTemplate { param([string]$TemplateName, [int]$RootVolumeSizeGb, [string]$Region)
    $existing = Invoke-AwsJson @("ec2", "describe-launch-templates", "--filters", "Name=launch-template-name,Values=$TemplateName", "--region", $Region, "--output", "json") 2>$null
    $ltData = [ordered]@{
        BlockDeviceMappings = @(
            [ordered]@{
                DeviceName = "/dev/xvda"
                Ebs = [ordered]@{ VolumeSize = $RootVolumeSizeGb; VolumeType = "gp3"; DeleteOnTermination = $true }
            }
        )
    }
    $ltJson = $ltData | ConvertTo-Json -Depth 10 -Compress
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $ltJson, $utf8NoBom)
    try {
        if (-not $existing -or -not $existing.LaunchTemplates -or $existing.LaunchTemplates.Count -eq 0) {
            Write-Host "  Creating EC2 Launch Template: $TemplateName (root=${RootVolumeSizeGb}GB)" -ForegroundColor Yellow
            Invoke-Aws @("ec2", "create-launch-template", "--launch-template-name", $TemplateName, "--launch-template-data", "file://$($tmp -replace '\\','/')", "--region", $Region) -ErrorMessage "create launch template $TemplateName" | Out-Null
            $script:ChangesMade = $true
        } else {
            # Check current root volume size
            $ltv = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-name", $TemplateName, "--versions", "`$Latest", "--region", $Region, "--output", "json") 2>$null
            $currentSize = 0
            if ($ltv -and $ltv.LaunchTemplateVersions -and $ltv.LaunchTemplateVersions.Count -gt 0) {
                $bdm = $ltv.LaunchTemplateVersions[0].LaunchTemplateData.BlockDeviceMappings
                if ($bdm -and $bdm.Count -gt 0) { $currentSize = [int]$bdm[0].Ebs.VolumeSize }
            }
            if ($currentSize -ne $RootVolumeSizeGb) {
                Write-Host "  Updating Launch Template: $TemplateName (current=${currentSize}GB -> desired=${RootVolumeSizeGb}GB)" -ForegroundColor Yellow
                Invoke-Aws @("ec2", "create-launch-template-version", "--launch-template-name", $TemplateName, "--launch-template-data", "file://$($tmp -replace '\\','/')", "--region", $Region) -ErrorMessage "create launch template version $TemplateName" | Out-Null
                $script:ChangesMade = $true
            } else {
                Write-Ok "Launch Template $TemplateName root=${currentSize}GB (no change)"
            }
        }
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    return $TemplateName
}

function Get-CEArn { param([string]$Name)
    $r = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $Name, "--region", $script:Region, "--output", "json")
    if (-not $r -or -not $r.computeEnvironments -or $r.computeEnvironments.Count -eq 0) { return $null }
    return $r.computeEnvironments[0].computeEnvironmentArn
}

function Get-VideoCEComputeResourceType {
    if ($script:VideoUseSpot) { return "SPOT" }
    return "EC2"
}

function Get-BatchActiveJobSummaries {
    param([string]$QueueName)
    $active = [System.Collections.ArrayList]::new()
    foreach ($status in @("SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING")) {
        $jobs = Invoke-AwsJson @("batch", "list-jobs", "--job-queue", $QueueName, "--job-status", $status, "--region", $script:Region, "--output", "json")
        if (-not $jobs -or -not $jobs.jobSummaryList) { continue }
        foreach ($job in $jobs.jobSummaryList) {
            [void]$active.Add([PSCustomObject]@{
                Status = $status
                JobId = $job.jobId
                JobName = $job.jobName
            })
        }
    }
    return @($active)
}

function Assert-NoActiveBatchJobs {
    param([string]$QueueName, [string]$Reason)
    $active = @(Get-BatchActiveJobSummaries -QueueName $QueueName)
    if ($active.Count -eq 0) { return }
    $sample = @($active | Select-Object -First 5 | ForEach-Object { "$($_.Status):$($_.JobName):$($_.JobId)" }) -join ", "
    throw "Refusing Batch CE rebuild for $Reason because $QueueName has $($active.Count) active job(s): $sample"
}

function Remove-BatchJobQueueIfExists {
    param([string]$QueueName)
    $qCheck = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $QueueName, "--region", $script:Region, "--output", "json")
    if (-not $qCheck -or -not $qCheck.jobQueues -or $qCheck.jobQueues.Count -eq 0) { return }
    Invoke-Aws @("batch", "update-job-queue", "--job-queue", $QueueName, "--state", "DISABLED", "--region", $script:Region) -ErrorMessage "disable Batch queue $QueueName" | Out-Null
    $wait = 0
    while ($wait -lt 120) {
        Start-Sleep -Seconds 5
        $wait += 5
        $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $QueueName, "--region", $script:Region, "--output", "json")
        if ($q -and $q.jobQueues -and $q.jobQueues[0].state -eq "DISABLED") { break }
    }
    Invoke-Aws @("batch", "delete-job-queue", "--job-queue", $QueueName, "--region", $script:Region) -ErrorMessage "delete Batch queue $QueueName" | Out-Null
    Wait-QueueDeleted -QueueName $QueueName -Reg $script:Region
}

function Remove-BatchComputeEnvironmentIfExists {
    param([string]$CEName)
    $ce = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $CEName, "--region", $script:Region, "--output", "json")
    if (-not $ce -or -not $ce.computeEnvironments -or $ce.computeEnvironments.Count -eq 0) { return }
    Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $CEName, "--state", "DISABLED", "--region", $script:Region) -ErrorMessage "disable Batch CE $CEName" | Out-Null
    $wait = 0
    while ($wait -lt 180) {
        Start-Sleep -Seconds 5
        $wait += 5
        $ce2 = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $CEName, "--region", $script:Region, "--output", "json")
        if ($ce2 -and $ce2.computeEnvironments -and $ce2.computeEnvironments[0].state -eq "DISABLED") { break }
    }
    $deleteRetries = 0
    while ($deleteRetries -lt 5) {
        try {
            Invoke-Aws @("batch", "delete-compute-environment", "--compute-environment", $CEName, "--region", $script:Region) -ErrorMessage "delete Batch CE $CEName" | Out-Null
            break
        } catch {
            if ($_.Exception.Message -match "resource is being modified" -and $deleteRetries -lt 4) {
                Write-Host "  Batch CE delete delayed (resource modifying); retry in 30s..." -ForegroundColor Yellow
                Start-Sleep -Seconds 30
                $deleteRetries++
            } else { throw }
        }
    }
    Wait-CEDeleted -CEName $CEName -Reg $script:Region
}

function Recreate-VideoCEAndQueue {
    param([string]$Reason)
    Assert-NoActiveBatchJobs -QueueName $script:VideoQueueName -Reason $Reason
    Write-Host "  Recreating Video CE and queue: $Reason" -ForegroundColor Yellow
    $script:ChangesMade = $true
    Remove-BatchJobQueueIfExists -QueueName $script:VideoQueueName
    Remove-BatchComputeEnvironmentIfExists -CEName $script:VideoCEName
    New-VideoCE
    Wait-CEValidEnabled -CEName $script:VideoCEName -Reg $script:Region
    $ceArn = Get-CEArn -Name $script:VideoCEName
    if (-not $ceArn) { throw "Video CE $($script:VideoCEName) was recreated but ARN was not found." }
    New-VideoQueue -CeArn $ceArn
    Write-Ok "Video CE and queue recreated"
}

function Update-VideoCEInPlace {
    param([string]$Reason, $CurrentComputeResources)
    Assert-NoActiveBatchJobs -QueueName $script:VideoQueueName -Reason $Reason
    Write-Host "  Updating Video CE in place: $Reason" -ForegroundColor Yellow
    $script:ChangesMade = $true

    $desiredTypes = @($script:VideoCEInstanceTypes | Where-Object { $_ })
    if (-not $desiredTypes -or $desiredTypes.Count -eq 0) { $desiredTypes = @($script:VideoCEInstanceType) }
    $subnets = if ($CurrentComputeResources -and $CurrentComputeResources.subnets) { @($CurrentComputeResources.subnets | Where-Object { $_ }) } else { @($script:PrivateSubnets | Where-Object { $_ }) }
    if (-not $subnets -or $subnets.Count -eq 0) { $subnets = @($script:PublicSubnets | Where-Object { $_ }) }
    $securityGroups = if ($CurrentComputeResources -and $CurrentComputeResources.securityGroupIds) { @($CurrentComputeResources.securityGroupIds | Where-Object { $_ }) } else { @($script:BatchSecurityGroupId) }
    $instanceRole = if ($CurrentComputeResources -and $CurrentComputeResources.instanceRole) { "$($CurrentComputeResources.instanceRole)" } else { "$($script:BatchIam.InstanceProfileArn)" }
    $launchTemplate = [ordered]@{ version = '$Latest' }
    if ($CurrentComputeResources -and $CurrentComputeResources.launchTemplate -and $CurrentComputeResources.launchTemplate.launchTemplateId) {
        $launchTemplate.launchTemplateId = "$($CurrentComputeResources.launchTemplate.launchTemplateId)"
    } elseif ($CurrentComputeResources -and $CurrentComputeResources.launchTemplate -and $CurrentComputeResources.launchTemplate.launchTemplateName) {
        $launchTemplate.launchTemplateName = "$($CurrentComputeResources.launchTemplate.launchTemplateName)"
    } else {
        $launchTemplate.launchTemplateName = Ensure-BatchLaunchTemplate -TemplateName "academy-video-batch-200gb" -RootVolumeSizeGb $script:VideoCERootVolumeSizeGb -Region $script:Region
    }

    $computeResources = [ordered]@{
        type = Get-VideoCEComputeResourceType
        allocationStrategy = "BEST_FIT_PROGRESSIVE"
        minvCpus = [int]$script:VideoCEMinvCpus
        maxvCpus = [int]$script:VideoCEMaxvCpus
        instanceTypes = @($desiredTypes)
        subnets = @($subnets)
        securityGroupIds = @($securityGroups)
        instanceRole = $instanceRole
        launchTemplate = $launchTemplate
    }
    $json = $computeResources | ConvertTo-Json -Depth 10 -Compress
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $json, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:VideoCEName, "--compute-resources", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "update Video CE" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    Wait-CEValidEnabled -CEName $script:VideoCEName -Reg $script:Region
    Write-Ok "Video CE updated"
}

function New-VideoCE {
    $iam = $script:BatchIam
    $subnets = @($script:PrivateSubnets | Where-Object { $_ })
    if (-not $subnets -or $subnets.Count -eq 0) { $subnets = @($script:PublicSubnets | Where-Object { $_ }) }
    $subnetArr = ($subnets | ForEach-Object { "`"$_`"" }) -join ","
    $ltName = Ensure-BatchLaunchTemplate -TemplateName "academy-video-batch-200gb" -RootVolumeSizeGb $script:VideoCERootVolumeSizeGb -Region $script:Region
    $path = Join-Path $BatchPath "video_compute_env.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $script:VideoCEName
    $content = $content -replace "PLACEHOLDER_SERVICE_ROLE_ARN", $iam.ServiceRoleArn
    $content = $content -replace "PLACEHOLDER_INSTANCE_PROFILE_ARN", $iam.InstanceProfileArn
    $content = $content -replace "PLACEHOLDER_SECURITY_GROUP_ID", $script:BatchSecurityGroupId
    $content = $content -replace "PLACEHOLDER_SUBNETS", $subnetArr
    $content = $content -replace "PLACEHOLDER_MIN_VCPUS", $script:VideoCEMinvCpus
    $content = $content -replace "PLACEHOLDER_MAX_VCPUS", $script:VideoCEMaxvCpus
    $content = $content -replace "PLACEHOLDER_COMPUTE_RESOURCE_TYPE", (Get-VideoCEComputeResourceType)
    $videoInstanceTypes = @($script:VideoCEInstanceTypes | Where-Object { $_ })
    if (-not $videoInstanceTypes -or $videoInstanceTypes.Count -eq 0) { $videoInstanceTypes = @($script:VideoCEInstanceType) }
    $instanceTypesJson = ($videoInstanceTypes | ForEach-Object { "`"$_`"" }) -join ","
    $content = $content -replace "PLACEHOLDER_INSTANCE_TYPES", $instanceTypesJson
    $content = $content -replace "PLACEHOLDER_LAUNCH_TEMPLATE_NAME", $ltName
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "create-compute-environment", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "create Video CE" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

# Ops CE: Private Subnet 사용 시 해당 서브넷의 라우트 테이블에 0.0.0.0/0 -> NAT Gateway 가 있어야 ECR/CloudWatch 아웃바운드 가능. Ensure-Network 에서 Private RT 생성 시 NAT 경로 설정함.
function New-OpsCE {
    $iam = $script:BatchIam
    $subnets = @($script:PrivateSubnets | Where-Object { $_ })
    if (-not $subnets -or $subnets.Count -eq 0) { $subnets = @($script:PublicSubnets | Where-Object { $_ }) }
    $subnetArr = ($subnets | ForEach-Object { "`"$_`"" }) -join ","
    $path = Join-Path $BatchPath "ops_compute_env.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_SERVICE_ROLE_ARN", $iam.ServiceRoleArn
    $content = $content -replace "PLACEHOLDER_INSTANCE_PROFILE_ARN", $iam.InstanceProfileArn
    $content = $content -replace "PLACEHOLDER_SECURITY_GROUP_ID", $script:BatchSecurityGroupId
    $content = $content -replace "PLACEHOLDER_SUBNETS", $subnetArr
    $content = $content -replace "PLACEHOLDER_INSTANCE_TYPE", $script:OpsCEInstanceType
    $content = $content -replace "PLACEHOLDER_MAX_VCPUS", $script:OpsCEMaxvCpus.ToString()
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "create-compute-environment", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "create Ops CE" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function New-VideoQueue { param([string]$CeArn)
    $path = Join-Path $BatchPath "video_job_queue.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $CeArn
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "create-job-queue", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "create Video Queue" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

# long path 폐기 (2026-05-10): New-VideoLongCE / New-VideoLongQueue 제거.

function New-OpsQueue { param([string]$CeArn)
    $path = Join-Path $BatchPath "ops_job_queue.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $CeArn
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "create-job-queue", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "create Ops Queue" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function Set-JobQueueEnabled { param([string]$QueueName, [string]$CeArn, [string]$Region)
    $json = '{"jobQueue":"' + $QueueName + '","state":"ENABLED","computeEnvironmentOrder":[{"order":1,"computeEnvironment":"' + $CeArn + '"}]}'
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $json, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "update-job-queue", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $Region) -ErrorMessage "Enable job queue $QueueName" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function Ensure-VideoCE {
    if ($script:PlanMode) { return }
    Write-Step "Ensure Video CE $($script:VideoCEName)"
    $ce = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoCEName, "--region", $script:Region, "--output", "json")
    if (-not $ce -or -not $ce.computeEnvironments -or $ce.computeEnvironments.Count -eq 0) {
        if (-not $script:AllowRebuild) { Write-Warn "Video CE not found; skip create."; return }
        Write-Host "  Creating Video CE" -ForegroundColor Yellow
        New-VideoCE
        $script:ChangesMade = $true
        Wait-CEValidEnabled -CEName $script:VideoCEName -Reg $script:Region
        return
    }
    $c = $ce.computeEnvironments[0]
    $res = $c.computeResources
    $currentMax = if ($res -and $res.maxvCpus) { [int]$res.maxvCpus } else { 0 }
    $currentTypes = if ($res -and $res.instanceTypes) { @($res.instanceTypes | Where-Object { $_ }) } else { @() }
    $currentResourceType = if ($res -and $res.PSObject.Properties["type"] -and $res.type) { "$($res.type)" } else { "" }
    $currentAllocation = if ($res -and $res.PSObject.Properties["allocationStrategy"] -and $res.allocationStrategy) { "$($res.allocationStrategy)" } else { "" }
    $desiredResourceType = Get-VideoCEComputeResourceType
    $desiredAllocation = "BEST_FIT_PROGRESSIVE"
    $desiredTypes = @($script:VideoCEInstanceTypes | Where-Object { $_ })
    if (-not $desiredTypes -or $desiredTypes.Count -eq 0) { $desiredTypes = @($script:VideoCEInstanceType) }
    $currentTypesKey = ($currentTypes | Sort-Object) -join ","
    $desiredTypesKey = ($desiredTypes | Sort-Object) -join ","
    $driftReasons = [System.Collections.ArrayList]::new()
    if ($currentResourceType -ne $desiredResourceType) { [void]$driftReasons.Add("type $currentResourceType -> $desiredResourceType") }
    if ($currentAllocation -ne $desiredAllocation) { [void]$driftReasons.Add("allocation $currentAllocation -> $desiredAllocation") }
    if ($currentMax -ne $script:VideoCEMaxvCpus) { [void]$driftReasons.Add("maxvCpus $currentMax -> $($script:VideoCEMaxvCpus)") }
    if ($currentTypesKey -ne $desiredTypesKey) { [void]$driftReasons.Add("types $currentTypesKey -> $desiredTypesKey") }
    $videoCEDrift = ($driftReasons.Count -gt 0)
    if ($c.status -eq "INVALID" -or $videoCEDrift) {
        $reason = if ($c.status -eq "INVALID") { "status INVALID" } else { $driftReasons -join "; " }
        if (-not $script:AllowRebuild) {
            if ($videoCEDrift) { Write-Warn "Video CE drift ($reason); run deploy (not -Plan) to update in place." }
            else { Write-Warn "Video CE INVALID; skip recreate." }
            return
        }
        if ($c.status -eq "INVALID") { Recreate-VideoCEAndQueue -Reason $reason }
        else { Update-VideoCEInPlace -Reason $reason -CurrentComputeResources $res }
        return
    }
    if ($c.state -eq "DISABLED") {
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:VideoCEName, "--state", "ENABLED", "--region", $script:Region) | Out-Null
        $script:ChangesMade = $true
        Wait-CEValidEnabled -CEName $script:VideoCEName -Reg $script:Region
    } else { Write-Ok "Video CE status=$($c.status) state=$($c.state)" }
}

function Ensure-OpsCE {
    if ($script:PlanMode) { return }
    Write-Step "Ensure Ops CE $($script:OpsCEName)"
    $ce = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:OpsCEName, "--region", $script:Region, "--output", "json")
    if (-not $ce -or -not $ce.computeEnvironments -or $ce.computeEnvironments.Count -eq 0) {
        if (-not $script:AllowRebuild) { Write-Warn "Ops CE not found; skip create."; return }
        Write-Host "  Creating Ops CE" -ForegroundColor Yellow
        New-OpsCE
        $script:ChangesMade = $true
        Wait-CEValidEnabled -CEName $script:OpsCEName -Reg $script:Region
        return
    }
    $c = $ce.computeEnvironments[0]
    $res = $c.computeResources
    $currentType = if ($res -and $res.instanceTypes -and $res.instanceTypes.Count -gt 0) { $res.instanceTypes[0] } else { $null }
    $currentMax = if ($res -and $res.maxvCpus) { [int]$res.maxvCpus } else { 0 }
    $opsTypeDrift = ($currentType -ne $script:OpsCEInstanceType) -or ($currentMax -ne $script:OpsCEMaxvCpus)
    if ($c.status -eq "INVALID" -or $opsTypeDrift) {
        if (-not $script:AllowRebuild) {
            if ($opsTypeDrift) { Write-Warn "Ops CE instance type drift (current $currentType max=$currentMax, want $($script:OpsCEInstanceType) max=$($script:OpsCEMaxvCpus)); run with -AllowRebuild to recreate." }
            else { Write-Warn "Ops CE INVALID; skip recreate." }
            return
        }
        if ($opsTypeDrift) { Write-Host "  Ops CE instance type drift -> disable queue, delete queue, disable CE, delete, create ($($script:OpsCEInstanceType)), create queue" -ForegroundColor Yellow }
        else { Write-Host "  INVALID -> disable queue, delete queue, disable CE, delete, wait, create, create queue" -ForegroundColor Yellow }
        $script:ChangesMade = $true
        $qCheck = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json")
        if ($qCheck -and $qCheck.jobQueues -and $qCheck.jobQueues.Count -gt 0) {
            Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:OpsQueueName, "--state", "DISABLED", "--region", $script:Region) -ErrorMessage "disable Ops queue" | Out-Null
            $wait = 0; while ($wait -lt 90) { Start-Sleep -Seconds 5; $wait += 5; $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json"); if ($q -and $q.jobQueues -and $q.jobQueues[0].state -eq "DISABLED") { break } }
            Invoke-Aws @("batch", "delete-job-queue", "--job-queue", $script:OpsQueueName, "--region", $script:Region) -ErrorMessage "delete Ops queue (required before CE delete)" | Out-Null
            $wait = 0; while ($wait -lt 60) { Start-Sleep -Seconds 5; $wait += 5; $dq = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json"); if (-not $dq -or -not $dq.jobQueues -or $dq.jobQueues.Count -eq 0) { break } }
        }
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:OpsCEName, "--state", "DISABLED", "--region", $script:Region) | Out-Null
        $wait = 0; while ($wait -lt 120) { Start-Sleep -Seconds 5; $wait += 5; $ce2 = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:OpsCEName, "--region", $script:Region, "--output", "json"); if ($ce2 -and $ce2.computeEnvironments -and $ce2.computeEnvironments[0].state -eq "DISABLED") { break } }
        $deleteRetries = 0
        while ($deleteRetries -lt 5) {
            try {
                Invoke-Aws @("batch", "delete-compute-environment", "--compute-environment", $script:OpsCEName, "--region", $script:Region) -ErrorMessage "delete Ops CE" | Out-Null
                break
            } catch {
                if ($_.Exception.Message -match "resource is being modified" -and $deleteRetries -lt 4) {
                    Write-Host "  Ops CE delete delayed (resource modifying); retry in 30s..." -ForegroundColor Yellow
                    Start-Sleep -Seconds 30
                    $deleteRetries++
                } else { throw }
            }
        }
        Wait-CEDeleted -CEName $script:OpsCEName -Reg $script:Region
        New-OpsCE
        Wait-CEValidEnabled -CEName $script:OpsCEName -Reg $script:Region
        $ceArn = Get-CEArn -Name $script:OpsCEName
        New-OpsQueue -CeArn $ceArn
        Write-Ok "Ops CE and queue recreated"
        return
    }
    if ($c.state -eq "DISABLED") {
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:OpsCEName, "--state", "ENABLED", "--region", $script:Region) | Out-Null
        $script:ChangesMade = $true
        Wait-CEValidEnabled -CEName $script:OpsCEName -Reg $script:Region
    } else { Write-Ok "Ops CE status=$($c.status) state=$($c.state)" }
}

function Ensure-VideoQueue {
    if ($script:PlanMode) { return }
    Write-Step "Ensure Video Queue $($script:VideoQueueName)"
    $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $script:Region, "--output", "json")
    if (-not $q -or -not $q.jobQueues -or $q.jobQueues.Count -eq 0) {
        if (-not $script:AllowRebuild) { return }
        $ceArn = Get-CEArn -Name $script:VideoCEName
        if (-not $ceArn) { throw "Video CE not found" }
        New-VideoQueue -CeArn $ceArn
        $script:ChangesMade = $true
        Write-Ok "Video Queue created"
        return
    }
    $qu = $q.jobQueues[0]
    if ($qu.state -eq "DISABLED") {
        $ceArn = Get-CEArn -Name $script:VideoCEName
        if ($ceArn) { Set-JobQueueEnabled -QueueName $script:VideoQueueName -CeArn $ceArn -Region $script:Region; $script:ChangesMade = $true }
    } else { Write-Ok "Video Queue state=$($qu.state)" }
}

# long path 폐기 (2026-05-10): Ensure-VideoLongCE / Ensure-VideoLongQueue 제거.

function Ensure-OpsQueue {
    if ($script:PlanMode) { return }
    Write-Step "Ensure Ops Queue $($script:OpsQueueName)"
    $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json")
    if (-not $q -or -not $q.jobQueues -or $q.jobQueues.Count -eq 0) {
        if (-not $script:AllowRebuild) { return }
        $ceArn = Get-CEArn -Name $script:OpsCEName
        if (-not $ceArn) { throw "Ops CE not found" }
        New-OpsQueue -CeArn $ceArn
        $script:ChangesMade = $true
        Write-Ok "Ops Queue created"
        return
    }
    $qu = $q.jobQueues[0]
    if ($qu.state -eq "DISABLED") {
        $ceArn = Get-CEArn -Name $script:OpsCEName
        if ($ceArn) { Set-JobQueueEnabled -QueueName $script:OpsQueueName -CeArn $ceArn -Region $script:Region; $script:ChangesMade = $true }
    } else { Write-Ok "Ops Queue state=$($qu.state)" }
}
