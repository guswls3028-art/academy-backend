# Cross-entrypoint atomic deployment lock (DynamoDB) + legacy kill-switch.
$ErrorActionPreference = "Stop"

$script:DeployLockMaxAgeSec = 10800

function Get-DeployLockAcquired {
    return $script:DeployLockAcquired -eq $true
}

function Acquire-DeployLock {
    param([string]$Reg)
    if ($script:PlanMode) { return }
    $script:DeployLockOwner = if ($env:ACADEMY_DEPLOY_LOCK_OWNER) {
        $env:ACADEMY_DEPLOY_LOCK_OWNER
    } else {
        "manual:$([Environment]::MachineName):${PID}:$([guid]::NewGuid().ToString('N'))"
    }
    $table = if ($script:DynamoLockTableName) { $script:DynamoLockTableName } else { "academy-v1-video-job-lock" }
    $env:AWS_DEFAULT_REGION = $Reg
    & python (Join-Path $PSScriptRoot "..\deployment_lock.py") acquire --owner $script:DeployLockOwner --table-name $table --ttl-seconds $script:DeployLockMaxAgeSec
    if ($LASTEXITCODE -ne 0) { throw "Failed to acquire atomic deployment lock." }
    $script:DeployLockAcquired = $true
    Write-Ok "Deploy lock acquired"
}

function Release-DeployLock {
    param([string]$Reg)
    if (-not (Get-DeployLockAcquired)) { return }
    try {
        $table = if ($script:DynamoLockTableName) { $script:DynamoLockTableName } else { "academy-v1-video-job-lock" }
        $env:AWS_DEFAULT_REGION = $Reg
        & python (Join-Path $PSScriptRoot "..\deployment_lock.py") release --owner $script:DeployLockOwner --table-name $table
        if ($LASTEXITCODE -ne 0) { throw "Lock release failed." }
        Write-Ok "Deploy lock released"
    } catch { Write-Warn "Release lock: $_" }
    $script:DeployLockAcquired = $false
}

# Legacy kill-switch: ensure no scripts/infra or scripts/archive execution. Call from deploy.ps1; -Ci can skip.
function Assert-NoLegacyScripts {
    param([switch]$Ci)
    if ($Ci) { return }
    $stack = Get-PSCallStack
    foreach ($frame in $stack) {
        $path = $frame.InvocationInfo.ScriptName
        if (-not $path) { continue }
        if ($path -match 'scripts[\\/]infra[\\/]') {
            throw "DEPRECATED: Do not run scripts/infra. Use scripts/v1/deploy.ps1 only."
        }
        if ($path -match 'scripts[\\/]archive[\\/]') {
            throw "FORBIDDEN: Do not run scripts/archive. Use scripts/v1 only."
        }
    }
}
