# Concurrency lock (SSM) + legacy kill-switch. deploy.ps1 only.
$ErrorActionPreference = "Stop"

$script:DeployLockParamName = "/academy/deploy-lock"
$script:DeployLockMaxAgeSec = 7200

function Get-DeployLockAcquired {
    return $script:DeployLockAcquired -eq $true
}

function Acquire-DeployLock {
    param([string]$Reg)
    if ($script:PlanMode) { return }
    $existing = $null
    try {
        $existing = Invoke-AwsJson @("ssm", "get-parameter", "--name", $script:DeployLockParamName, "--region", $Reg, "--output", "json")
    } catch { }
    $now = [int][double]::Parse((Get-Date -UFormat %s))
    if ($existing -and $existing.Parameter -and $existing.Parameter.Value) {
        $parts = $existing.Parameter.Value -split '\s+'
        $lockTime = 0
        if ($parts.Count -ge 2) { [int]::TryParse($parts[1], [ref]$lockTime) | Out-Null }
        $age = $now - $lockTime
        if ($age -lt $script:DeployLockMaxAgeSec) {
            throw "Deploy lock held (age ${age}s). Another deploy may be in progress. Wait or clear /academy/deploy-lock."
        }
    }
    $val = "$PID $now"
    Invoke-Aws @("ssm", "put-parameter", "--name", $script:DeployLockParamName, "--value", $val, "--type", "String", "--overwrite", "--region", $Reg) -ErrorMessage "put deploy lock" | Out-Null
    $script:DeployLockAcquired = $true
    Write-Ok "Deploy lock acquired"
}

function Release-DeployLock {
    param([string]$Reg)
    if (-not (Get-DeployLockAcquired)) { return }
    try {
        Invoke-Aws @("ssm", "delete-parameter", "--name", $script:DeployLockParamName, "--region", $Reg) -ErrorMessage "delete deploy lock" 2>$null | Out-Null
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
            throw "FORBIDDEN: Do not run scripts/archive. Use scripts/v4 only."
        }
    }
}
