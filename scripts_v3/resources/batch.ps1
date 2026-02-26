# Full Rebuild: Ensure Batch Video/Ops CE and Queues. Create if missing; INVALID -> delete+wait+recreate+wait+enable.
# Uses scripts/infra/batch/*.json (read-only). Requires $script:BatchIam (from Ensure-BatchIAM) and $script:AllowRebuild.
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$BatchPath = Join-Path $InfraPath "batch"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Get-CEArn {
    param([string]$Name)
    $r = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $Name, "--region", $script:Region, "--output", "json")
    if (-not $r -or -not $r.computeEnvironments -or $r.computeEnvironments.Count -eq 0) { return $null }
    return $r.computeEnvironments[0].computeEnvironmentArn
}

function New-VideoCE {
    $iam = $script:BatchIam
    $subnetArr = ($script:PublicSubnets | ForEach-Object { "`"$_`"" }) -join ","
    $path = Join-Path $BatchPath "video_compute_env.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $script:VideoCEName
    $content = $content -replace "PLACEHOLDER_SERVICE_ROLE_ARN", $iam.ServiceRoleArn
    $content = $content -replace "PLACEHOLDER_INSTANCE_PROFILE_ARN", $iam.InstanceProfileArn
    $content = $content -replace "PLACEHOLDER_SECURITY_GROUP_ID", $script:BatchSecurityGroupId
    $content = $content -replace '"PLACEHOLDER_SUBNET_1"', $subnetArr
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "create-compute-environment", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "create Video CE" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function New-OpsCE {
    $iam = $script:BatchIam
    $subnetArr = ($script:PublicSubnets | ForEach-Object { "`"$_`"" }) -join ","
    $path = Join-Path $BatchPath "ops_compute_env.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_SERVICE_ROLE_ARN", $iam.ServiceRoleArn
    $content = $content -replace "PLACEHOLDER_INSTANCE_PROFILE_ARN", $iam.InstanceProfileArn
    $content = $content -replace "PLACEHOLDER_SECURITY_GROUP_ID", $script:BatchSecurityGroupId
    $content = $content -replace '"PLACEHOLDER_SUBNET_1"', $subnetArr
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "create-compute-environment", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "create Ops CE" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function New-VideoQueue {
    param([string]$CeArn)
    $path = Join-Path $BatchPath "video_job_queue.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $CeArn
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "create-job-queue", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "create Video Queue" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function New-OpsQueue {
    param([string]$CeArn)
    $path = Join-Path $BatchPath "ops_job_queue.json"
    $content = [System.IO.File]::ReadAllText($path, $utf8NoBom)
    $content = $content -replace "PLACEHOLDER_COMPUTE_ENV_NAME", $CeArn
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        Invoke-Aws @("batch", "create-job-queue", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) -ErrorMessage "create Ops Queue" | Out-Null
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function Ensure-VideoCE {
    Write-Step "Ensure Video CE $($script:VideoCEName)"
    $ce = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoCEName, "--region", $script:Region, "--output", "json")
    if (-not $ce -or -not $ce.computeEnvironments -or $ce.computeEnvironments.Count -eq 0) {
        if (-not $script:AllowRebuild) { Write-Warn "Video CE not found; -AllowRebuild false, skip create."; return }
        Write-Host "  Creating Video CE" -ForegroundColor Yellow
        New-VideoCE
        Wait-CEValidEnabled -CEName $script:VideoCEName -Reg $script:Region
        return
    }
    $c = $ce.computeEnvironments[0]
    $status = $c.status
    $state = $c.state
    if ($status -eq "INVALID") {
        if (-not $script:AllowRebuild) { Write-Warn "Video CE INVALID; -AllowRebuild false, skip recreate."; return }
        Write-Host "  INVALID -> disable queue, disable CE, delete, wait, create, wait, enable" -ForegroundColor Yellow
        Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:VideoQueueName, "--state", "DISABLED", "--region", $script:Region) -ErrorMessage "Disable Video Queue" 2>$null | Out-Null
        Start-Sleep -Seconds 5
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:VideoCEName, "--state", "DISABLED", "--region", $script:Region) -ErrorMessage "Disable Video CE" | Out-Null
        $wait = 0; while ($wait -lt 90) { Start-Sleep -Seconds 5; $wait += 5; $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $script:Region, "--output", "json"); if ($q -and $q.jobQueues -and $q.jobQueues[0].state -eq "DISABLED") { break } }
        $wait = 0; while ($wait -lt 120) { Start-Sleep -Seconds 5; $wait += 5; $ce2 = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoCEName, "--region", $script:Region, "--output", "json"); if ($ce2 -and $ce2.computeEnvironments -and $ce2.computeEnvironments[0].state -eq "DISABLED") { break } }
        Invoke-Aws @("batch", "delete-compute-environment", "--compute-environment", $script:VideoCEName, "--region", $script:Region) -ErrorMessage "Delete Video CE" | Out-Null
        Wait-CEDeleted -CEName $script:VideoCEName -Reg $script:Region
        New-VideoCE
        Wait-CEValidEnabled -CEName $script:VideoCEName -Reg $script:Region
        $ceArn = Get-CEArn -Name $script:VideoCEName
        Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:VideoQueueName, "--compute-environment-order", "[{\"order\":1,\"computeEnvironment\":\"$ceArn\"}]", "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Video Queue" 2>$null | Out-Null
        return
    }
    if ($state -eq "DISABLED") {
        Write-Host "  Enabling CE" -ForegroundColor Yellow
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:VideoCEName, "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Video CE" | Out-Null
        Wait-CEValidEnabled -CEName $script:VideoCEName -Reg $script:Region
    } else {
        Write-Ok "Video CE status=$status state=$state"
    }
}

function Ensure-OpsCE {
    Write-Step "Ensure Ops CE $($script:OpsCEName)"
    $ce = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:OpsCEName, "--region", $script:Region, "--output", "json")
    if (-not $ce -or -not $ce.computeEnvironments -or $ce.computeEnvironments.Count -eq 0) {
        if (-not $script:AllowRebuild) { Write-Warn "Ops CE not found; -AllowRebuild false, skip create."; return }
        Write-Host "  Creating Ops CE" -ForegroundColor Yellow
        New-OpsCE
        Wait-CEValidEnabled -CEName $script:OpsCEName -Reg $script:Region
        return
    }
    $c = $ce.computeEnvironments[0]
    $status = $c.status
    $state = $c.state
    if ($status -eq "INVALID") {
        if (-not $script:AllowRebuild) { Write-Warn "Ops CE INVALID; -AllowRebuild false, skip recreate."; return }
        Write-Host "  INVALID -> disable queue, disable CE, delete, wait, create, wait, enable" -ForegroundColor Yellow
        Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:OpsQueueName, "--state", "DISABLED", "--region", $script:Region) -ErrorMessage "Disable Ops Queue" 2>$null | Out-Null
        Start-Sleep -Seconds 5
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:OpsCEName, "--state", "DISABLED", "--region", $script:Region) -ErrorMessage "Disable Ops CE" | Out-Null
        $wait = 0; while ($wait -lt 90) { Start-Sleep -Seconds 5; $wait += 5; $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json"); if ($q -and $q.jobQueues -and $q.jobQueues[0].state -eq "DISABLED") { break } }
        $wait = 0; while ($wait -lt 120) { Start-Sleep -Seconds 5; $wait += 5; $ce2 = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:OpsCEName, "--region", $script:Region, "--output", "json"); if ($ce2 -and $ce2.computeEnvironments -and $ce2.computeEnvironments[0].state -eq "DISABLED") { break } }
        Invoke-Aws @("batch", "delete-compute-environment", "--compute-environment", $script:OpsCEName, "--region", $script:Region) -ErrorMessage "Delete Ops CE" | Out-Null
        Wait-CEDeleted -CEName $script:OpsCEName -Reg $script:Region
        New-OpsCE
        Wait-CEValidEnabled -CEName $script:OpsCEName -Reg $script:Region
        $ceArn = Get-CEArn -Name $script:OpsCEName
        Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:OpsQueueName, "--compute-environment-order", "[{\"order\":1,\"computeEnvironment\":\"$ceArn\"}]", "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Ops Queue" 2>$null | Out-Null
        return
    }
    if ($state -eq "DISABLED") {
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:OpsCEName, "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Ops CE" | Out-Null
        Wait-CEValidEnabled -CEName $script:OpsCEName -Reg $script:Region
    } else {
        Write-Ok "Ops CE status=$status state=$state"
    }
}

function Ensure-VideoQueue {
    Write-Step "Ensure Video Queue $($script:VideoQueueName)"
    $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $script:Region, "--output", "json")
    if (-not $q -or -not $q.jobQueues -or $q.jobQueues.Count -eq 0) {
        if (-not $script:AllowRebuild) { Write-Warn "Video Queue not found; skip create."; return }
        $ceArn = Get-CEArn -Name $script:VideoCEName
        if (-not $ceArn) { throw "Video CE not found; cannot create Video Queue." }
        Write-Host "  Creating Video Queue" -ForegroundColor Yellow
        New-VideoQueue -CeArn $ceArn
        Write-Ok "Video Queue created"
        return
    }
    $qu = $q.jobQueues[0]
    if ($qu.state -eq "DISABLED") {
        Write-Host "  Enabling queue" -ForegroundColor Yellow
        $ceArn = Get-CEArn -Name $script:VideoCEName
        if ($ceArn) {
            Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:VideoQueueName, "--compute-environment-order", "[{\"order\":1,\"computeEnvironment\":\"$ceArn\"}]", "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Video Queue" | Out-Null
        }
    } else {
        Write-Ok "Video Queue state=$($qu.state)"
    }
}

function Ensure-OpsQueue {
    Write-Step "Ensure Ops Queue $($script:OpsQueueName)"
    $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json")
    if (-not $q -or -not $q.jobQueues -or $q.jobQueues.Count -eq 0) {
        if (-not $script:AllowRebuild) { Write-Warn "Ops Queue not found; skip create."; return }
        $ceArn = Get-CEArn -Name $script:OpsCEName
        if (-not $ceArn) { throw "Ops CE not found; cannot create Ops Queue." }
        Write-Host "  Creating Ops Queue" -ForegroundColor Yellow
        New-OpsQueue -CeArn $ceArn
        Write-Ok "Ops Queue created"
        return
    }
    $qu = $q.jobQueues[0]
    if ($qu.state -eq "DISABLED") {
        $ceArn = Get-CEArn -Name $script:OpsCEName
        if ($ceArn) {
            Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:OpsQueueName, "--compute-environment-order", "[{\"order\":1,\"computeEnvironment\":\"$ceArn\"}]", "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Ops Queue" | Out-Null
        }
    } else {
        Write-Ok "Ops Queue state=$($qu.state)"
    }
}
