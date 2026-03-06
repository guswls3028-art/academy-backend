# Usage: pwsh scripts/v1/deploy.ps1 [-Env prod] [-Plan] [-Bootstrap] ...
# 원테이크: Bootstrap 기본 ON. SSM/SQS/RDS engineVersion/ECR 자동 준비 후 Ensure. 사용자 사전 작업 없음.
# 배포 원칙: 빌드 서버는 사용하지 않는다(0대). 이미지 빌드·ECR 푸시는 GitHub Actions(OIDC)만 사용한다.
# deploy.ps1는 이미 ECR에 올라간 이미지를 pull하여 배포/refresh만 수행한다. (즉 -SkipBuild가 기본 흐름)
# 전체 실행 시간: API health 대기(최대 300s) + Netprobe( cold start 시 최대 600s) + Evidence(수십 초) 등으로 20~25분 넘을 수 있음. CI/터미널 타임아웃은 30분 이상 권장 (docs/00-SSOT/v1/reports/DEPLOY-TIMING-CHECKLIST.md 참고).
# Cursor 등 새 프로세스에서 실행 시: -AwsProfile <이름> 으로 프로파일 지정 (해당 프로세스에 env 인증이 없을 때).
# ==============================================================================
[CmdletBinding()]
param(
    [ValidateSet("prod","staging","dev")]
    [string]$Env = "prod",
    [switch]$Plan = $false,
    [switch]$Bootstrap = $true,
    [switch]$StrictValidation = $true,
    [switch]$SkipBuild = $true,
    [switch]$SkipSqs = $false,
    [switch]$SkipRds = $false,
    [switch]$SkipRedis = $false,
    [switch]$PruneLegacy = $false,
    [switch]$PurgeAndRecreate = $false,
    [switch]$DryRun = $false,
    [switch]$ForceRecreateAll = $false,
    [switch]$SkipNetprobe = $false,
    [switch]$MinimalDeploy = $false,
    [switch]$SkipApiSSMWait = $false,
    [switch]$Ci = $false,
    [switch]$RelaxedValidation = $false,
    [switch]$DeployFront = $false,
    [string]$EcrRepoUri = "",
    [string]$AwsProfile = ""
)
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ScriptRoot = $PSScriptRoot

# 인증: AWS·Cloudflare(클플) 인증은 Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키를 환경변수로 넣어 배포·검증·인증을 진행한다. 스크립트는 .env를 로드하지 않음.
# Cursor(에이전트)가 루트 .env를 열람해 AWS/Cloudflare 등을 환경변수로 설정한 뒤 본 스크립트를 실행한다.
. (Join-Path $ScriptRoot "core\env.ps1")
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path

# -AwsProfile 이 있으면 해당 프로파일 사용 (환경변수보다 우선)
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
. (Join-Path $ScriptRoot "resources\rds.ps1")
. (Join-Path $ScriptRoot "resources\redis.ps1")
. (Join-Path $ScriptRoot "resources\asg_ai.ps1")
. (Join-Path $ScriptRoot "resources\asg_messaging.ps1")
. (Join-Path $ScriptRoot "resources\batch.ps1")
. (Join-Path $ScriptRoot "resources\jobdef.ps1")
. (Join-Path $ScriptRoot "resources\eventbridge.ps1")
. (Join-Path $ScriptRoot "resources\dynamodb.ps1")
. (Join-Path $ScriptRoot "resources\netprobe.ps1")
. (Join-Path $ScriptRoot "resources\cloudwatch.ps1")

$null = Load-SSOT -Env $Env
$script:RelaxedValidation = $RelaxedValidation
$script:MinimalDeploy = $MinimalDeploy
if ($MinimalDeploy) {
    $script:SkipEcrVpcEndpoints = $true
    $script:VideoLongCEName = $null
    $script:VideoLongQueueName = $null
    $script:VideoLongJobDefName = $null
    $script:OpsCEName = $null
    $script:OpsQueueName = $null
    if (-not $PSBoundParameters.ContainsKey("SkipApiSSMWait")) { $script:SkipApiSSMWait = $true }
}
$script:SkipApiSSMWait = $SkipApiSSMWait
if ($EcrRepoUri) { $script:EcrRepoUri = $EcrRepoUri } else { $script:EcrRepoUri = "" }

# Bootstrap는 Ensure-Network 이후 try 블록 내에서 실행 (빌드 서버 Ensure 시 서브넷 필요).

Write-Host "`n=== DEPLOY v1 ($Env) ===" -ForegroundColor Cyan
if ($Plan) { Write-Host "MODE: Plan (no AWS changes)" -ForegroundColor Yellow }
if ($Bootstrap) { Write-Host "MODE: Bootstrap ON (one-take)" -ForegroundColor Cyan }
if ($StrictValidation) { Write-Host "MODE: StrictValidation ON" -ForegroundColor Cyan }
if ($RelaxedValidation) { Write-Host "MODE: RelaxedValidation (SQS scaling failure non-fatal)" -ForegroundColor Yellow }
if ($DeployFront) { Write-Host "MODE: DeployFront ON (build → R2 → purge → verify)" -ForegroundColor Cyan }
if ($PruneLegacy -and -not $Plan) { Write-Host "MODE: PruneLegacy" -ForegroundColor Yellow }
if ($PurgeAndRecreate) { Write-Host "MODE: PurgeAndRecreate" -ForegroundColor Yellow }
if ($MinimalDeploy) { Write-Host "MODE: MinimalDeploy (Video Long, Ops, EventBridge skipped)" -ForegroundColor Cyan }
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
    Ensure-EC2InstanceProfileSSM
    Ensure-Network
    Ensure-NetworkVpc
    Confirm-SubnetsMatchSSOT

    # Bootstrap (원테이크): Ensure-Network 이후 실행하여 빌드 서버 Ensure 시 서브넷 사용 가능.
    if ($Bootstrap -and -not $Plan) {
        Invoke-Bootstrap -Bootstrap:$true -SkipSqs:$SkipSqs -SkipRds:$SkipRds -SkipRedis:$SkipRedis -SkipBuild:$SkipBuild
    }
    if ($script:EcrRepoUriResolved) { $script:EcrRepoUri = $script:EcrRepoUriResolved }

    # Strict Gate (Bootstrap 이후 평가)
    $strictCheck = -not $Plan -and $StrictValidation -and -not $script:RelaxedValidation
    switch ($true) {
        { $strictCheck -and $script:EcrImmutableTagRequired -and (-not $script:EcrRepoUri -or $script:EcrRepoUri.Trim() -eq "") } {
            Write-Fail "Strict: EcrRepoUri not set. Bootstrap could not resolve image. Pass -EcrRepoUri or ensure build/ECR available."
            throw "Strict: EcrRepoUri required."
        }
        { $script:EcrRepoUri -and ($script:EcrRepoUri -match ':latest\s*$') -and -not $script:EcrUseLatestTag } {
            Write-Fail ":latest tag is prohibited. Use an immutable tag or set ecr.useLatestTag in SSOT."
            throw "EcrRepoUri must not contain :latest when useLatestTag is false."
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

    Confirm-RDSState
    Confirm-RedisState
    Confirm-SSMEnv
    Ensure-ECRRepos
    Ensure-DynamoLockTable
    Ensure-DynamoUploadCheckpointTable
    Ensure-ASGAi
    Ensure-ASGMessaging
    Ensure-VideoCE
    if ($script:VideoLongCEName) { Ensure-VideoLongCE }
    if (-not $script:MinimalDeploy) { Ensure-OpsCE }
    Ensure-VideoQueue
    if ($script:VideoLongQueueName) { Ensure-VideoLongQueue }
    if (-not $script:MinimalDeploy) { Ensure-OpsQueue }
    Ensure-VideoJobDef
    if ($script:VideoLongJobDefName) { Ensure-VideoLongJobDef }
    if (-not $script:MinimalDeploy) {
        Ensure-OpsJobDefReconcile
        Ensure-OpsJobDefScanStuck
        Ensure-OpsJobDefNetprobe
        Ensure-EventBridgeRules
    }
    Ensure-VideoBatchLogRetention
    Ensure-ALBStack
    Ensure-API
    if (-not $SkipBuild) {
        Write-Warn "Build step is deprecated in v1 (GitHub Actions OIDC only). Skipping build on this machine."
    } else {
        Write-Ok "Build skipped (GitHub Actions OIDC only)"
    }

    $netJobId = ""
    $netStatus = ""
    if (-not $SkipNetprobe) {
        try {
            # RunnableFailSec 600: Ops CE cold start (minvCpus=0) can take 5+ min to schedule job.
            $net = Invoke-Netprobe -TimeoutSec 1200 -RunnableFailSec 600
            $netJobId = $net.jobId
            $netStatus = $net.status
        } catch {
            Write-Warn "Netprobe failed (deploy continues): $_"
            if ($_.Exception.Message -match "jobId=([a-f0-9-]+)") { $netJobId = $matches[1] }
            $netStatus = "failed"
        }
    } else {
        Write-Warn "Netprobe skipped (-SkipNetprobe)"
    }

    $ev = Show-Evidence -NetprobeJobId $netJobId -NetprobeStatus $netStatus
    if ($ev) { Save-EvidenceReport -MarkdownContent (Convert-EvidenceToMarkdown -Ev $ev) }

    if ($DeployFront -and -not $Plan) {
        Assert-SSOTFrontR2Required
        try {
            & (Join-Path $ScriptRoot "deploy-front.ps1") -RepoRoot $RepoRoot
        } catch {
            Write-Warn "Front deploy failed (deploy continues): $_"
        }
    }

    # After-deploy verification (MinimalDeploy: ALB targets, Workers, Batch CE/Queue)
    if (-not $Plan) {
        $verifyOk = $true
        $R = $script:Region
        Write-Host "`n=== After-Deploy Verification ===" -ForegroundColor Cyan
        try {
            $asgAll = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $R, "--output", "json")
            $asgNames = @($script:AiASGName, $script:MessagingASGName, $script:ApiASGName) | Where-Object { $_ }
            foreach ($name in $asgNames) {
                $a = $asgAll.AutoScalingGroups | Where-Object { $_.AutoScalingGroupName -eq $name } | Select-Object -First 1
                if ($a) {
                    $desired = $a.DesiredCapacity; $running = ($a.Instances | Where-Object { $_.LifecycleState -eq "InService" }).Count
                    Write-Host "  ASG $name : desired=$desired inService=$running" -ForegroundColor $(if ($running -ge 1) { "Green" } else { "Yellow" })
                    if ($running -lt 1 -and $desired -ge 1) { $verifyOk = $false }
                }
            }
            $tg = Invoke-AwsJson @("elbv2", "describe-target-groups", "--names", $script:ApiTargetGroupName, "--region", $R, "--output", "json") 2>$null
            if ($tg -and $tg.TargetGroups -and $tg.TargetGroups.Count -gt 0) {
                $th = Invoke-AwsJson @("elbv2", "describe-target-health", "--target-group-arn", $tg.TargetGroups[0].TargetGroupArn, "--region", $R, "--output", "json") 2>$null
                $healthy = @($th.TargetHealthDescriptions | Where-Object { $_.TargetHealth.State -eq "healthy" }).Count
                $total = if ($th.TargetHealthDescriptions) { $th.TargetHealthDescriptions.Count } else { 0 }
                Write-Host "  ALB target health : $healthy / $total healthy" -ForegroundColor $(if ($healthy -ge 1) { "Green" } else { "Yellow" })
                if ($healthy -lt 1 -and $total -gt 0) { $verifyOk = $false }
            }
            $ce = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoCEName, "--region", $R, "--output", "json") 2>$null
            if ($ce -and $ce.computeEnvironments -and $ce.computeEnvironments.Count -gt 0) {
                $ceStatus = $ce.computeEnvironments[0].status; $ceState = $ce.computeEnvironments[0].state
                Write-Host "  Batch Video CE : status=$ceStatus state=$ceState" -ForegroundColor $(if ($ceStatus -eq "VALID") { "Green" } else { "Yellow" })
                if ($ceStatus -ne "VALID") { $verifyOk = $false }
            }
            $q = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $R, "--output", "json") 2>$null
            if ($q -and $q.jobQueues -and $q.jobQueues.Count -gt 0) {
                $qState = $q.jobQueues[0].state
                Write-Host "  Batch Video Queue : state=$qState" -ForegroundColor $(if ($qState -eq "ENABLED") { "Green" } else { "Yellow" })
                if ($qState -ne "ENABLED") { $verifyOk = $false }
            }
        } catch {
            Write-Warn "Verification error: $_"
            $verifyOk = $false
        }
        if ($verifyOk) {
            Write-Host "`nDEPLOY_SUCCESS" -ForegroundColor Green
        } else {
            Write-Host "`nDEPLOY_SUCCESS (verification warnings - check ASG/ALB/Batch above)" -ForegroundColor Yellow
        }
    }
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
