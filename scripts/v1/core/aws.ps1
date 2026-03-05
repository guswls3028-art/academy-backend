# AWS CLI wrapper. No Start-Process; & aws @ArgsArray to preserve quoting.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
# When AWS_PROFILE is set, --profile is injected so subprocess uses the same credentials.
# In Plan mode: read-only (describe/get/list) run normally; mutating commands are skipped and return $null / no throw.

$script:AwsMutatingVerbs = @(
    'create', 'update', 'delete', 'put', 'register', 'deregister',
    'attach', 'detach', 'modify', 'authorize', 'revoke',
    'terminate', 'release', 'start', 'stop', 'add-', 'remove-', 'set-'
)

function Get-AwsArgsWithProfile {
    param([string[]]$ArgsArray)
    if (-not $ArgsArray -or $ArgsArray.Count -lt 1) { return $ArgsArray }
    $out = [System.Collections.ArrayList]::new()
    $hasProfile = $false
    $hasRegion = $false
    foreach ($a in $ArgsArray) {
        if ($a -eq '--profile') { $hasProfile = $true }
        if ($a -eq '--region') { $hasRegion = $true }
    }
    [void]$out.Add($ArgsArray[0])  # aws
    if ($ArgsArray.Count -ge 2) { [void]$out.Add($ArgsArray[1]) }  # service
    if ($env:AWS_PROFILE -and $env:AWS_PROFILE.Trim() -ne '' -and -not $hasProfile) {
        [void]$out.Add('--profile')
        [void]$out.Add($env:AWS_PROFILE.Trim())
    }
    if ($env:AWS_DEFAULT_REGION -and $env:AWS_DEFAULT_REGION.Trim() -ne '' -and -not $hasRegion) {
        [void]$out.Add('--region')
        [void]$out.Add($env:AWS_DEFAULT_REGION.Trim())
    }
    for ($i = 2; $i -lt $ArgsArray.Count; $i++) { [void]$out.Add($ArgsArray[$i]) }
    return $out
}

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
    $fullArgs = Get-AwsArgsWithProfile -ArgsArray $ArgsArray
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @fullArgs 2>&1
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
    $fullArgs = Get-AwsArgsWithProfile -ArgsArray $ArgsArray
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @fullArgs 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) {
        $text = ($out | Out-String).Trim()
        if (-not $text) { $text = "no output" }
        throw "${ErrorMessage}. ExitCode=$exit. Output: $text"
    }
    return $out
}
