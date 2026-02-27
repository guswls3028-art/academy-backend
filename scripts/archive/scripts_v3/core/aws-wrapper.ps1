# AWS CLI wrapper. Same as legacy scripts/infra batch_ops_setup.ps1: & aws @ArgsArray 2>&1.
# Do not use Start-Process so inner quotes in args are preserved (e.g. --compute-environment-order JSON).
function Invoke-AwsJson {
    param([string[]]$ArgsArray)
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
