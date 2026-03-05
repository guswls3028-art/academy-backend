# AWS CLI wrapper. No Start-Process; & aws @ArgsArray to preserve quoting.
# In Plan mode: read-only (describe/get/list) run normally; mutating commands are skipped and return $null / no throw.

$script:AwsMutatingVerbs = @(
    'create', 'update', 'delete', 'put', 'register', 'deregister',
    'attach', 'detach', 'modify', 'authorize', 'revoke',
    'terminate', 'release', 'start', 'stop', 'add-', 'remove-', 'set-'
)

function Test-AwsArgsMutating {
    param([string[]]$ArgsArray)
    if (-not $ArgsArray -or $ArgsArray.Count -lt 2) { return $false }
    $verb = $ArgsArray[1] -replace '^aws\s+', ''
    foreach ($m in $script:AwsMutatingVerbs) {
        if ($verb -like "${m}*") { return $true }
    }
    return $false
}

function Invoke-AwsJson {
    param([string[]]$ArgsArray)
    if ($script:PlanMode -and (Test-AwsArgsMutating -ArgsArray $ArgsArray)) {
        return $null
    }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @ArgsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    if (-not $out) { return $null }
    try {
        $str = ($out | Out-String).Trim()
        if (-not $str) { return $null }
        return $str | ConvertFrom-Json
    } catch { return $null }
}

function Invoke-Aws {
    param([string[]]$ArgsArray, [string]$ErrorMessage = "AWS CLI failed")
    if ($script:PlanMode -and (Test-AwsArgsMutating -ArgsArray $ArgsArray)) {
        return $null
    }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @ArgsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) {
        $text = ($out | Out-String).Trim()
        if (-not $text) { $text = "no output" }
        throw "${ErrorMessage}. ExitCode=$exit. Output: $text"
    }
    return $out
}
