function Invoke-AwsJson {
    param([string[]]$ArgsArray)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @ArgsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0 -or -not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}
function Invoke-Aws {
    param([string[]]$ArgsArray, [string]$ErrorMessage = "AWS CLI failed")
    $out = & aws @ArgsArray 2>&1
    if ($LASTEXITCODE -ne 0) {
        $text = ($out | Out-String).Trim()
        throw "${ErrorMessage}. ExitCode=$LASTEXITCODE. Output: $text"
    }
    return $out
}
