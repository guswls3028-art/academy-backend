# ==============================================================================
# SSOT v3 Full Rebuild — 단일 진입점. Create/Recreate/Drift 수렴. docs/00-SSOT/INFRA-SSOT-V3.* 참조.
# Usage: .\scripts_v3\deploy.ps1 [-Env prod] [-EcrRepoUri ...] [-AllowRebuild] [-SkipNetprobe]
# ==============================================================================
[CmdletBinding()]
param(
    [ValidateSet("prod","staging","dev")]
    [string]$Env = "prod",
    [string]$EcrRepoUri = "",
    [bool]$AllowRebuild = $true,
    [switch]$SkipNetprobe = $false
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ScriptRoot = $PSScriptRoot

Write-Host "`n=== ONE-TAKE DEPLOY START ($Env) [Full Rebuild] ===" -ForegroundColor Cyan

# Env (SSOT values)
. (Join-Path $ScriptRoot "env\prod.ps1")
if ($EcrRepoUri) { $script:EcrRepoUri = $EcrRepoUri } else { $script:EcrRepoUri = "" }
$script:AllowRebuild = $AllowRebuild

# Core
. (Join-Path $ScriptRoot "core\logging.ps1")
. (Join-Path $ScriptRoot "core\aws-wrapper.ps1")
. (Join-Path $ScriptRoot "core\wait.ps1")
. (Join-Path $ScriptRoot "core\preflight.ps1")
. (Join-Path $ScriptRoot "core\evidence.ps1")

# Resources
. (Join-Path $ScriptRoot "resources\iam.ps1")
. (Join-Path $ScriptRoot "resources\batch.ps1")
. (Join-Path $ScriptRoot "resources\jobdef.ps1")
. (Join-Path $ScriptRoot "resources\eventbridge.ps1")
. (Join-Path $ScriptRoot "resources\asg.ps1")
. (Join-Path $ScriptRoot "resources\ssm.ps1")
. (Join-Path $ScriptRoot "resources\api.ps1")

# Netprobe
. (Join-Path $ScriptRoot "netprobe\batch.ps1")

# Sequence (state-contract): IAM -> CE -> Queue -> JobDef -> EventBridge -> Validate -> Netprobe -> Evidence
Invoke-PreflightCheck
$script:BatchIam = Ensure-BatchIAM

Ensure-VideoCE
Ensure-OpsCE
Ensure-VideoQueue
Ensure-OpsQueue

Ensure-VideoJobDef
Ensure-OpsJobDefReconcile
Ensure-OpsJobDefScanStuck
Ensure-OpsJobDefNetprobe

Ensure-EventBridgeRules
Confirm-ASGState
Confirm-SSMEnv
Confirm-APIHealth

$netJobId = ""
$netStatus = ""
if (-not $SkipNetprobe) {
    $net = Invoke-Netprobe -TimeoutSec 1200 -RunnableFailSec 180
    $netJobId = $net.jobId
    $netStatus = $net.status
} else {
    Write-Warn "Netprobe skipped (-SkipNetprobe)"
}

Show-Evidence -NetprobeJobId $netJobId -NetprobeStatus $netStatus

Write-Host "=== ONE-TAKE DEPLOY COMPLETE ===`n" -ForegroundColor Green
