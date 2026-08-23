# Cooperative lock for standalone production runtime-environment writers.
$ErrorActionPreference = "Stop"

$script:RuntimeEnvMutationLockAcquired = $false
$script:RuntimeEnvMutationLockOwnedHere = $false
$script:RuntimeEnvMutationLockReleaseAllowed = $true
$script:RuntimeEnvMutationLockOwner = ""
$script:RuntimeEnvMutationLockTable = "academy-v1-video-job-lock"
$script:RuntimeEnvMutationLockTtlSeconds = 10800
$script:RuntimeEnvMutationLockHelper = Join-Path $PSScriptRoot "..\deployment_lock.py"

function Get-RuntimeEnvLockPython {
    $command = Get-Command python3 -ErrorAction SilentlyContinue
    if (-not $command) {
        $command = Get-Command python -ErrorAction Stop
    }
    return $command.Source
}

function Invoke-RuntimeEnvLockAction {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("acquire", "assert-owned", "renew", "release")]
        [string]$Action,
        [Parameter(Mandatory = $true)][string]$Region
    )
    $env:AWS_DEFAULT_REGION = $Region
    $python = Get-RuntimeEnvLockPython
    & $python $script:RuntimeEnvMutationLockHelper $Action `
        --owner $script:RuntimeEnvMutationLockOwner `
        --table-name $script:RuntimeEnvMutationLockTable `
        --ttl-seconds $script:RuntimeEnvMutationLockTtlSeconds
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime environment mutation lock action failed: $Action"
    }
}

function Enter-AcademyRuntimeEnvMutationLock {
    param(
        [string]$Region = "ap-northeast-2",
        [string]$OwnerPrefix = "runtime-env"
    )
    if ($script:RuntimeEnvMutationLockAcquired) {
        throw "Runtime environment mutation lock is already acquired in this process."
    }
    if ($env:ACADEMY_RUNTIME_ENV_LOCK_OWNER) {
        $script:RuntimeEnvMutationLockOwner = $env:ACADEMY_RUNTIME_ENV_LOCK_OWNER
        Invoke-RuntimeEnvLockAction -Action "assert-owned" -Region $Region
        $script:RuntimeEnvMutationLockAcquired = $true
        $script:RuntimeEnvMutationLockOwnedHere = $false
        $script:RuntimeEnvMutationLockReleaseAllowed = $true
        return
    }
    $script:RuntimeEnvMutationLockOwner = (
        "manual:{0}:{1}:{2}:{3}" -f
        $OwnerPrefix,
        [Environment]::MachineName,
        $PID,
        [Guid]::NewGuid().ToString("N")
    )
    Invoke-RuntimeEnvLockAction -Action "acquire" -Region $Region
    $script:RuntimeEnvMutationLockAcquired = $true
    $script:RuntimeEnvMutationLockOwnedHere = $true
    $script:RuntimeEnvMutationLockReleaseAllowed = $true
    $env:ACADEMY_RUNTIME_ENV_LOCK_OWNER = $script:RuntimeEnvMutationLockOwner
}

function Assert-AcademyRuntimeEnvMutationLock {
    param([string]$Region = "ap-northeast-2")
    if (-not $script:RuntimeEnvMutationLockAcquired) {
        throw "Runtime environment mutation lock is not acquired."
    }
    Invoke-RuntimeEnvLockAction -Action "assert-owned" -Region $Region
}

function Renew-AcademyRuntimeEnvMutationLock {
    param([string]$Region = "ap-northeast-2")
    if (-not $script:RuntimeEnvMutationLockAcquired) {
        throw "Runtime environment mutation lock is not acquired."
    }
    Invoke-RuntimeEnvLockAction -Action "renew" -Region $Region
}

function Assert-AcademyWorkerRefreshTargets {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$Names
    )
    if ($Names.Count -ne 3) {
        throw "Worker refresh requires exactly three ASG targets."
    }
    $seen = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($name in $Names) {
        if (
            [string]::IsNullOrWhiteSpace($name) -or
            $name -cne $name.Trim() -or
            -not $seen.Add($name)
        ) {
            throw "Worker refresh ASG targets must be exact, nonblank, and distinct."
        }
    }
    return $Names
}

function Start-AcademyInstanceRefresh {
    param(
        [Parameter(Mandatory = $true)][string]$AutoScalingGroupName,
        [string]$Region = "ap-northeast-2"
    )
    Assert-AcademyRuntimeEnvMutationLock -Region $Region
    # Set this before the API call so a commit-then-timeout cannot release the
    # shared lock while an unobserved refresh may already be active.
    $script:RuntimeEnvMutationLockReleaseAllowed = $false
    $refreshId = & aws autoscaling start-instance-refresh `
        --auto-scaling-group-name $AutoScalingGroupName `
        --preferences MinHealthyPercentage=100,MaxHealthyPercentage=200,InstanceWarmup=120 `
        --region $Region `
        --query InstanceRefreshId `
        --output text
    $normalizedRefreshId = ($refreshId | Out-String).Trim()
    $parsedRefreshId = [Guid]::Empty
    if (
        $LASTEXITCODE -ne 0 -or
        -not [Guid]::TryParse($normalizedRefreshId, [ref]$parsedRefreshId)
    ) {
        throw "Instance refresh start result is ambiguous."
    }
    return $normalizedRefreshId
}

function Wait-AcademyInstanceRefresh {
    param(
        [Parameter(Mandatory = $true)][string]$AutoScalingGroupName,
        [Parameter(Mandatory = $true)][string]$InstanceRefreshId,
        [string]$Region = "ap-northeast-2",
        [ValidateRange(1, 120)][int]$MaxAttempts = 60,
        [ValidateRange(5, 120)][int]$PollSeconds = 30
    )
    try {
        for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
            Renew-AcademyRuntimeEnvMutationLock -Region $Region
            $raw = & aws autoscaling describe-instance-refreshes `
                --auto-scaling-group-name $AutoScalingGroupName `
                --instance-refresh-ids $InstanceRefreshId `
                --region $Region `
                --output json
            if ($LASTEXITCODE -ne 0) {
                throw "Instance refresh readback failed."
            }
            $items = @(($raw | ConvertFrom-Json).InstanceRefreshes)
            if ($items.Count -ne 1) {
                throw "Instance refresh readback did not return exactly one result."
            }
            $status = [string]$items[0].Status
            if ($status -eq "Successful") {
                Assert-AcademyRuntimeEnvMutationLock -Region $Region
                return
            }
            if ($status -in @(
                "Failed",
                "Cancelled",
                "RollbackFailed",
                "RollbackSuccessful"
            )) {
                throw "Instance refresh failed closed with terminal status=$status."
            }
            if ($attempt -lt $MaxAttempts) {
                Start-Sleep -Seconds $PollSeconds
            }
        }
        throw "Instance refresh timed out before terminal success."
    } catch {
        $script:RuntimeEnvMutationLockReleaseAllowed = $false
        throw
    }
}

function Complete-AcademyRuntimeRefreshBoundary {
    param([string]$Region = "ap-northeast-2")
    Assert-AcademyRuntimeEnvMutationLock -Region $Region
    $script:RuntimeEnvMutationLockReleaseAllowed = $true
}

function Assert-AcademyPublicApiHealth {
    foreach ($probe in @(
        @{ Path = "healthz"; Expected = "ok" },
        @{ Path = "health"; Expected = "healthy" }
    )) {
        try {
            $response = Invoke-RestMethod `
                -Uri "https://api.hakwonplus.com/$($probe.Path)" `
                -Method Get `
                -TimeoutSec 15
        } catch {
            $script:RuntimeEnvMutationLockReleaseAllowed = $false
            throw "Public API health readback failed."
        }
        if ([string]$response.status -ne $probe.Expected) {
            $script:RuntimeEnvMutationLockReleaseAllowed = $false
            throw "Public API health readback returned an unexpected status."
        }
    }
}

function Exit-AcademyRuntimeEnvMutationLock {
    param([string]$Region = "ap-northeast-2")
    if (-not $script:RuntimeEnvMutationLockAcquired) { return }
    if (-not $script:RuntimeEnvMutationLockReleaseAllowed) {
        Write-Warning (
            "LOCK_RETAINED owner={0} reason=runtime_forward_convergence_required" -f
            $script:RuntimeEnvMutationLockOwner
        )
        return
    }
    $ownedHere = $script:RuntimeEnvMutationLockOwnedHere
    if ($ownedHere) {
        Invoke-RuntimeEnvLockAction -Action "release" -Region $Region
    } else {
        Invoke-RuntimeEnvLockAction -Action "assert-owned" -Region $Region
    }
    $script:RuntimeEnvMutationLockAcquired = $false
    $script:RuntimeEnvMutationLockOwnedHere = $false
    $script:RuntimeEnvMutationLockOwner = ""
    if ($ownedHere) {
        Remove-Item Env:ACADEMY_RUNTIME_ENV_LOCK_OWNER -ErrorAction SilentlyContinue
    }
}
