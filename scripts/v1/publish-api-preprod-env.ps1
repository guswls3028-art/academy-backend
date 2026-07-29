# Publish a release-bound, versioned API canary environment. Secret values are
# read and written in process memory and are never emitted to logs or outputs.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^sha-[0-9a-fA-F]{40}-run-[0-9]+-[0-9]+$')]
    [string]$ReleaseId,
    [ValidatePattern('^/academy/api/env$')]
    [string]$ProductionEnvParameter = "/academy/api/env",
    [ValidatePattern('^/academy/api/preprod/db-credentials$')]
    [string]$PreprodCredentialParameter = "/academy/api/preprod/db-credentials",
    [ValidatePattern('^/academy/api/preprod/env$')]
    [string]$PreprodEnvParameter = "/academy/api/preprod/env",
    [ValidatePattern('^[a-z][a-z0-9_]{2,62}$')]
    [string]$PreprodDatabaseName = "academy_api_preprod",
    [ValidatePattern('^[a-z][a-z0-9_]{2,62}$')]
    [string]$PreprodDatabaseUser = "academy_api_preprod_app",
    [string]$GithubOutputPath = "",
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

function Get-RequiredSecureParameterValue {
    param([string]$Name)
    $result = Invoke-AwsJson @(
        "ssm", "get-parameter",
        "--name", $Name,
        "--with-decryption",
        "--region", $env:AWS_DEFAULT_REGION,
        "--output", "json"
    )
    if (-not $result -or -not $result.Parameter -or -not $result.Parameter.Value) {
        throw "Required secure parameter is missing or unreadable: $Name"
    }
    return [string]$result.Parameter.Value
}

$productionEnvValue = Get-RequiredSecureParameterValue -Name $ProductionEnvParameter
$credentialValue = Get-RequiredSecureParameterValue -Name $PreprodCredentialParameter
try {
    $production = $productionEnvValue | ConvertFrom-Json
    $credential = $credentialValue | ConvertFrom-Json
} catch {
    throw "API preprod source parameters must contain valid JSON objects."
}

$settingsModule = [string]$production.DJANGO_SETTINGS_MODULE
$productionDatabaseName = [string]$production.DB_NAME
$productionDatabaseUser = [string]$production.DB_USER
$credentialUser = [string]$credential.DB_USER
$credentialPassword = [string]$credential.DB_PASSWORD
if ($settingsModule -ne "apps.api.config.settings.prod") {
    throw "Production API env does not select the production settings module."
}
if (-not $productionDatabaseName -or $productionDatabaseName -eq $PreprodDatabaseName) {
    throw "Production and preprod database names must be distinct."
}
if ($credentialUser -ne $PreprodDatabaseUser) {
    throw "Preprod credential parameter is not bound to the dedicated database role."
}
if (-not $productionDatabaseUser -or $productionDatabaseUser -eq $credentialUser) {
    throw "Production and preprod database users must be distinct."
}
if (-not $credentialPassword -or $credentialPassword.Length -lt 32) {
    throw "Preprod database password is missing or too short."
}

$production | Add-Member -NotePropertyName "DB_NAME" -NotePropertyValue $PreprodDatabaseName -Force
$production | Add-Member -NotePropertyName "DB_USER" -NotePropertyValue $credentialUser -Force
$production | Add-Member -NotePropertyName "DB_PASSWORD" -NotePropertyValue $credentialPassword -Force
$production | Add-Member -NotePropertyName "ACADEMY_PREPROD_RELEASE_ID" -NotePropertyValue $ReleaseId -Force
$value = $production | ConvertTo-Json -Compress -Depth 20
$put = Invoke-AwsJson @(
    "ssm", "put-parameter",
    "--name", $PreprodEnvParameter,
    "--type", "SecureString",
    "--tier", "Advanced",
    "--value", $value,
    "--overwrite",
    "--region", $env:AWS_DEFAULT_REGION,
    "--output", "json"
)
if (-not $put -or -not $put.Version) {
    throw "API preprod env publication returned no parameter version."
}
$version = [int]$put.Version

$readback = Invoke-AwsJson @(
    "ssm", "get-parameter",
    "--name", "${PreprodEnvParameter}:$version",
    "--with-decryption",
    "--region", $env:AWS_DEFAULT_REGION,
    "--output", "json"
)
if (-not $readback -or -not $readback.Parameter -or -not $readback.Parameter.Value) {
    throw "Versioned API preprod env readback failed."
}
$actual = [string]$readback.Parameter.Value | ConvertFrom-Json
if (
    [string]$actual.ACADEMY_PREPROD_RELEASE_ID -ne $ReleaseId -or
    [string]$actual.DB_NAME -ne $PreprodDatabaseName -or
    [string]$actual.DB_USER -ne $credentialUser -or
    [string]$actual.DB_PASSWORD -ne $credentialPassword -or
    [string]$actual.DJANGO_SETTINGS_MODULE -ne "apps.api.config.settings.prod"
) {
    throw "Versioned API preprod env readback does not match the release boundary."
}

$safeOutputs = [ordered]@{
    parameter_name = $PreprodEnvParameter
    parameter_version = [string]$version
    release_id = $ReleaseId
    preprod_database_name = $PreprodDatabaseName
    production_database_name = $productionDatabaseName
    preprod_database_user = $credentialUser
}
if ($GithubOutputPath) {
    foreach ($entry in $safeOutputs.GetEnumerator()) {
        Add-Content -LiteralPath $GithubOutputPath -Value "$($entry.Key)=$($entry.Value)"
    }
}
Write-Host (
    "API_PREPROD_ENV_PUBLISHED parameter={0} version={1} release={2} database={3} role={4}" -f
    $PreprodEnvParameter,
    $version,
    $ReleaseId,
    $PreprodDatabaseName,
    $credentialUser
) -ForegroundColor Green
