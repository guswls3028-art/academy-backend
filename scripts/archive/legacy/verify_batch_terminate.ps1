# Optional wrapper: run Batch TerminateJob verification (Python script).
# Usage: .\scripts\verify_batch_terminate.ps1
#        .\scripts\verify_batch_terminate.ps1 -JobId "YOUR_AWS_BATCH_JOB_ID"
#        .\scripts\verify_batch_terminate.ps1 -Region ap-northeast-2
# Note: Use quotes for -JobId; PowerShell treats < and > as redirection.

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

$pyArgs = @()
if ($Region) { $pyArgs += "--region"; $pyArgs += $Region }
if ($JobId)  { $pyArgs += "--job-id"; $pyArgs += $JobId }
if ($Profile) { $pyArgs += "--profile"; $pyArgs += $Profile }
if ($SettingsModule) { $pyArgs += "--settings-module"; $pyArgs += $SettingsModule }

& python $PyScript @pyArgs
exit $LASTEXITCODE
