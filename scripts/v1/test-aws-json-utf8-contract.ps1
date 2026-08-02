$ErrorActionPreference = "Stop"

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) "academy-aws-utf8-$([guid]::NewGuid().ToString('N'))"
$originalPythonIoEncoding = $env:PYTHONIOENCODING
$originalPythonUtf8 = $env:PYTHONUTF8
$originalConsoleEncoding = [Console]::OutputEncoding
$originalAwsFunction = Get-Item Function:\global:aws -ErrorAction SilentlyContinue

try {
    New-Item -ItemType Directory -Path $tempRoot | Out-Null
    $fakeAwsPython = @'
import json

print(json.dumps({
    "Parameter": {
        "Value": "카카오뱅크 / 유현진 / ī",
        "Version": 77,
    }
}, ensure_ascii=False))
'@
    [IO.File]::WriteAllText(
        (Join-Path $tempRoot "fake_aws.py"),
        $fakeAwsPython,
        [Text.UTF8Encoding]::new($false)
    )
    $script:AwsUtf8TestPython = (Get-Command python -ErrorAction Stop).Source
    $script:AwsUtf8TestScript = Join-Path $tempRoot "fake_aws.py"
    function global:aws {
        & $script:AwsUtf8TestPython $script:AwsUtf8TestScript
    }
    $env:PYTHONIOENCODING = $null
    $env:PYTHONUTF8 = $null
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    . (Join-Path $PSScriptRoot "core\aws.ps1")

    $expected = "카카오뱅크 / 유현진 / ī"
    $json = Invoke-AwsJson @("ssm", "get-parameter", "--output", "json")
    if (-not $json -or [string]$json.Parameter.Value -ne $expected) {
        throw "Invoke-AwsJson must preserve UTF-8 native output exactly."
    }
    if ($null -ne $env:PYTHONIOENCODING -or $null -ne $env:PYTHONUTF8) {
        throw "Invoke-AwsJson must restore Python encoding environment variables."
    }

    $raw = Invoke-Aws @("ssm", "get-parameter", "--output", "json")
    $parsed = (($raw | Out-String).Trim() | ConvertFrom-Json)
    if ([string]$parsed.Parameter.Value -ne $expected) {
        throw "Invoke-Aws must preserve UTF-8 native output exactly."
    }
    if ($null -ne $env:PYTHONIOENCODING -or $null -ne $env:PYTHONUTF8) {
        throw "Invoke-Aws must restore Python encoding environment variables."
    }

    Write-Host "AWS_JSON_UTF8_CONTRACT_PASS" -ForegroundColor Green
} finally {
    $env:PYTHONIOENCODING = $originalPythonIoEncoding
    $env:PYTHONUTF8 = $originalPythonUtf8
    [Console]::OutputEncoding = $originalConsoleEncoding
    Remove-Item Function:\global:aws -ErrorAction SilentlyContinue
    if ($originalAwsFunction) {
        Set-Item Function:\global:aws $originalAwsFunction.ScriptBlock
    }
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
