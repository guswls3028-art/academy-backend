# ==============================================================================
# Video Worker ASG: Lambda 제거, SQS Metric Math 직접 참조
# Scaling Path: SQS Visible + NotVisible -> CloudWatch Metric Math -> ASG
# Lambda(academy-worker-queue-depth-metric)는 Scaling Path에서 완전 제외.
# ==============================================================================
# 사용:
#   .\scripts\video_worker_scaling_sqs_direct.ps1
#   .\scripts\video_worker_scaling_sqs_direct.ps1 -Region ap-northeast-2
#   .\scripts\video_worker_scaling_sqs_direct.ps1 -DryRun
#   .\scripts\video_worker_scaling_sqs_direct.ps1 -Rollback
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$Profile = "",
    [switch]$DryRun = $false,
    [switch]$Rollback = $false
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

$AsgName = "academy-video-worker-asg"
$QueueName = "academy-video-jobs"
$PolicyName = "video-backlogcount-tt"
$BackupRoot = Join-Path $RepoRoot "backups\video_worker"

$AwsBase = @("--region", $Region, "--cli-read-timeout", "15", "--cli-connect-timeout", "10")
if ($Profile) { $AwsBase = @("--profile", $Profile) + $AwsBase }
function Invoke-AwsCli { param([parameter(ValueFromRemainingArguments)]$Rest) $a = @($Rest) + $AwsBase; $exe = (Get-Command aws.exe -CommandType Application -ErrorAction SilentlyContinue).Source; if (-not $exe) { $exe = "aws" }; $ea = $ErrorActionPreference; $ErrorActionPreference = 'SilentlyContinue'; try { & $exe @a } finally { $ErrorActionPreference = $ea } }

function Log-Step { param([string]$Msg) Write-Host $Msg -ForegroundColor Cyan }
function Log-Warn { param([string]$Msg) Write-Host $Msg -ForegroundColor Yellow }
function Log-Fail { param([string]$Msg) Write-Host $Msg -ForegroundColor Red }

# ------------------------------------------------------------------------------
# 1) 현재 ScalingPolicy 백업
# ------------------------------------------------------------------------------
function Backup-Policy {
    $ts = Get-Date -Format "yyyyMMdd_HHmmss"
    $dir = Join-Path $BackupRoot "metricmath_$ts"
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    Log-Step "1) Backup -> $dir"

    $polJson = Invoke-AwsCli autoscaling describe-policies --auto-scaling-group-name $AsgName --output json 2>$null
    if ($polJson) {
        [System.IO.File]::WriteAllText((Join-Path $dir "scaling_policies.json"), $polJson, $utf8NoBom)
        $polObj = $polJson | ConvertFrom-Json
        $vp = $polObj.ScalingPolicies | Where-Object { $_.PolicyName -eq $PolicyName } | Select-Object -First 1
        if ($vp -and $vp.TargetTrackingConfiguration) {
            $tt = $vp.TargetTrackingConfiguration | ConvertTo-Json -Depth 15 -Compress
            [System.IO.File]::WriteAllText((Join-Path $dir "video_tt_config.json"), $tt, $utf8NoBom)
        }
    }

    $alarmsJson = Invoke-AwsCli cloudwatch describe-alarms --output json 2>$null
    if ($alarmsJson) { [System.IO.File]::WriteAllText((Join-Path $dir "alarms.json"), $alarmsJson, $utf8NoBom) }

    Log-Step "  Backup done: $dir"
    return $dir
}

# ------------------------------------------------------------------------------
# 2) DEPRECATED: m1+m2 적용 제거. SSOT = Visible-only (m1만)
# ------------------------------------------------------------------------------
function New-MetricMathConfig {
    Write-Host "  (DEPRECATED: m1+m2 no longer applied. SSOT = Visible-only m1)" -ForegroundColor Yellow
    return $null
}

# ------------------------------------------------------------------------------
# 3) SSOT 스크립트 호출 (Visible-only, Expression=m1)
# ------------------------------------------------------------------------------
function Apply-MetricMathPolicy {
    Log-Step "2) Applying SSOT: scripts/infra/apply_video_asg_scaling_policy.ps1 (Visible-only, m1)"
    & (Join-Path $ScriptRoot "infra\apply_video_asg_scaling_policy.ps1") -Region $Region -AsgName $AsgName
    if ($LASTEXITCODE -ne 0) { Log-Fail "SSOT apply failed"; return $false }
    Log-Step "  SSOT applied (Expression=m1, m2 미포함)"
    return $true
}

# ------------------------------------------------------------------------------
# 4) 검증: video-visible-only-tt (Expression=m1, m2 미포함)
# ------------------------------------------------------------------------------
function Test-MetricMathApplied {
    Log-Step "4) Validate"
    $pol = Invoke-AwsCli autoscaling describe-policies --auto-scaling-group-name $AsgName --output json 2>$null | ConvertFrom-Json
    $vp = $pol.ScalingPolicies | Where-Object { $_.PolicyName -eq "video-visible-only-tt" } | Select-Object -First 1
    if (-not $vp) {
        Log-Fail "  Policy video-visible-only-tt not found"
        return $false
    }
    $cust = $vp.TargetTrackingConfiguration.CustomizedMetricSpecification
    if (-not $cust) {
        Log-Fail "  No CustomizedMetricSpecification"
        return $false
    }
    $e1 = $cust.Metrics | Where-Object { $_.Id -eq "e1" } | Select-Object -First 1
    if (-not $e1 -or $e1.Expression -ne "m1") {
        Log-Fail "  Expected Expression=m1 (Visible only), got: $($e1.Expression)"
        return $false
    }
    $hasM2 = $cust.Metrics | Where-Object { $_.Id -eq "m2" } | Select-Object -First 1
    if ($hasM2) {
        Log-Fail "  m2(NotVisible) should NOT exist (Visible-only policy)"
        return $false
    }
    Log-Step "  OK: Expression=m1 (Visible only), Lambda OUT of path"
    return $true
}

# ------------------------------------------------------------------------------
# 5) 테스트: SQS visible/notVisible, total/instances 출력
# ------------------------------------------------------------------------------
function Show-TestStats {
    Log-Step "5) Test stats"
    $qurl = Invoke-AwsCli sqs get-queue-url --queue-name $QueueName --query "QueueUrl" --output text 2>$null
    if (-not $qurl) { Log-Warn "  SQS queue not found"; return }
    $attrs = Invoke-AwsCli sqs get-queue-attributes --queue-url $qurl --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --output json 2>$null | ConvertFrom-Json
    $visible = 0
    $notVisible = 0
    if ($attrs.Attributes) {
        [int]::TryParse($attrs.Attributes.ApproximateNumberOfMessages, [ref]$visible) | Out-Null
        [int]::TryParse($attrs.Attributes.ApproximateNumberOfMessagesNotVisible, [ref]$notVisible) | Out-Null
    }
    $total = $visible + $notVisible
    $asg = Invoke-AwsCli autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgName --query "AutoScalingGroups[0]" --output json 2>$null | ConvertFrom-Json
    $desired = 0
    $inService = 0
    if ($asg) {
        $desired = $asg.DesiredCapacity
        $inService = ($asg.Instances | Where-Object { $_.LifecycleState -eq "InService" }).Count
    }
    Write-Host "  visible=$visible notVisible=$notVisible total=$total | desired=$desired inService=$inService" -ForegroundColor Gray
    if ($inService -gt 0) {
        $ratio = [math]::Round($total / $inService, 2)
        Write-Host "  total_messages / inService_instances = $ratio" -ForegroundColor Gray
    }
}

# ------------------------------------------------------------------------------
# 6) 롤백: SSOT (Visible-only)로 복원
# ------------------------------------------------------------------------------
function Restore-Backup {
    Log-Step "6) Rollback -> SSOT (Visible-only)"
    & (Join-Path $ScriptRoot "infra\apply_video_asg_scaling_policy.ps1") -Region $Region -AsgName $AsgName
    if ($LASTEXITCODE -ne 0) { Log-Fail "SSOT apply failed"; return $false }
    Log-Step "  Restored to SSOT (video-visible-only-tt)"
    return $true
}

# ------------------------------------------------------------------------------
# Pre-check
# ------------------------------------------------------------------------------
function Test-Prechecks {
    Log-Step "0) Pre-check"
    $id = Invoke-AwsCli sts get-caller-identity --output json 2>$null
    if (-not $id) {
        Log-Fail "AWS login/perms failed. Run aws sts get-caller-identity"
        return $false
    }
    $asg = Invoke-AwsCli autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgName --output json 2>$null | ConvertFrom-Json
    if (-not $asg -or -not $asg.AutoScalingGroups) {
        Log-Fail "ASG $AsgName not found"
        return $false
    }
    $qurl = Invoke-AwsCli sqs get-queue-url --queue-name $QueueName --query "QueueUrl" --output text 2>$null
    if (-not $qurl) {
        Log-Fail "SQS $QueueName not found"
        return $false
    }
    Log-Step "  sts OK, ASG OK, SQS OK"
    return $true
}

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
Write-Host "Video Worker SQS Direct (Lambda-free) | Region=$Region DryRun=$DryRun Rollback=$Rollback" -ForegroundColor Cyan

if (-not (Test-Prechecks)) { exit 1 }

if ($Rollback) {
    Restore-Backup | Out-Null
    Write-Host "`nRollback done. Validate: aws autoscaling describe-policies --auto-scaling-group-name $AsgName --region $Region" -ForegroundColor Green
    exit 0
}

$null = Backup-Policy

if ($DryRun) {
    Log-Step "DryRun: Will apply SSOT (scripts/infra/apply_video_asg_scaling_policy.ps1, Visible-only m1)"
    Log-Step "DryRun done (no apply)"
    exit 0
}

if (-not (Apply-MetricMathPolicy)) { exit 1 }
if (-not (Test-MetricMathApplied)) { exit 1 }
Show-TestStats

Log-Step "Changed ScalingPolicy (SSOT Visible-only)"
$pol = Invoke-AwsCli autoscaling describe-policies --auto-scaling-group-name $AsgName --output json 2>$null | ConvertFrom-Json
$vp = $pol.ScalingPolicies | Where-Object { $_.PolicyName -eq "video-visible-only-tt" } | Select-Object -First 1
if ($vp) { Write-Host ($vp.TargetTrackingConfiguration | ConvertTo-Json -Depth 10) -ForegroundColor Gray }

Write-Host "`nSetup done. SSOT = Expression=m1 (Visible only). Validate:" -ForegroundColor Green
Write-Host "  aws autoscaling describe-policies --auto-scaling-group-name $AsgName --region $Region --query ""ScalingPolicies[?PolicyType=='TargetTrackingScaling'].TargetTrackingConfiguration.CustomizedMetricSpecification.Metrics"""
Write-Host "`nRollback: .\scripts\video_worker_scaling_sqs_direct.ps1 -Rollback"
