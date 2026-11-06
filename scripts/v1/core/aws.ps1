# AWS CLI wrapper. No Start-Process; & aws @ArgsArray to preserve quoting.
# AWS/Cloudflare credentials are supplied by the caller through the intended profile or process environment; this script does not load backend/.env.
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

function Convert-JsonArgToFileRef {
    <#
    .SYNOPSIS
    JSON 문자열을 file:// 임시파일 참조로 변환.
    Windows PowerShell에서 aws CLI splatting 시 JSON 큰따옴표가 소실되는 문제 해결.
    사용: $prefs = Convert-JsonArgToFileRef $jsonString
    반환: "file://C:\...\tmpXXXX.tmp"
    호출자가 사용 후 tmpFile 삭제 필요 (또는 시스템이 정리).
    #>
    param([string]$JsonString)
    if (-not $JsonString -or $JsonString -match '^file://') { return $JsonString }
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $JsonString, [System.Text.UTF8Encoding]::new($false))
    return "file://$tmp"
}

function Remove-TempFiles {
    param($TempFiles)
    if (-not $TempFiles) { return }
    foreach ($f in $TempFiles) {
        Remove-Item $f -ErrorAction SilentlyContinue 2>$null
    }
}

function Invoke-AwsJson {
    param([string[]]$ArgsArray)
    if ($script:PlanMode -and (Test-AwsArgsMutating -ArgsArray $ArgsArray)) {
        return $null
    }
    $fullArgs = Get-AwsArgsWithProfile -ArgsArray $ArgsArray
    $prev = $ErrorActionPreference
    $prevPythonIoEncoding = $env:PYTHONIOENCODING
    $prevPythonUtf8 = $env:PYTHONUTF8
    $ErrorActionPreference = "Continue"
    try {
        # AWS CLI v2 inherits the Windows console code page unless Python's
        # native stdout encoding is explicit. PowerShell 7 decodes native
        # output as UTF-8 here, so a CP949 payload silently corrupts Hangul
        # before ConvertFrom-Json and can then be written back to SSM.
        $env:PYTHONIOENCODING = "utf-8"
        $env:PYTHONUTF8 = "1"
        $out = & aws @fullArgs 2>&1
        $exit = $LASTEXITCODE
    } finally {
        $env:PYTHONIOENCODING = $prevPythonIoEncoding
        $env:PYTHONUTF8 = $prevPythonUtf8
        $ErrorActionPreference = $prev
    }
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
    $prevPythonIoEncoding = $env:PYTHONIOENCODING
    $prevPythonUtf8 = $env:PYTHONUTF8
    $ErrorActionPreference = "Continue"
    try {
        $env:PYTHONIOENCODING = "utf-8"
        $env:PYTHONUTF8 = "1"
        $out = & aws @fullArgs 2>&1
        $exit = $LASTEXITCODE
    } finally {
        $env:PYTHONIOENCODING = $prevPythonIoEncoding
        $env:PYTHONUTF8 = $prevPythonUtf8
        $ErrorActionPreference = $prev
    }
    if ($exit -ne 0) {
        $text = ($out | Out-String).Trim()
        if (-not $text) { $text = "no output" }
        throw "${ErrorMessage}. ExitCode=$exit. Output: $text"
    }
    return $out
}

function Invoke-SsmSecureStringPut {
    # Keep Value inside this process and the child's stdin, never CLI arguments.
    param(
        [Parameter(Mandatory = $true)][hashtable]$Request,
        [string]$Region = $env:AWS_DEFAULT_REGION,
        [ValidateRange(1, 300)][int]$TimeoutSeconds = 90
    )
    if ($script:PlanMode) { return $null }
    $process = $null
    $started = $false
    $deadline = [Threading.CancellationTokenSource]::new()
    $deadline.CancelAfter($TimeoutSeconds * 1000)
    $failureCode = 'SSM_SECURE_PUT_FAILED'
    try {
        $python = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $python) {
            $python = Get-Command python3 -CommandType Application -ErrorAction Stop | Select-Object -First 1
        }
        $info = [Diagnostics.ProcessStartInfo]::new()
        $info.FileName = $python.Source
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardInput = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
        $info.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
        $info.Environment['PYTHONUTF8'] = '1'
        $info.Environment['PYTHONIOENCODING'] = 'utf-8'
        $info.ArgumentList.Add('-B')
        $info.ArgumentList.Add((Join-Path $PSScriptRoot '..\ssm_secure_put.py'))
        $process = [Diagnostics.Process]::new()
        $process.StartInfo = $info
        $deadline.Token.ThrowIfCancellationRequested()
        [void]$process.Start()
        $started = $true
        $stdoutTask = $process.StandardOutput.ReadToEndAsync($deadline.Token)
        $stderrTask = $process.StandardError.ReadToEndAsync($deadline.Token)
        $exitTask = $process.WaitForExitAsync($deadline.Token)
        $envelope = @{
            request = $Request
            region = $Region
            profile = $env:AWS_PROFILE
        } | ConvertTo-Json -Depth 10 -Compress
        $bytes = [Text.Encoding]::UTF8.GetBytes($envelope)
        $writeTask = $process.StandardInput.BaseStream.WriteAsync($bytes, 0, $bytes.Length, $deadline.Token)
        [void]$writeTask.WaitAsync($deadline.Token).GetAwaiter().GetResult()
        $process.StandardInput.Close()
        [void]$exitTask.WaitAsync($deadline.Token).GetAwaiter().GetResult()
        # A descendant may keep the output pipes open after the child exits.
        # Every wait shares the original deadline, including stdin backpressure.
        $stdout = $stdoutTask.WaitAsync($deadline.Token).GetAwaiter().GetResult()
        $stderr = $stderrTask.WaitAsync($deadline.Token).GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) {
            $knownCodes = @('SSM_SECURE_PUT_INVALID_REQUEST', 'SSM_SECURE_PUT_SDK_UNAVAILABLE', 'SSM_SECURE_PUT_INVALID_RESPONSE')
            if ($stderr.Trim() -cin $knownCodes) { $failureCode = $stderr.Trim() }
            throw 'child failed'
        }
        $failureCode = 'SSM_SECURE_PUT_INVALID_RESPONSE'
        $result = $stdout | ConvertFrom-Json -ErrorAction Stop
        $names = @($result.PSObject.Properties.Name)
        if (@($names | Where-Object { $_ -cnotin @('Version', 'Tier') }).Count -or
            $result.Version -isnot [long] -and $result.Version -isnot [int] -or
            $result.Version -lt 1 -or
            ($names -ccontains 'Tier' -and $result.Tier -cnotin @('Standard', 'Advanced', 'Intelligent-Tiering'))) {
            throw 'invalid child metadata'
        }
        $deadline.Token.ThrowIfCancellationRequested()
        return $result
    } catch {
        # Never include child output, request JSON or the original exception.
        if ($deadline.IsCancellationRequested) { $failureCode = 'SSM_SECURE_PUT_TIMEOUT' }
        throw $failureCode
    } finally {
        $deadline.Cancel()
        if ($process) {
            # Termination is best effort; never add an unbounded cleanup wait.
            try { if ($started -and -not $process.HasExited) { $process.Kill($true) } } catch {}
            $process.Dispose()
        }
        $deadline.Dispose()
    }
}
