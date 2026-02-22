# ==============================================================================
# 영상 워커 스케일링 수정 원큐 적용: SQS 기반 VideoQueueDepthTotal로 전환
# 백업 → Lambda/ASG 정책 적용 → 검증. -Rollback 시 이전 정책 복원.
# ==============================================================================
# 사용:
# [DEPRECATED] Video = AWS Batch 전용. academy-video-worker-asg 미사용.
#   적용: .\scripts\apply_video_worker_scaling_fix.ps1 -Region ap-northeast-2
#   롤백: .\scripts\apply_video_worker_scaling_fix.ps1 -Region ap-northeast-2 -Rollback
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$AsgName = "academy-video-worker-asg",
    [string]$QueueName = "academy-video-jobs",
    [string]$LambdaName = "academy-worker-queue-depth-metric",
    [string]$PolicyName = "video-backlogcount-tt",
    [switch]$Rollback = $false
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

# 백업 디렉터리 (타임스탬프). 롤백 시 최신 백업 사용
$BackupRoot = Join-Path $RepoRoot "backup_video_worker_scaling"

function Write-Step { param([string]$Msg) Write-Host "`n========== $Msg ==========" -ForegroundColor Cyan }
function Backup-CurrentState {
    $ts = Get-Date -Format "yyyyMMdd_HHmmss"
    $dir = Join-Path $BackupRoot $ts
    New-Item -ItemType Directory -Path $dir -Force | Out-Null

    Write-Step "1a. Backup ASG"
    $asg = aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgName --region $Region --output json 2>$null
    if ($asg) { [System.IO.File]::WriteAllText((Join-Path $dir "asg.json"), $asg, $utf8NoBom) }

    Write-Step "1b. Backup Scaling Policies"
    $policies = aws autoscaling describe-policies --auto-scaling-group-name $AsgName --region $Region --output json 2>$null
    if ($policies) {
        [System.IO.File]::WriteAllText((Join-Path $dir "scaling_policies.json"), $policies, $utf8NoBom)
        $obj = $policies | ConvertFrom-Json
        $videoPolicy = $obj.ScalingPolicies | Where-Object { $_.PolicyName -eq $PolicyName } | Select-Object -First 1
        if ($videoPolicy -and $videoPolicy.TargetTrackingConfiguration) {
            $tt = $videoPolicy.TargetTrackingConfiguration | ConvertTo-Json -Depth 10 -Compress
            [System.IO.File]::WriteAllText((Join-Path $dir "video_tt_config.json"), $tt, $utf8NoBom)
        }
    }

    Write-Step "1c. Backup Lambda config (env)"
    $lambdaConfig = aws lambda get-function-configuration --function-name $LambdaName --region $Region --output json 2>$null
    if ($lambdaConfig) { [System.IO.File]::WriteAllText((Join-Path $dir "lambda_config.json"), $lambdaConfig, $utf8NoBom) }

    Write-Step "1d. Backup SQS attributes"
    $qurl = aws sqs get-queue-url --queue-name $QueueName --region $Region --query "QueueUrl" --output text 2>$null
    if ($qurl) {
        $attrs = aws sqs get-queue-attributes --queue-url $qurl --attribute-names All --region $Region --output json 2>$null
        if ($attrs) { [System.IO.File]::WriteAllText((Join-Path $dir "sqs_attributes.json"), $attrs, $utf8NoBom) }
    }

    Write-Host "Backup saved to: $dir" -ForegroundColor Green
    return $dir
}

function Apply-Fix {
    Write-Step "2a. Deploy Lambda (VideoQueueDepthTotal, SQS only)"
    $lambdaPath = Join-Path $RepoRoot "infra\worker_asg\queue_depth_lambda\lambda_function.py"
    $zipPath = Join-Path $RepoRoot "worker_queue_depth_lambda.zip"
    if (-not (Test-Path $lambdaPath)) { throw "Lambda not found: $lambdaPath" }
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path $lambdaPath -DestinationPath $zipPath -Force
    aws lambda update-function-code --function-name $LambdaName --zip-file "fileb://$zipPath" --region $Region | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Lambda update-function-code failed" }
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    Write-Host "Lambda code update requested. Waiting up to 30s for Successful..." -ForegroundColor Gray
    $waited = 0
    do {
        Start-Sleep -Seconds 2
        $waited += 2
        $status = aws lambda get-function-configuration --function-name $LambdaName --region $Region --query "LastUpdateStatus" --output text
        if ($status -eq "Successful") { break }
        if ($waited -ge 30) { Write-Host "WARN: Lambda still updating." -ForegroundColor Yellow; break }
    } while ($true)

    Write-Step "2b. ASG TargetTracking → SSOT (Visible-only, scripts/infra/apply_video_asg_scaling_policy.ps1)"
    & (Join-Path $ScriptRoot "infra\apply_video_asg_scaling_policy.ps1") -Region $Region -AsgName $AsgName
    if ($LASTEXITCODE -ne 0) { throw "apply_video_asg_scaling_policy failed" }
    Write-Host "Policy updated via SSOT (video-visible-only-tt, Expression=m1)." -ForegroundColor Green
}

function Restore-Backup {
    param([string]$BackupDir)
    if (-not (Test-Path $BackupDir)) { throw "Backup dir not found: $BackupDir" }
    $ttPath = Join-Path $BackupDir "video_tt_config.json"
    if (-not (Test-Path $ttPath)) {
        Write-Host "No video_tt_config.json in backup; cannot restore policy. Re-apply fix or set policy manually." -ForegroundColor Yellow
        return
    }
    Write-Step "Rollback: Restore ASG scaling policy via SSOT (Visible-only). Backup was old policy; SSOT = video-visible-only-tt."
    & (Join-Path $ScriptRoot "infra\apply_video_asg_scaling_policy.ps1") -Region $Region -AsgName $AsgName
    Write-Host "Policy set to SSOT (Visible-only). Lambda still publishes VideoQueueDepthTotal; for full rollback revert infra/worker_asg/queue_depth_lambda/lambda_function.py and run: .\scripts\deploy_queue_depth_lambda.ps1 -Region $Region" -ForegroundColor Yellow
}

function Verify-State {
    Write-Step "3. Verify"
    $qurl = aws sqs get-queue-url --queue-name $QueueName --region $Region --query "QueueUrl" --output text 2>$null
    Write-Host "SQS $QueueName (visible + notVisible):"
    if ($qurl) {
        aws sqs get-queue-attributes --queue-url $qurl --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --region $Region --output table
    }

    Write-Host "`nLambda invoke (video_queue_depth_total):"
    $invokeOut = Join-Path $RepoRoot "response_scaling_fix.json"
    aws lambda invoke --function-name $LambdaName --region $Region --cli-binary-format raw-in-base64-out $invokeOut 2>$null
    if (Test-Path $invokeOut) {
        $payload = Get-Content $invokeOut -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host "  video_queue_depth_total: $($payload.video_queue_depth_total)"
    }

    Write-Host "`nASG policy $PolicyName:"
    aws autoscaling describe-policies --auto-scaling-group-name $AsgName --policy-names $PolicyName --region $Region --query "ScalingPolicies[0].{PolicyName:PolicyName,TargetTrackingConfiguration:TargetTrackingConfiguration}" --output json

    Write-Host "`nASG desired/min/max:"
    aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgName --region $Region --query "AutoScalingGroups[0].{DesiredCapacity:DesiredCapacity,MinSize:MinSize,MaxSize:MaxSize}" --output table
}

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
Write-Host "Video Worker Scaling Fix | Region=$Region Rollback=$Rollback" -ForegroundColor Cyan

if ($Rollback) {
    if (-not (Test-Path $BackupRoot)) { throw "No backup found at $BackupRoot. Run apply without -Rollback first." }
    $latestBackup = Get-ChildItem -Path $BackupRoot -Directory | Sort-Object Name -Descending | Select-Object -First 1
    if (-not $latestBackup) { throw "No backup directory found in $BackupRoot" }
    Restore-Backup -BackupDir $latestBackup.FullName
    Write-Host "`nRollback done. Remember to revert Lambda code and run deploy_queue_depth_lambda.ps1 if you need BacklogCount behavior." -ForegroundColor Yellow
    exit 0
}

$backupDir = Backup-CurrentState
Apply-Fix
Verify-State

Write-Host "`nApply complete. Scaling is now SQS-based (VideoQueueDepthTotal). Use .\scripts\diagnose_video_worker_full.ps1 for full diagnostic." -ForegroundColor Green
