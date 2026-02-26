# Ensure Batch Video CE, Ops CE, Video Queue, Ops Queue. Describe -> Decision -> Update/Create. Wait loops for delete/recreate.
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$BatchPath = Join-Path $InfraPath "batch"

function Ensure-VideoCE {
    Write-Step "Ensure Video CE $($script:VideoCEName)"
    $ce = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoCEName, "--region", $script:Region, "--output", "json")
    if (-not $ce -or -not $ce.computeEnvironments -or $ce.computeEnvironments.Count -eq 0) {
        Write-Ok "Video CE not found; create via scripts/infra/batch_video_setup.ps1 or bootstrap first."
        return
    }
    $c = $ce.computeEnvironments[0]
    $status = $c.status
    $state = $c.state
    if ($status -eq "INVALID") {
        Write-Host "  INVALID -> delete, wait, recreate (re-run deploy after bootstrap)" -ForegroundColor Yellow
        Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:VideoQueueName, "--compute-environment-order", "[{\"order\":1,\"computeEnvironment\":\"\"}]", "--region", $script:Region) -ErrorMessage "Update queue to remove CE" 2>$null
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:VideoCEName, "--state", "DISABLED", "--region", $script:Region) -ErrorMessage "Disable CE"
        Invoke-Aws @("batch", "delete-compute-environment", "--compute-environment", $script:VideoCEName, "--region", $script:Region) -ErrorMessage "Delete CE"
        Wait-CEDeleted -CEName $script:VideoCEName -Reg $script:Region
        Write-Warn "Recreate Video CE manually: scripts/infra/batch_video_setup.ps1 then re-run deploy"
        return
    }
    if ($state -eq "DISABLED") {
        Write-Host "  Enabling CE" -ForegroundColor Yellow
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:VideoCEName, "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Video CE"
        Wait-CEValidEnabled -CEName $script:VideoCEName -Reg $script:Region
    } else {
        Write-Ok "Video CE status=$status state=$state"
    }
}

function Ensure-OpsCE {
    Write-Step "Ensure Ops CE $($script:OpsCEName)"
    $ce = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:OpsCEName, "--region", $script:Region, "--output", "json")
    if (-not $ce -or -not $ce.computeEnvironments -or $ce.computeEnvironments.Count -eq 0) {
        Write-Ok "Ops CE not found; create via scripts/infra/batch_ops_setup.ps1 first."
        return
    }
    $c = $ce.computeEnvironments[0]
    $status = $c.status
    $state = $c.state
    if ($status -eq "INVALID") {
        Write-Host "  INVALID -> delete, wait, recreate" -ForegroundColor Yellow
        Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:OpsQueueName, "--compute-environment-order", "[{\"order\":1,\"computeEnvironment\":\"\"}]", "--region", $script:Region) -ErrorMessage "Update Ops queue" 2>$null
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:OpsCEName, "--state", "DISABLED", "--region", $script:Region) -ErrorMessage "Disable Ops CE"
        Invoke-Aws @("batch", "delete-compute-environment", "--compute-environment", $script:OpsCEName, "--region", $script:Region) -ErrorMessage "Delete Ops CE"
        Wait-CEDeleted -CEName $script:OpsCEName -Reg $script:Region
        Write-Warn "Recreate Ops CE manually: scripts/infra/batch_ops_setup.ps1 then re-run deploy"
        return
    }
    if ($state -eq "DISABLED") {
        Invoke-Aws @("batch", "update-compute-environment", "--compute-environment", $script:OpsCEName, "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Ops CE"
        Wait-CEValidEnabled -CEName $script:OpsCEName -Reg $script:Region
    } else {
        Write-Ok "Ops CE status=$status state=$state"
    }
}

function Ensure-VideoQueue {
    Write-Step "Ensure Video Queue $($script:VideoQueueName)"
    $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $script:Region, "--output", "json")
    if (-not $q -or -not $q.jobQueues -or $q.jobQueues.Count -eq 0) {
        Write-Ok "Video Queue not found; create via batch_video_setup first."
        return
    }
    $qu = $q.jobQueues[0]
    if ($qu.state -eq "DISABLED") {
        Write-Host "  Enabling queue" -ForegroundColor Yellow
        $ceArn = (Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoCEName, "--region", $script:Region, "--output", "json")).computeEnvironments[0].computeEnvironmentArn
        Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:VideoQueueName, "--compute-environment-order", "[{\"order\":1,\"computeEnvironment\":\"$ceArn\"}]", "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Video Queue"
    } else {
        Write-Ok "Video Queue state=$($qu.state)"
    }
}

function Ensure-OpsQueue {
    Write-Step "Ensure Ops Queue $($script:OpsQueueName)"
    $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $script:Region, "--output", "json")
    if (-not $q -or -not $q.jobQueues -or $q.jobQueues.Count -eq 0) {
        Write-Ok "Ops Queue not found; create via batch_ops_setup first."
        return
    }
    $qu = $q.jobQueues[0]
    if ($qu.state -eq "DISABLED") {
        $ceArn = (Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:OpsCEName, "--region", $script:Region, "--output", "json")).computeEnvironments[0].computeEnvironmentArn
        Invoke-Aws @("batch", "update-job-queue", "--job-queue", $script:OpsQueueName, "--compute-environment-order", "[{\"order\":1,\"computeEnvironment\":\"$ceArn\"}]", "--state", "ENABLED", "--region", $script:Region) -ErrorMessage "Enable Ops Queue"
    } else {
        Write-Ok "Ops Queue state=$($qu.state)"
    }
}
