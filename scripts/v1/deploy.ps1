# Usage: pwsh scripts/v1/deploy.ps1 [-Env prod] [-Plan] [-Bootstrap] ...
# 원테이크: Bootstrap 기본 ON. SSM/SQS/RDS engineVersion/ECR 자동 준비 후 Ensure. 사용자 사전 작업 없음.
# 배포 원칙: 모든 배포·재배포는 빌드 서버 경유(-SkipBuild는 예외 상황에만). 비용 최적화: ECR Ensure 시 라이프사이클 정책 자동 적용(불필요 이미지 미보관).
# Cursor 등 새 프로세스에서 실행 시: -AwsProfile <이름> 으로 프로파일 지정 (해당 프로세스에 env 인증이 없을 때).
# ==============================================================================
[CmdletBinding()]
param(
    [ValidateSet("prod","staging","dev")]
    [string]$Env = "prod",
    [switch]$Plan = $false,
    [switch]$Bootstrap = $true,
    [switch]$StrictValidation = $true,
    [switch]$SkipBuild = $false,
    [switch]$SkipSqs = $false,
    [switch]$SkipRds = $false,
    [switch]$SkipRedis = $false,
    [switch]$PruneLegacy = $false,
    [switch]$PurgeAndRecreate = $false,
    [switch]$DryRun = $false,
    [switch]$ForceRecreateAll = $false,
    [switch]$SkipNetprobe = $false,
    [switch]$SkipApiSSMWait = $false,
    [switch]$Ci = $false,
    [switch]$RelaxedValidation = $false,
    [string]$EcrRepoUri = "",
    [string]$AwsProfile = ""
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ScriptRoot = $PSScriptRoot

# .env 자동 로드 (AWS 권한 — 동일 세션 수동 로드 불필요)
. (Join-Path $ScriptRoot "core\env.ps1")
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
Load-EnvFile -RepoRoot $RepoRoot | Out-Null

# Cursor/다른 프로세스에서 실행 시: -AwsProfile 이면 해당 프로파일 사용 ( .env 보다 우선 )
if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
    Write-Host "Using AWS_PROFILE: $($env:AWS_PROFILE) (region: $($env:AWS_DEFAULT_REGION))" -ForegroundColor Cyan
}

$script:PlanMode = $Plan
$script:AllowRebuild = -not $Plan -and (-not $ForceRecreateAll -or $true)
$script:ChangesMade = $false
$script:DeployLockAcquired = $false
$script:SqsScalingNotEnforced = $false
switch ($true) { { $EcrRepoUri } { $script:EcrRepoUri = $EcrRepoUri } default { $script:EcrRepoUri = "" } }

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
. (Join-Path $ScriptRoot "core\bootstrap.ps1")

# Resources
. (Join-Path $ScriptRoot "resources\network.ps1")
. (Join-Path $ScriptRoot "resources\iam.ps1")
. (Join-Path $ScriptRoot "resources\ssm.ps1")
. (Join-Path $ScriptRoot "resources\ecr.ps1")
. (Join-Path $ScriptRoot "resources\alb.ps1")
. (Join-Path $ScriptRoot "resources\api.ps1")
. (Join-Path $ScriptRoot "resources\build.ps1")
. (Join-Path $ScriptRoot "resources\rds.ps1")
. (Join-Path $ScriptRoot "resources\redis.ps1")
. (Join-Path $ScriptRoot "resources\asg_ai.ps1")
. (Join-Path $ScriptRoot "resources\asg_messaging.ps1")
. (Join-Path $ScriptRoot "resources\batch.ps1")
. (Join-Path $ScriptRoot "resources\jobdef.ps1")
. (Join-Path $ScriptRoot "resources\eventbridge.ps1")
. (Join-Path $ScriptRoot "resources\dynamodb.ps1")
. (Join-Path $ScriptRoot "resources\netprobe.ps1")

$null = Load-SSOT -Env $Env
$script:RelaxedValidation = $RelaxedValidation
$script:SkipApiSSMWait = $SkipApiSSMWait
if ($EcrRepoUri) { $script:EcrRepoUri = $EcrRepoUri } else { $script:EcrRepoUri = "" }

# Bootstrap (원테이크): SSM password, SQS, RDS engineVersion, ECR URI 자동 준비. Plan이면 스킵.
if ($Bootstrap -and -not $Plan) {
    Invoke-Bootstrap -Bootstrap:$true -SkipSqs:$SkipSqs -SkipRds:$SkipRds -SkipRedis:$SkipRedis -SkipBuild:$SkipBuild
}
# Bootstrap 이후 최종 EcrRepoUri 사용 (param 또는 Bootstrap이 채움)
if ($script:EcrRepoUriResolved) { $script:EcrRepoUri = $script:EcrRepoUriResolved }

# Strict Gate (Bootstrap 이후 평가): 준비 못한 항목만 실패
$strictCheck = -not $Plan -and $StrictValidation -and -not $script:RelaxedValidation
switch ($true) {
    { $strictCheck -and $script:EcrImmutableTagRequired -and (-not $script:EcrRepoUri -or $script:EcrRepoUri.Trim() -eq "") } {
        Write-Fail "Strict: EcrRepoUri not set. Bootstrap could not resolve image. Pass -EcrRepoUri or ensure build/ECR available."
        throw "Strict: EcrRepoUri required."
    }
    { $script:EcrRepoUri -and ($script:EcrRepoUri -match ':latest\s*$') } {
        Write-Fail ":latest tag is prohibited. Use an immutable tag."
        throw "EcrRepoUri must not contain :latest."
    }
    { $strictCheck -and [string]::IsNullOrWhiteSpace($script:MessagingSqsQueueUrl) -and [string]::IsNullOrWhiteSpace($script:MessagingSqsQueueName) } {
        Write-Fail "Strict: messagingWorker SQS not set. Bootstrap could not create/find queue."
        throw "Strict: set messagingWorker.sqsQueueUrl or sqsQueueName, or allow Bootstrap to create."
    }
    { $strictCheck -and [string]::IsNullOrWhiteSpace($script:AiSqsQueueUrl) -and [string]::IsNullOrWhiteSpace($script:AiSqsQueueName) } {
        Write-Fail "Strict: aiWorker SQS not set. Bootstrap could not create/find queue."
        throw "Strict: set aiWorker.sqsQueueUrl or sqsQueueName, or allow Bootstrap to create."
    }
    { $strictCheck -and $script:RdsDbIdentifier -and [string]::IsNullOrWhiteSpace($script:RdsMasterPasswordSsmParam) } {
        Write-Fail "Strict: rds.masterPasswordSsmParam not set. Bootstrap could not create SSM password."
        throw "Strict: set rds.masterPasswordSsmParam in params.yaml."
    }
    { $strictCheck -and $script:RdsDbIdentifier -and $script:RdsMasterPasswordSsmParam } {
        $rdsParam = Invoke-AwsJson @("ssm", "get-parameter", "--name", $script:RdsMasterPasswordSsmParam, "--with-decryption", "--region", $script:Region, "--output", "json")
        $badParam = -not $rdsParam -or -not $rdsParam.Parameter -or -not $rdsParam.Parameter.Value
        switch ($badParam) {
            $true {
                Write-Fail "Strict: RDS master password SSM parameter not found: $($script:RdsMasterPasswordSsmParam)"
                throw "Strict: create SSM SecureString or run with Bootstrap."
            }
        }
    }
}

Write-Host "`n=== DEPLOY v1 ($Env) ===" -ForegroundColor Cyan
if ($Plan) { Write-Host "MODE: Plan (no AWS changes)" -ForegroundColor Yellow }
if ($Bootstrap) { Write-Host "MODE: Bootstrap ON (one-take)" -ForegroundColor Cyan }
if ($StrictValidation) { Write-Host "MODE: StrictValidation ON" -ForegroundColor Cyan }
if ($RelaxedValidation) { Write-Host "MODE: RelaxedValidation (SQS scaling failure non-fatal)" -ForegroundColor Yellow }
if ($PruneLegacy -and -not $Plan) { Write-Host "MODE: PruneLegacy" -ForegroundColor Yellow }
if ($PurgeAndRecreate) { Write-Host "MODE: PurgeAndRecreate" -ForegroundColor Yellow }
if ($DryRun) { Write-Host "MODE: DryRun (no changes)" -ForegroundColor Yellow }

try {
    Assert-NoLegacyScripts -Ci:$Ci
    Acquire-DeployLock -Reg $script:Region
    Invoke-PreflightCheck
    $driftRows = Get-StructuralDrift
    Show-DriftTable -Rows $driftRows
    Save-DriftReport -Rows $driftRows

    if ($PurgeAndRecreate -and $DryRun) {
        $purgePlan = Get-PurgePlan
        $sb = [System.Text.StringBuilder]::new()
        [void]$sb.AppendLine("# Purge plan (DryRun)")
        [void]$sb.AppendLine("**Generated:** $(Get-Date -Format 'o')")
        [void]$sb.AppendLine("")
        foreach ($key in $purgePlan.Keys) {
            [void]$sb.AppendLine("## $key")
            foreach ($id in $purgePlan[$key]) { [void]$sb.AppendLine("- $id") }
            [void]$sb.AppendLine("")
        }
        Save-EvidenceReport -MarkdownContent $sb.ToString()
        Write-Host "`n=== PURGE DRY RUN COMPLETE (no changes) ===`n" -ForegroundColor Green
        Release-DeployLock -Reg $script:Region
        exit 0
    }

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

    if ($PurgeAndRecreate -and -not $Plan) {
        Invoke-PurgeAndRecreate -IncludePruneLegacy:$false
    }

    if ($Plan) {
        $ev = Show-Evidence -NetprobeJobId "" -NetprobeStatus "skipped"
        if ($ev) { Save-EvidenceReport -MarkdownContent (Convert-EvidenceToMarkdown -Ev $ev) }
        Write-Host "`n=== PLAN COMPLETE ===`n" -ForegroundColor Green
        Release-DeployLock -Reg $script:Region
        exit 0
    }

    $script:BatchIam = Ensure-BatchIAM
    Ensure-Network
    Ensure-NetworkVpc
    Confirm-SubnetsMatchSSOT
    Confirm-RDSState
    Confirm-RedisState
    Confirm-SSMEnv
    Ensure-ECRRepos
    Ensure-DynamoLockTable
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
    Ensure-ALBStack
    Ensure-API
    if (-not $SkipBuild) {
        Ensure-Build
    } else {
        Write-Warn "Build skipped (-SkipBuild). ECR image already provided."
    }

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
Write-Host "=== DEPLOY v1 COMPLETE ===`n" -ForegroundColor Green
