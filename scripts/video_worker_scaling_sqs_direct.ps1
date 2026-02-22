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
# 2) Metric Math 기반 TargetTracking JSON 생성
# ------------------------------------------------------------------------------
function New-MetricMathConfig {
    $metricMath = @{
        CustomizedMetricSpecification = @{
            Metrics = @(
                @{
                    Id = "m1"
                    MetricStat = @{
                        Metric = @{
                            MetricName = "ApproximateNumberOfMessagesVisible"
                            Namespace  = "AWS/SQS"
                            Dimensions = @(@{ Name = "QueueName"; Value = $QueueName })
                        }
                        Stat   = "Sum"
                        Period = 60
                    }
                    ReturnData = $false
                }
                @{
                    Id = "m2"
                    MetricStat = @{
                        Metric = @{
                            MetricName = "ApproximateNumberOfMessagesNotVisible"
                            Namespace  = "AWS/SQS"
                            Dimensions = @(@{ Name = "QueueName"; Value = $QueueName })
                        }
                        Stat   = "Sum"
                        Period = 60
                    }
                    ReturnData = $false
                }
                @{
                    Id         = "e1"
                    Expression = "m1 + m2"
                    Label      = "VideoQueueDepthTotal"
                    ReturnData = $true
                }
            )
        }
        TargetValue             = 1.0
        DisableScaleIn          = $false
        EstimatedInstanceWarmup = 180
    }
    return $metricMath
}

# ------------------------------------------------------------------------------
# 3) 기존 정책 삭제 후 Metric Math 정책 적용
# ------------------------------------------------------------------------------
function Apply-MetricMathPolicy {
    Log-Step "2) Delete existing policy (if any)"
    $pol = Invoke-AwsCli autoscaling describe-policies --auto-scaling-group-name $AsgName --output json 2>$null | ConvertFrom-Json
    $existing = $pol.ScalingPolicies | Where-Object { $_.PolicyName -eq $PolicyName } | Select-Object -First 1
    if ($existing) {
        Invoke-AwsCli autoscaling delete-policy --auto-scaling-group-name $AsgName --policy-name $PolicyName 2>$null
        Log-Step "  Deleted: $PolicyName"
        Start-Sleep -Seconds 2
    } else {
        Log-Step "  No existing policy to delete"
    }

    Log-Step "3) Put Metric Math TargetTracking policy"
    $config = New-MetricMathConfig
    $configJson = $config | ConvertTo-Json -Depth 10 -Compress
    $tmpFile = Join-Path $RepoRoot "metricmath_tt.json"
    [System.IO.File]::WriteAllText($tmpFile, $configJson, $utf8NoBom)
    $pathUri = "file://$($tmpFile -replace '\\','/' -replace ' ', '%20')"
    Invoke-AwsCli autoscaling put-scaling-policy --auto-scaling-group-name $AsgName --policy-name $PolicyName --policy-type TargetTrackingScaling --target-tracking-configuration $pathUri
    Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) { Log-Fail "put-scaling-policy failed"; return $false }
    Log-Step "  Metric Math policy applied (AWS/SQS m1+m2)"
    return $true
}

# ------------------------------------------------------------------------------
# 4) 검증: Namespace=AWS/SQS, Academy/VideoProcessing 미사용 확인
# ------------------------------------------------------------------------------
function Test-MetricMathApplied {
    Log-Step "4) Validate"
    $pol = Invoke-AwsCli autoscaling describe-policies --auto-scaling-group-name $AsgName --output json 2>$null | ConvertFrom-Json
    $vp = $pol.ScalingPolicies | Where-Object { $_.PolicyName -eq $PolicyName } | Select-Object -First 1
    if (-not $vp) {
        Log-Fail "  Policy $PolicyName not found"
        return $false
    }
    $cust = $vp.TargetTrackingConfiguration.CustomizedMetricSpecification
    if (-not $cust) {
        Log-Fail "  No CustomizedMetricSpecification"
        return $false
    }
    if ($cust.Namespace -eq "Academy/VideoProcessing") {
        Log-Fail "  Policy uses Lambda metric (Namespace=Academy/VideoProcessing). FAIL."
        return $false
    }
    $usesMetrics = $cust.Metrics -and $cust.Metrics.Count -gt 0
    if (-not $usesMetrics) {
        Log-Fail "  No Metrics array (Metric Math). FAIL."
        return $false
    }
    $hasSqs = $cust.Metrics | Where-Object { $_.MetricStat.Metric.Namespace -eq "AWS/SQS" } | Select-Object -First 1
    if (-not $hasSqs) {
        Log-Fail "  No AWS/SQS metric in policy. FAIL."
        return $false
    }
    Log-Step "  Namespace=AWS/SQS, Metric Math OK, Lambda OUT of path"
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
# 6) 롤백: 백업된 정책 복원
# ------------------------------------------------------------------------------
function Restore-Backup {
    Log-Step "6) Rollback"
    $metricmathDirs = Get-ChildItem -Path $BackupRoot -Directory -Filter "metricmath_*" -ErrorAction SilentlyContinue | Sort-Object Name -Descending
    if (-not $metricmathDirs -or $metricmathDirs.Count -eq 0) {
        Log-Fail "No metricmath_* backup found in $BackupRoot"
        return $false
    }
    $dir = $metricmathDirs[0].FullName
    Log-Step "  Restore from: $dir"

    $ttPath = Join-Path $dir "video_tt_config.json"
    if (-not (Test-Path $ttPath)) {
        Log-Fail "video_tt_config.json not found"
        return $false
    }
    $ttContent = Get-Content $ttPath -Raw
    $tmpFile = Join-Path $RepoRoot "asg_video_tt_rollback.json"
    [System.IO.File]::WriteAllText($tmpFile, $ttContent, $utf8NoBom)
    $pathUri = "file://$($tmpFile -replace '\\','/' -replace ' ', '%20')"

    Invoke-AwsCli autoscaling delete-policy --auto-scaling-group-name $AsgName --policy-name $PolicyName 2>$null
    Start-Sleep -Seconds 2
    Invoke-AwsCli autoscaling put-scaling-policy --auto-scaling-group-name $AsgName --policy-name $PolicyName --policy-type TargetTrackingScaling --target-tracking-configuration $pathUri
    Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) { Log-Fail "put-scaling-policy rollback failed"; return $false }
    Log-Step "  Restored (Lambda metric if that was backed up)"
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
    $config = New-MetricMathConfig
    $configJson = $config | ConvertTo-Json -Depth 10
    Log-Step "DryRun: Metric Math config (to apply)"
    Write-Host $configJson -ForegroundColor Gray
    Log-Step "DryRun done (no apply)"
    exit 0
}

if (-not (Apply-MetricMathPolicy)) { exit 1 }
if (-not (Test-MetricMathApplied)) { exit 1 }
Show-TestStats

Log-Step "Changed ScalingPolicy (TargetTrackingConfiguration)"
$pol = Invoke-AwsCli autoscaling describe-policies --auto-scaling-group-name $AsgName --output json 2>$null | ConvertFrom-Json
$vp = $pol.ScalingPolicies | Where-Object { $_.PolicyName -eq $PolicyName } | Select-Object -First 1
if ($vp) { Write-Host ($vp.TargetTrackingConfiguration | ConvertTo-Json -Depth 10) -ForegroundColor Gray }

Write-Host "`nSetup done. Lambda is OUT of scaling path. Validate:" -ForegroundColor Green
Write-Host "  aws autoscaling describe-policies --auto-scaling-group-name $AsgName --region $Region"
Write-Host "  (CustomizedMetricSpecification.Metrics must reference AWS/SQS, NOT Academy/VideoProcessing)"
Write-Host "`nRollback: .\scripts\video_worker_scaling_sqs_direct.ps1 -Rollback"
