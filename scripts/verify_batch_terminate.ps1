# Optional wrapper: run Batch TerminateJob verification (Python script).
# Usage: .\scripts\verify_batch_terminate.ps1
#        .\scripts\verify_batch_terminate.ps1 -JobId "abc12345-..."
#        .\scripts\verify_batch_terminate.ps1 -Region ap-northeast-2

param(
    [string]$Region = "",
    [string]$JobId = "",
    [string]$Profile = "",
    [string]$SettingsModule = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$PyScript = Join-Path $ScriptDir "verify_batch_terminate.py"

if (-not (Test-Path -LiteralPath $PyScript)) {
    Write-Host "ERROR: $PyScript not found." -ForegroundColor Red
    exit 3
}

$args = @()
if ($Region) { $args += "--region"; $args += $Region }
if ($JobId)  { $args += "--job-id"; $args += $JobId }
if ($Profile) { $args += "--profile"; $args += $Profile }
if ($SettingsModule) { $args += "--settings-module"; $args += $SettingsModule }

& python $PyScript @args
exit $LASTEXITCODE
