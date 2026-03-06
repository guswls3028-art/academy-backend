# Batch: Video/Ops CE and Queue Ensure. Uses v1/templates/batch. INVALID -> delete+wait+recreate+wait.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"
$V4Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BatchPath = Join-Path $V4Root "templates\batch"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Get-CEArn { param([string]$Name)
    $r = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $Name, "--region", $script:Region, "--output", "json")
    if (-not $r -or -not $r.computeEnvironments -or $r.computeEnvironments.Count -eq 0) { return $null }
    return $r.computeEnvironments[0].computeEnvironmentArn
}

function New-VideoCE {
    $iam = $script:BatchIam
    $subnets = @($script:PrivateSubnets | Where-Object { $_ })
    if (-not $subnets -or $subnets.Count -eq 0) { $subnets = @($script:PublicSubnets | Where-Object { $_ }) }
    $subnetArr = ($subnets | ForEach-Object { "`"$_`"" }) -join ","
    $path = Join-Path $BatchPath "video_compute_env.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $script:VideoCEName
    $content = $content -replace "PLACEHOLDER_SERVICE_ROLE_ARN", $iam.ServiceRoleArn
    $content = $content -replace "PLACEHOLDER_INSTANCE_PROFILE_ARN", $iam.InstanceProfileArn
    $content = $content -replace "PLACEHOLDER_SECURITY_GROUP_ID", $script:BatchSecurityGroupId
    $content = $content -replace "PLACEHOLDER_SUBNETS", $subnetArr
    $content = $content -replace "PLACEHOLDER_MIN_VCPUS", $script:VideoCEMinvCpus
    $content = $content -replace "PLACEHOLDER_MAX_VCPUS", $script:VideoCEMaxvCpus
    $content = $content -replace "PLACEHOLDER_INSTANCE_TYPE", $script:VideoCEInstanceType
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

function New-VideoLongCE {
    if (-not $script:VideoLongCEName) { return }
    $iam = $script:BatchIam
    $subnets = @($script:PrivateSubnets | Where-Object { $_ })
    if (-not $subnets -or $subnets.Count -eq 0) { $subnets = @($script:PublicSubnets | Where-Object { $_ }) }
    $subnetArr = ($subnets | ForEach-Object { "`"$_`"" }) -join ","
    $path = Join-Path $BatchPath "video_compute_env_long.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $script:VideoLongCEName
    $content = $content -replace "PLACEHOLDER_SERVICE_ROLE_ARN", $iam.ServiceRoleArn
    $content = $content -replace "PLACEHOLDER_INSTANCE_PROFILE_ARN", $iam.InstanceProfileArn
    $content = $content -replace "PLACEHOLDER_SECURITY_GROUP_ID", $script:BatchSecurityGroupId
    $content = $content -replace "PLACEHOLDER_SUBNETS", $subnetArr
    $content = $content -replace "PLACEHOLDER_MIN_VCPUS", $script:VideoLongMinvCpus
    $content = $content -replace "PLACEHOLDER_MAX_VCPUS", $script:VideoLongMaxvCpus
    $content = $content -replace "PLACEHOLDER_INSTANCE_TYPE", $script:VideoLongInstanceType
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "create-compute-environment", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "create Video Long CE" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function New-VideoLongQueue { param([string]$CeArn)
    $path = Join-Path $BatchPath "video_job_queue_long.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $CeArn
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "create-job-queue", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "create Video Long Queue" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

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
    $currentType = if ($res -and $res.instanceTypes -and $res.instanceTypes.Count -gt 0) { $res.instanceTypes[0] } else { $null }
    $videoCEDrift = ($currentMax -ne $script:VideoCEMaxvCpus) -or ($currentType -ne $script:VideoCEInstanceType)
    if ($c.status -eq "INVALID" -or $videoCEDrift) {
        if (-not $script:AllowRebuild) {
            if ($videoCEDrift) { Write-Warn "Video CE drift (current max=$currentMax type=$currentType, SSOT max=$($script:VideoCEMaxvCpus) type=$($script:VideoCEInstanceType)); run with -AllowRebuild to recreate." }
            else { Write-Warn "Video CE INVALID; skip recreate." }
            return
        }
        if ($videoCEDrift) {
            Write-Warn "Video CE drift detected. CE delete requires queue to be re-pointed first; skipping rebuild to avoid JobQueue relationship error. Deploy continues."
            return
        }
        if ($c.status -eq "INVALID") { Write-Host "  INVALID -> disable queue, disable CE, delete, wait, create, wait" -ForegroundColor Yellow }
        $script:ChangesMade = $true
        $qCheck = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $script:Region, "--output", "json")
        if ($qCheck -and $qCheck.jobQueues -and $qCheck.jobQueues.Count -gt 0) {
            Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:VideoQueueName, "--state", "DISABLED", "--region", $script:Region) 2>$null | Out-Null
            $wait = 0; while ($wait -lt 90) { Start-Sleep -Seconds 5; $wait += 5; $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $script:Region, "--output", "json"); if ($q -and $q.jobQueues -and $q.jobQueues[0].state -eq "DISABLED") { break } }
        }
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:VideoCEName, "--state", "DISABLED", "--region", $script:Region) | Out-Null
        $wait = 0; while ($wait -lt 120) { Start-Sleep -Seconds 5; $wait += 5; $ce2 = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoCEName, "--region", $script:Region, "--output", "json"); if ($ce2 -and $ce2.computeEnvironments -and $ce2.computeEnvironments[0].state -eq "DISABLED") { break } }
        Invoke-Aws @("batch", "delete-compute-environment", "--compute-environment", $script:VideoCEName, "--region", $script:Region) | Out-Null
        Wait-CEDeleted -CEName $script:VideoCEName -Reg $script:Region
        New-VideoCE
        Wait-CEValidEnabled -CEName $script:VideoCEName -Reg $script:Region
        $ceArn = Get-CEArn -Name $script:VideoCEName
        $qAfter = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $script:Region, "--output", "json")
        if ($qAfter -and $qAfter.jobQueues -and $qAfter.jobQueues.Count -gt 0) { Set-JobQueueEnabled -QueueName $script:VideoQueueName -CeArn $ceArn -Region $script:Region }
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
        if ($opsTypeDrift) { Write-Host "  Ops CE instance type drift -> disable queue, disable CE, delete, create ($($script:OpsCEInstanceType)), enable queue" -ForegroundColor Yellow }
        else { Write-Host "  INVALID -> disable queue, disable CE, delete, wait, create, wait" -ForegroundColor Yellow }
        $script:ChangesMade = $true
        $qCheck = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json")
        if ($qCheck -and $qCheck.jobQueues -and $qCheck.jobQueues.Count -gt 0) {
            Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:OpsQueueName, "--state", "DISABLED", "--region", $script:Region) 2>$null | Out-Null
            $wait = 0; while ($wait -lt 90) { Start-Sleep -Seconds 5; $wait += 5; $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json"); if ($q -and $q.jobQueues -and $q.jobQueues[0].state -eq "DISABLED") { break } }
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
        $qAfter = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json")
        if ($qAfter -and $qAfter.jobQueues -and $qAfter.jobQueues.Count -gt 0) { Set-JobQueueEnabled -QueueName $script:OpsQueueName -CeArn $ceArn -Region $script:Region }
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

function Ensure-VideoLongCE {
    if (-not $script:VideoLongCEName -or $script:PlanMode) { return }
    Write-Step "Ensure Video Long CE $($script:VideoLongCEName)"
    $ce = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoLongCEName, "--region", $script:Region, "--output", "json")
    if (-not $ce -or -not $ce.computeEnvironments -or $ce.computeEnvironments.Count -eq 0) {
        if (-not $script:AllowRebuild) { Write-Warn "Video Long CE not found; skip create."; return }
        Write-Host "  Creating Video Long CE (On-Demand)" -ForegroundColor Yellow
        New-VideoLongCE
        $script:ChangesMade = $true
        Wait-CEValidEnabled -CEName $script:VideoLongCEName -Reg $script:Region
        return
    }
    $c = $ce.computeEnvironments[0]
    if ($c.status -eq "INVALID") {
        if (-not $script:AllowRebuild) { Write-Warn "Video Long CE INVALID; skip recreate."; return }
        Write-Host "  INVALID -> disable queue, disable CE, delete, wait, create, wait" -ForegroundColor Yellow
        $script:ChangesMade = $true
        $qCheck = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoLongQueueName, "--region", $script:Region, "--output", "json")
        if ($qCheck -and $qCheck.jobQueues -and $qCheck.jobQueues.Count -gt 0) {
            Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:VideoLongQueueName, "--state", "DISABLED", "--region", $script:Region) 2>$null | Out-Null
            $wait = 0; while ($wait -lt 90) { Start-Sleep -Seconds 5; $wait += 5; $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoLongQueueName, "--region", $script:Region, "--output", "json"); if ($q -and $q.jobQueues -and $q.jobQueues[0].state -eq "DISABLED") { break } }
        }
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:VideoLongCEName, "--state", "DISABLED", "--region", $script:Region) | Out-Null
        $wait = 0; while ($wait -lt 120) { Start-Sleep -Seconds 5; $wait += 5; $ce2 = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoLongCEName, "--region", $script:Region, "--output", "json"); if ($ce2 -and $ce2.computeEnvironments -and $ce2.computeEnvironments[0].state -eq "DISABLED") { break } }
        Invoke-Aws @("batch", "delete-compute-environment", "--compute-environment", $script:VideoLongCEName, "--region", $script:Region) | Out-Null
        Wait-CEDeleted -CEName $script:VideoLongCEName -Reg $script:Region
        New-VideoLongCE
        Wait-CEValidEnabled -CEName $script:VideoLongCEName -Reg $script:Region
        $ceArn = Get-CEArn -Name $script:VideoLongCEName
        $qAfter = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoLongQueueName, "--region", $script:Region, "--output", "json")
        if ($qAfter -and $qAfter.jobQueues -and $qAfter.jobQueues.Count -gt 0) { Set-JobQueueEnabled -QueueName $script:VideoLongQueueName -CeArn $ceArn -Region $script:Region }
        return
    }
    if ($c.state -eq "DISABLED") {
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:VideoLongCEName, "--state", "ENABLED", "--region", $script:Region) | Out-Null
        $script:ChangesMade = $true
        Wait-CEValidEnabled -CEName $script:VideoLongCEName -Reg $script:Region
    } else { Write-Ok "Video Long CE status=$($c.status) state=$($c.state)" }
}

function Ensure-VideoLongQueue {
    if (-not $script:VideoLongQueueName -or $script:PlanMode) { return }
    Write-Step "Ensure Video Long Queue $($script:VideoLongQueueName)"
    $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoLongQueueName, "--region", $script:Region, "--output", "json")
    if (-not $q -or -not $q.jobQueues -or $q.jobQueues.Count -eq 0) {
        if (-not $script:AllowRebuild) { return }
        $ceArn = Get-CEArn -Name $script:VideoLongCEName
        if (-not $ceArn) { throw "Video Long CE not found" }
        New-VideoLongQueue -CeArn $ceArn
        $script:ChangesMade = $true
        Write-Ok "Video Long Queue created"
        return
    }
    $qu = $q.jobQueues[0]
    if ($qu.state -eq "DISABLED") {
        $ceArn = Get-CEArn -Name $script:VideoLongCEName
        if ($ceArn) { Set-JobQueueEnabled -QueueName $script:VideoLongQueueName -CeArn $ceArn -Region $script:Region; $script:ChangesMade = $true }
    } else { Write-Ok "Video Long Queue state=$($qu.state)" }
}

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
