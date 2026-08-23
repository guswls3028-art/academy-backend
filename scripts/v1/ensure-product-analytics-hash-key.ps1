# Ensure the production API environment has a dedicated product-analytics HMAC
# key. The key remains inside the SecureString value and is never printed.
[CmdletBinding()]
param(
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
. (Join-Path $ScriptRoot "core\runtime-env-lock.ps1")
Enter-AcademyRuntimeEnvMutationLock `
    -Region $env:AWS_DEFAULT_REGION `
    -OwnerPrefix "product-analytics-hash-key"

try {
$current = Invoke-AwsJson @(
    "ssm", "get-parameter",
    "--name", $ParameterName,
    "--with-decryption",
    "--region", $env:AWS_DEFAULT_REGION,
    "--output", "json"
)
if (-not $current -or -not $current.Parameter -or -not $current.Parameter.Value) {
    throw "Production API environment parameter is missing or unreadable."
}
try {
    $environment = [string]$current.Parameter.Value | ConvertFrom-Json
} catch {
    throw "Production API environment parameter must contain a JSON object."
}
if ([string]$environment.DJANGO_SETTINGS_MODULE -ne "apps.api.config.settings.prod") {
    throw "Product analytics HMAC key may only be added to the production API settings environment."
}

$existing = [string]$environment.PRODUCT_ANALYTICS_HASH_KEY
$changed = $false
if (-not $existing) {
    $bytes = [byte[]]::new(48)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $generated = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
    $environment | Add-Member -NotePropertyName "PRODUCT_ANALYTICS_HASH_KEY" -NotePropertyValue $generated -Force
    $changed = $true
} elseif ($existing.Length -lt 32) {
    throw "Existing product analytics HMAC key is too short; refusing automatic replacement."
}

$version = [int]$current.Parameter.Version
if ($changed) {
    $value = $environment | ConvertTo-Json -Compress -Depth 20
    Assert-AcademyRuntimeEnvMutationLock -Region $env:AWS_DEFAULT_REGION
    $put = Invoke-AwsJson @(
        "ssm", "put-parameter",
        "--name", $ParameterName,
        "--type", "SecureString",
        "--value", $value,
        "--overwrite",
        "--region", $env:AWS_DEFAULT_REGION,
        "--output", "json"
    )
    if (-not $put -or -not $put.Version) {
        throw "Product analytics HMAC key publication returned no parameter version."
    }
    $version = [int]$put.Version
}

$readback = Invoke-AwsJson @(
    "ssm", "get-parameter",
    "--name", "${ParameterName}:$version",
    "--with-decryption",
    "--region", $env:AWS_DEFAULT_REGION,
    "--output", "json"
)
if (-not $readback -or -not $readback.Parameter -or -not $readback.Parameter.Value) {
    throw "Versioned product analytics HMAC key readback failed."
}
$actual = [string]$readback.Parameter.Value | ConvertFrom-Json
$actualKey = [string]$actual.PRODUCT_ANALYTICS_HASH_KEY
if ($actualKey.Length -lt 32 -or [string]$actual.DJANGO_SETTINGS_MODULE -ne "apps.api.config.settings.prod") {
    throw "Versioned product analytics HMAC key readback failed validation."
}

Write-Host (
    "PRODUCT_ANALYTICS_HASH_KEY_READY parameter={0} version={1} changed={2} configured=true" -f
    $ParameterName,
    $version,
    $changed.ToString().ToLowerInvariant()
) -ForegroundColor Green
} finally {
    Exit-AcademyRuntimeEnvMutationLock -Region $env:AWS_DEFAULT_REGION
}
