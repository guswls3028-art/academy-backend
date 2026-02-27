# ==============================================================================
# SSOT v3 FullStack — single entry point. API/Build/ASG/Batch/Ops/RDS/Redis/SSM/ECR/EventBridge/Network.
# Usage: .\scripts_v3\deploy_fullstack.ps1 [-Env prod] [-AllowRebuild] [-SkipNetprobe] [-PruneLegacy] [-DryRun]
# -DryRun: print DELETE CANDIDATE table only then exit.
# -PruneLegacy: delete non-SSOT resources -> Wait -> FullStack Ensure -> Netprobe -> Evidence.
# ==============================================================================
[CmdletBinding()]
param(
    [ValidateSet("prod","staging","dev")]
    [string]$Env = "prod",
    [bool]$AllowRebuild = $true,
    [switch]$SkipNetprobe = $false,
    [switch]$PruneLegacy = $false,
    [switch]$DryRun = $false
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ScriptRoot = $PSScriptRoot

Write-Host "`n=== FULLSTACK DEPLOY START ($Env) ===" -ForegroundColor Cyan
if ($DryRun) { Write-Host "MODE: DryRun (DELETE CANDIDATE table only)" -ForegroundColor Yellow }
if ($PruneLegacy -and -not $DryRun) { Write-Host "MODE: PruneLegacy (delete then Ensure)" -ForegroundColor Yellow }

. (Join-Path $ScriptRoot "env\prod.ps1")
$script:AllowRebuild = $AllowRebuild

# Core
. (Join-Path $ScriptRoot "core\logging.ps1")
. (Join-Path $ScriptRoot "core\aws-wrapper.ps1")
. (Join-Path $ScriptRoot "core\ssot_canonical.ps1")
. (Join-Path $ScriptRoot "core\prune.ps1")
. (Join-Path $ScriptRoot "core\wait.ps1")
. (Join-Path $ScriptRoot "core\drift.ps1")
. (Join-Path $ScriptRoot "core\preflight.ps1")
. (Join-Path $ScriptRoot "core\evidence.ps1")

# DryRun: DELETE CANDIDATE table + Drift table + delete order/risk, then exit
if ($DryRun) {
    Set-SSOTCanonicalLists | Out-Null
    $all = Get-AllAwsResources
    $candidates = Get-DeleteCandidates -All $all
    $count = Show-DeleteCandidateTable -Candidates $candidates
    $driftRows = Get-StructuralDrift
    Show-StructuralDriftTable -Rows $driftRows | Out-Null
    Write-Host "`n--- Delete order (when PruneLegacy) ---" -ForegroundColor Cyan
    Write-Host "  EventBridge -> Queue -> CE -> JobDef -> ASG -> ECS cluster -> IAM -> EIP"
    Write-Host "  (After each delete: describe polling Wait only. No fixed sleep.)"
    Write-Host "  Details: docs/00-SSOT/PRUNE-DELETE-ORDER-AND-RISKS.md"
    Write-Host "1. EventBridge (remove targets then delete rule)"
    Write-Host "2. Batch Queue (DISABLED -> delete)"
    Write-Host "3. Batch CE (DISABLED -> delete -> Wait)"
    Write-Host "4. Batch JobDef (deregister, non-SSOT names only)"
    Write-Host "5. ASG (min=0 desired=0 -> force-delete)"
    Write-Host "6. ECS Cluster (delete -> Wait)"
    Write-Host "7. IAM Role (detach/inline delete then delete-role)"
    Write-Host "8. EIP (release unassociated only)"
    Write-Host "`n--- Risks ---" -ForegroundColor Yellow
    Write-Host "- EventBridge: schedule leftovers removed. Canonical 2 rules kept."
    Write-Host "- Queue/CE: Jobs using that queue/CE may fail on delete. Wait for jobs to finish before Prune."
    Write-Host "- JobDef: Deregistering old revision fails jobs using it. Only non-SSOT names deleted."
    Write-Host "- IAM: academy-* roles except Batch/EventBridge deleted. If API/Build use academy-* add to exclude list."
    Write-Host "- ASG: Batch CE ASGs excluded by name pattern. Only Messaging/AI academy ASGs are targets."
    Write-Host "- ECS: Batch-owned clusters may fail delete (ignored). Manual clusters only."
    Write-Host "- EIP: Associated EIPs excluded from list. Release unassociated only."
    Write-Host "`n=== DRY RUN COMPLETE ===`n" -ForegroundColor Green
    exit 0
}

# PruneLegacy: run deletes then FullStack Ensure (Wait-* after each delete, no fixed sleep)
if ($PruneLegacy) {
    Set-SSOTCanonicalLists | Out-Null
    $all = Get-AllAwsResources
    $candidates = Get-DeleteCandidates -All $all
    $count = Show-DeleteCandidateTable -Candidates $candidates
    if ($count -gt 0) {
        Write-Host "PruneLegacy: running deletes..." -ForegroundColor Yellow
        Invoke-PruneLegacyDeletes -Candidates $candidates
        Write-Host "PruneLegacy done. Running Ensure." -ForegroundColor Gray
    }
}

# Resources
. (Join-Path $ScriptRoot "resources\iam.ps1")
. (Join-Path $ScriptRoot "resources\network.ps1")
. (Join-Path $ScriptRoot "resources\rds.ps1")
. (Join-Path $ScriptRoot "resources\redis.ps1")
. (Join-Path $ScriptRoot "resources\batch.ps1")
. (Join-Path $ScriptRoot "resources\jobdef.ps1")
. (Join-Path $ScriptRoot "resources\eventbridge.ps1")
. (Join-Path $ScriptRoot "resources\asg.ps1")
. (Join-Path $ScriptRoot "resources\asg_messaging.ps1")
. (Join-Path $ScriptRoot "resources\asg_ai.ps1")
. (Join-Path $ScriptRoot "resources\ssm.ps1")
. (Join-Path $ScriptRoot "resources\api.ps1")
. (Join-Path $ScriptRoot "resources\build.ps1")

. (Join-Path $ScriptRoot "netprobe\batch.ps1")

$netJobId = ""
$netStatus = ""
$script:ChangesMade = $false
try {
    Invoke-PreflightCheck
    Ensure-NetworkVpc
    Confirm-SubnetsMatchSSOT

    $script:BatchIam = Ensure-BatchIAM

    Confirm-RDSState
    Ensure-RDSSecurityGroup
    Confirm-RedisState
    Ensure-RedisSecurityGroup

    Ensure-VideoCE
    Ensure-OpsCE
    Ensure-VideoQueue
    Ensure-OpsQueue

    Ensure-VideoJobDef
    Ensure-OpsJobDefReconcile
    Ensure-OpsJobDefScanStuck
    Ensure-OpsJobDefNetprobe

    Ensure-EventBridgeRules
    Ensure-ASGMessaging
    Ensure-ASGAi
    Confirm-SSMEnv
    Confirm-APIHealth
    Confirm-BuildInstance

    if (-not $SkipNetprobe) {
        $net = Invoke-Netprobe -TimeoutSec 1200 -RunnableFailSec 300
        $netJobId = $net.jobId
        $netStatus = $net.status
    } else {
        Write-Warn "Netprobe skipped (-SkipNetprobe)"
    }
} catch {
    throw
} finally {
    Show-Evidence -NetprobeJobId $netJobId -NetprobeStatus $netStatus
}

if (-not $script:ChangesMade) {
    Write-Host "Idempotent: No changes required." -ForegroundColor Green
}
Write-Host "=== FULLSTACK DEPLOY COMPLETE ===`n" -ForegroundColor Green
