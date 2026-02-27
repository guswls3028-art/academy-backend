# Batch: Video/Ops CE and Queue Ensure. Uses v4/templates/batch. INVALID -> delete+wait+recreate+wait.
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
    if ($c.status -eq "INVALID") {
        if (-not $script:AllowRebuild) { Write-Warn "Video CE INVALID; skip recreate."; return }
        Write-Host "  INVALID -> disable queue, disable CE, delete, wait, create, wait" -ForegroundColor Yellow
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
    if ($c.status -eq "INVALID") {
        if (-not $script:AllowRebuild) { Write-Warn "Ops CE INVALID; skip recreate."; return }
        Write-Host "  INVALID -> disable queue, disable CE, delete, wait, create, wait" -ForegroundColor Yellow
        $script:ChangesMade = $true
        $qCheck = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json")
        if ($qCheck -and $qCheck.jobQueues -and $qCheck.jobQueues.Count -gt 0) {
            Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:OpsQueueName, "--state", "DISABLED", "--region", $script:Region) 2>$null | Out-Null
            $wait = 0; while ($wait -lt 90) { Start-Sleep -Seconds 5; $wait += 5; $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json"); if ($q -and $q.jobQueues -and $q.jobQueues[0].state -eq "DISABLED") { break } }
        }
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:OpsCEName, "--state", "DISABLED", "--region", $script:Region) | Out-Null
        $wait = 0; while ($wait -lt 120) { Start-Sleep -Seconds 5; $wait += 5; $ce2 = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:OpsCEName, "--region", $script:Region, "--output", "json"); if ($ce2 -and $ce2.computeEnvironments -and $ce2.computeEnvironments[0].state -eq "DISABLED") { break } }
        Invoke-Aws @("batch", "delete-compute-environment", "--compute-environment", $script:OpsCEName, "--region", $script:Region) | Out-Null
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
