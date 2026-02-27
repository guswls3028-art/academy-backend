# ==============================================================================
# Academy SSOT v4 — 단일 진입점. 풀스택 Ensure + PruneLegacy + PurgeAndRecreate + Netprobe + Evidence.
# Usage: pwsh scripts/v4/deploy.ps1 [-Env prod] [-Plan] [-PruneLegacy] [-PurgeAndRecreate] [-DryRun] [-ForceRecreateAll] [-SkipNetprobe] [-Ci] [-EcrRepoUri ...]
# -Plan: AWS 변경 0, 표/리포트만 출력.
# -PruneLegacy: SSOT 외 academy-* 삭제 후 Ensure. -Plan이면 후보 표만 출력.
# -PurgeAndRecreate: SSOT Batch/EventBridge 전부 삭제 후 전체 Ensure 재실행. -DryRun이면 삭제 예정만 출력 후 종료.
# ==============================================================================
[CmdletBinding()]
param(
    [ValidateSet("prod","staging","dev")]
    [string]$Env = "prod",
    [switch]$Plan = $false,
    [switch]$PruneLegacy = $false,
    [switch]$PurgeAndRecreate = $false,
    [switch]$DryRun = $false,
    [switch]$ForceRecreateAll = $false,
    [switch]$SkipNetprobe = $false,
    [switch]$Ci = $false,
    [string]$EcrRepoUri = ""
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ScriptRoot = $PSScriptRoot

$script:PlanMode = $Plan
$script:AllowRebuild = -not $Plan -and (-not $ForceRecreateAll -or $true)
$script:ChangesMade = $false
$script:DeployLockAcquired = $false
if ($EcrRepoUri) { $script:EcrRepoUri = $EcrRepoUri } else { $script:EcrRepoUri = "" }

# Core (order: ssot first so Load-SSOT sets vars)
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\logging.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "core\wait.ps1")
. (Join-Path $ScriptRoot "core\diff.ps1")
. (Join-Path $ScriptRoot "core\evidence.ps1")
. (Join-Path $ScriptRoot "core\prune.ps1")
. (Join-Path $ScriptRoot "core\guard.ps1")
. (Join-Path $ScriptRoot "core\preflight.ps1")
. (Join-Path $ScriptRoot "core\reports.ps1")

# Resources
. (Join-Path $ScriptRoot "resources\network.ps1")
. (Join-Path $ScriptRoot "resources\iam.ps1")
. (Join-Path $ScriptRoot "resources\ssm.ps1")
. (Join-Path $ScriptRoot "resources\ecr.ps1")
. (Join-Path $ScriptRoot "resources\api.ps1")
. (Join-Path $ScriptRoot "resources\build.ps1")
. (Join-Path $ScriptRoot "resources\rds.ps1")
. (Join-Path $ScriptRoot "resources\redis.ps1")
. (Join-Path $ScriptRoot "resources\asg_ai.ps1")
. (Join-Path $ScriptRoot "resources\asg_messaging.ps1")
. (Join-Path $ScriptRoot "resources\batch.ps1")
. (Join-Path $ScriptRoot "resources\jobdef.ps1")
. (Join-Path $ScriptRoot "resources\eventbridge.ps1")
. (Join-Path $ScriptRoot "resources\netprobe.ps1")

Load-SSOT -Env $Env | Out-Null

Write-Host "`n=== DEPLOY v4 ($Env) ===" -ForegroundColor Cyan
if ($Plan) { Write-Host "MODE: Plan (no AWS changes)" -ForegroundColor Yellow }
if ($PruneLegacy -and -not $Plan) { Write-Host "MODE: PruneLegacy" -ForegroundColor Yellow }

try {
    Assert-NoLegacyScripts -Ci:$Ci
    Acquire-DeployLock -Reg $script:Region
    Invoke-PreflightCheck
    $driftRows = Get-StructuralDrift
    Show-DriftTable -Rows $driftRows
    Save-DriftReport -Rows $driftRows

    if ($PruneLegacy) {
        $all = Get-AllAwsResourcesForPrune
        $candidates = Get-DeleteCandidates -All $all
        $count = Show-DeleteCandidateTable -Candidates $candidates
        if (-not $Plan -and $count -gt 0) {
            Write-Host "PruneLegacy: running deletes..." -ForegroundColor Yellow
            Invoke-PruneLegacyDeletes -Candidates $candidates
        }
        if ($Plan) {
            Write-Host "`n=== PLAN COMPLETE (no changes) ===`n" -ForegroundColor Green
            exit 0
        }
    }

    if ($Plan) {
        $ev = Show-Evidence -NetprobeJobId "" -NetprobeStatus "skipped"
        if ($ev) { Save-EvidenceReport -MarkdownContent (Convert-EvidenceToMarkdown -Ev $ev) }
        Write-Host "`n=== PLAN COMPLETE ===`n" -ForegroundColor Green
        Release-DeployLock -Reg $script:Region
        exit 0
    }

    $script:BatchIam = Ensure-BatchIAM
    Ensure-NetworkVpc
    Confirm-SubnetsMatchSSOT
    Confirm-RDSState
    Ensure-RDSSecurityGroup
    Confirm-RedisState
    Ensure-RedisSecurityGroup
    Confirm-SSMEnv
    Ensure-ECRRepos
    Ensure-ASGMessaging
    Ensure-ASGAi
    Ensure-VideoCE
    Ensure-OpsCE
    Ensure-VideoQueue
    Ensure-OpsQueue
    Ensure-VideoJobDef
    Ensure-OpsJobDefReconcile
    Ensure-OpsJobDefScanStuck
    Ensure-OpsJobDefNetprobe
    Ensure-EventBridgeRules
    Confirm-APIHealth
    Confirm-BuildInstance

    $netJobId = ""
    $netStatus = ""
    if (-not $SkipNetprobe) {
        $net = Invoke-Netprobe -TimeoutSec 1200 -RunnableFailSec 300
        $netJobId = $net.jobId
        $netStatus = $net.status
    } else {
        Write-Warn "Netprobe skipped (-SkipNetprobe)"
    }

    $ev = Show-Evidence -NetprobeJobId $netJobId -NetprobeStatus $netStatus
    if ($ev) { Save-EvidenceReport -MarkdownContent (Convert-EvidenceToMarkdown -Ev $ev) }
}
catch {
    Write-Fail $_.Exception.Message
    throw
}
finally {
    Release-DeployLock -Reg $script:Region
}

if (-not $script:ChangesMade) {
    Write-Host "Idempotent: No changes required." -ForegroundColor Green
}
Write-Host "=== DEPLOY v4 COMPLETE ===`n" -ForegroundColor Green
