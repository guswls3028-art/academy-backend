# Dedicated homework compatibility runtime; never enables general Video Batch in development.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$VideoImageUri,
    [Parameter(Mandatory = $true)][string]$ReleaseId,
    [Parameter(Mandatory = $true)][int]$WorkersEnvVersion,
    [Parameter(Mandatory = $true)][string]$ProductionDatabaseName,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'core/ssot.ps1')
$configuration = Get-ParamsYaml | ConvertTo-Json -Depth 20 -Compress
$arguments = @((Join-Path $PSScriptRoot 'homework_preview_development.py'),
    '--image', $VideoImageUri, '--release', $ReleaseId,
    '--workers-version', [string]$WorkersEnvVersion, '--production-database', $ProductionDatabaseName,
    '--output', $OutputPath)
if ($Apply) { $arguments += '--apply' }
$configuration | & python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Isolated homework preview runtime/canary failed; candidate must not advance.' }
