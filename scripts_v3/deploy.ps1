# ==============================================================================
# SSOT v3 One-Take Deploy — 단일 진입점. docs/00-SSOT/INFRA-SSOT-V3.* 참조.
# Usage: .\scripts_v3\deploy.ps1 [-Env prod] [-SkipNetprobe]
# ==============================================================================
[CmdletBinding()]
param(
    [ValidateSet("prod","staging","dev")]
    [string]$Env = "prod",
    [switch]$SkipNetprobe = $false
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ScriptRoot = $PSScriptRoot

Write-Host "`n=== ONE-TAKE DEPLOY START ($Env) ===" -ForegroundColor Cyan

# Env (SSOT values)
. (Join-Path $ScriptRoot "env\prod.ps1")

# Core
. (Join-Path $ScriptRoot "core\logging.ps1")
. (Join-Path $ScriptRoot "core\aws-wrapper.ps1")
. (Join-Path $ScriptRoot "core\wait.ps1")
. (Join-Path $ScriptRoot "core\preflight.ps1")
. (Join-Path $ScriptRoot "core\evidence.ps1")

# Resources
. (Join-Path $ScriptRoot "resources\batch.ps1")
. (Join-Path $ScriptRoot "resources\eventbridge.ps1")
. (Join-Path $ScriptRoot "resources\asg.ps1")
. (Join-Path $ScriptRoot "resources\ssm.ps1")
. (Join-Path $ScriptRoot "resources\api.ps1")

# Netprobe
. (Join-Path $ScriptRoot "netprobe\batch.ps1")

# Sequence (state-contract)
Invoke-PreflightCheck

Ensure-VideoCE
Ensure-OpsCE
Ensure-VideoQueue
Ensure-OpsQueue
Ensure-EventBridgeRules
Confirm-ASGState
Confirm-SSMEnv
Confirm-APIHealth

$netJobId = ""
$netStatus = ""
if (-not $SkipNetprobe) {
    try {
        $net = Invoke-Netprobe
        $netJobId = $net.jobId
        $netStatus = $net.status
    } catch {
        Write-Host "Netprobe failed: $_" -ForegroundColor Red
        $netStatus = "FAILED"
        Show-Evidence -NetprobeJobId $netJobId -NetprobeStatus $netStatus
        throw
    }
} else {
    Write-Warn "Netprobe skipped (-SkipNetprobe)"
}

Show-Evidence -NetprobeJobId $netJobId -NetprobeStatus $netStatus

Write-Host "=== ONE-TAKE DEPLOY COMPLETE ===`n" -ForegroundColor Green
