# Converge the persistent development database through the same fail-closed
# role/database boundary used by the isolated API canary bootstrap.
[CmdletBinding()]
param(
    [ValidateRange(60, 600)]
    [int]$TimeoutSec = 300,
    [string]$AwsProfile = "default"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$script:PlanMode = $false
. (Join-Path $ScriptRoot "core\ssot.ps1")
Load-SSOT -Env prod | Out-Null

& (Join-Path $ScriptRoot "converge-api-preprod-database.ps1") `
    -CredentialParameter $script:ApiDevelopmentCredentialParameter `
    -PreprodDatabaseName $script:ApiDevelopmentDatabaseName `
    -PreprodDatabaseUser $script:ApiDevelopmentDatabaseUser `
    -TimeoutSec $TimeoutSec `
    -AwsProfile $AwsProfile
