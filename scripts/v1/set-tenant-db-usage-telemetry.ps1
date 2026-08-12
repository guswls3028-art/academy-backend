# Safely update the production API's sampled tenant DB telemetry settings.
[CmdletBinding()]
param(
    [switch]$Disable,
    [ValidateRange(0.01, 1.0)]
    [double]$SampleRate = 0.1,
    [ValidateRange(100, 60000)]
    [int]$SlowRequestMs = 1000,
    [ValidatePattern('^/academy/api/env$')]
    [string]$ParameterName = "/academy/api/env",
    [switch]$Ci = $false,
    [string]$AwsProfile = "default"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
if ($Ci) {
    Remove-Item Env:AWS_PROFILE -ErrorAction SilentlyContinue
} elseif ($AwsProfile -and $AwsProfile.Trim()) {
    $env:AWS_PROFILE = $AwsProfile.Trim()
}
if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }

$script:PlanMode = $false
. (Join-Path $ScriptRoot "core\env.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
Assert-AwsMutationIdentity | Out-Null

$pythonCommand = Get-Command python3 -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    $pythonCommand = Get-Command python -ErrorAction Stop
}
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "academy-tenant-db-telemetry-" + [Guid]::NewGuid().ToString("N")
)
[void](New-Item -ItemType Directory -Path $tempRoot)
$currentPath = Join-Path $tempRoot "current.txt"
$updatedPath = Join-Path $tempRoot "updated.json"
$readbackPath = Join-Path $tempRoot "readback.txt"
$transformPath = Join-Path $tempRoot "transform.py"
$verifyPath = Join-Path $tempRoot "verify.py"

try {
    $raw = & aws ssm get-parameter `
        --name $ParameterName `
        --with-decryption `
        --region $env:AWS_DEFAULT_REGION `
        --query Parameter.Value `
        --output text
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($raw)) {
        throw "Production API environment parameter is missing or unreadable."
    }
    [System.IO.File]::WriteAllText($currentPath, [string]$raw)

    @'
import json, sys
source, destination, enabled, sample_rate, slow_ms = sys.argv[1:]
raw = open(source, encoding="utf-8").read().strip()
data = json.loads(raw)
if not isinstance(data, dict) or len(data) < 50:
    raise SystemExit("production API environment invariant failed")
if data.get("DJANGO_SETTINGS_MODULE") != "apps.api.config.settings.prod":
    raise SystemExit("production settings invariant failed")
before = len(data)
data["TENANT_DB_USAGE_ENABLED"] = enabled
data["TENANT_DB_USAGE_SAMPLE_RATE"] = sample_rate
data["TENANT_DB_USAGE_SLOW_REQUEST_MS"] = slow_ms
if len(data) < before:
    raise SystemExit("environment key count regressed")
open(destination, "w", encoding="utf-8").write(
    json.dumps(data, ensure_ascii=False, separators=(",", ":"))
)
print(f"TENANT_DB_TELEMETRY_TRANSFORM_PASS keys_before={before} keys_after={len(data)}")
'@ | Set-Content -LiteralPath $transformPath -Encoding utf8

    $enabledValue = if ($Disable) { "false" } else { "true" }
    & $pythonCommand.Source $transformPath $currentPath $updatedPath `
        $enabledValue $SampleRate.ToString([Globalization.CultureInfo]::InvariantCulture) `
        $SlowRequestMs.ToString()
    if ($LASTEXITCODE -ne 0) { throw "Telemetry environment transform failed." }

    $version = & aws ssm put-parameter `
        --name $ParameterName `
        --type SecureString `
        --value "file://$updatedPath" `
        --overwrite `
        --region $env:AWS_DEFAULT_REGION `
        --query Version `
        --output text
    if ($LASTEXITCODE -ne 0 -or -not $version) {
        throw "Telemetry environment publication failed."
    }

    $readback = & aws ssm get-parameter `
        --name "${ParameterName}:$version" `
        --with-decryption `
        --region $env:AWS_DEFAULT_REGION `
        --query Parameter.Value `
        --output text
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($readback)) {
        throw "Versioned telemetry environment readback failed."
    }
    [System.IO.File]::WriteAllText($readbackPath, [string]$readback)

    @'
import json, sys
path, enabled, sample_rate, slow_ms = sys.argv[1:]
data = json.loads(open(path, encoding="utf-8").read())
expected = {
    "TENANT_DB_USAGE_ENABLED": enabled,
    "TENANT_DB_USAGE_SAMPLE_RATE": sample_rate,
    "TENANT_DB_USAGE_SLOW_REQUEST_MS": slow_ms,
}
for key, value in expected.items():
    if str(data.get(key, "")) != value:
        raise SystemExit(f"readback mismatch: {key}")
if data.get("DJANGO_SETTINGS_MODULE") != "apps.api.config.settings.prod":
    raise SystemExit("production settings readback mismatch")
print("TENANT_DB_TELEMETRY_READBACK_PASS configured=true")
'@ | Set-Content -LiteralPath $verifyPath -Encoding utf8
    & $pythonCommand.Source $verifyPath $readbackPath $enabledValue `
        $SampleRate.ToString([Globalization.CultureInfo]::InvariantCulture) `
        $SlowRequestMs.ToString()
    if ($LASTEXITCODE -ne 0) { throw "Versioned telemetry readback validation failed." }

    Write-Host (
        "TENANT_DB_TELEMETRY_READY parameter={0} version={1} enabled={2} sample_rate={3} slow_ms={4}" -f
        $ParameterName,
        $version,
        $enabledValue,
        $SampleRate.ToString([Globalization.CultureInfo]::InvariantCulture),
        $SlowRequestMs
    ) -ForegroundColor Green
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        [System.IO.Directory]::Delete($tempRoot, $true)
    }
}
