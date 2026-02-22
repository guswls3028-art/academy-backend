# ==============================================================================
# Video Worker 원큐 검증: 요구사항 만족 여부 OK/FAIL 판정
# ==============================================================================
# 사용: .\scripts\video_worker_oneclick_validate.ps1
#      .\scripts\video_worker_oneclick_validate.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$Profile = ""
)

$ErrorActionPreference = "Continue"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot

$AsgName = "academy-video-worker-asg"
$QueueName = "academy-video-jobs"
$DlqName = "academy-video-jobs-dlq"
$LambdaName = "academy-worker-queue-depth-metric"
$PolicyName = "video-backlogcount-tt"

$AwsBase = @("--region", $Region)
if ($Profile) { $AwsBase = @("--profile", $Profile) + $AwsBase }
function Invoke-AwsCli { param([parameter(ValueFromRemainingArguments)]$Rest) $a = @($Rest) + $AwsBase; $exe = (Get-Command aws.exe -CommandType Application -ErrorAction SilentlyContinue).Source; if (-not $exe) { $exe = "aws" }; & $exe @a }

$results = @{}
function Ok { param([string]$K, [string]$V) $results[$K] = @{ ok = $true; msg = $V }; Write-Host "  [OK] $K : $V" -ForegroundColor Green }
function Fail { param([string]$K, [string]$V) $results[$K] = @{ ok = $false; msg = $V }; Write-Host "  [FAIL] $K : $V" -ForegroundColor Red }
function Warn { param([string]$K, [string]$V) $results[$K] = @{ ok = $true; warn = $V }; Write-Host "  [WARN] $K : $V" -ForegroundColor Yellow }

Write-Host "`n========== Video Worker One-Click Validate ==========" -ForegroundColor Cyan

# 1) Scaling metric SQS-based?
$pol = $null
try { $pol = Invoke-AwsCli autoscaling describe-policies --auto-scaling-group-name $AsgName --output json 2>$null | ConvertFrom-Json } catch {}
$metricName = "none"
$policyRef = $null
if ($pol -and $pol.ScalingPolicies) {
    $vp = $pol.ScalingPolicies | Where-Object { $_.PolicyName -like "*video*" -or $_.PolicyName -like "*backlog*" } | Select-Object -First 1
    if ($vp) {
        $policyRef = $vp
        if ($vp.TargetTrackingConfiguration.PredefinedMetricSpecification) {
            $metricName = "Predefined:" + $vp.TargetTrackingConfiguration.PredefinedMetricSpecification.PredefinedMetricType
        } elseif ($vp.TargetTrackingConfiguration.CustomizedMetricSpecification) {
            $metricName = $vp.TargetTrackingConfiguration.CustomizedMetricSpecification.MetricName
        }
    }
}
if ($metricName -eq "VideoQueueDepthTotal") {
    Ok "ScalingMetric" "SQS-based (VideoQueueDepthTotal = visible+notVisible)"
} elseif ($metricName -eq "BacklogCount" -or $metricName -eq "backlog") {
    Fail "ScalingMetric" "DB/API 기반 메트릭 사용 중: $metricName. SQS 기반으로 교체 필요."
} else {
    Fail "ScalingMetric" "Current metric: $metricName. Expected: VideoQueueDepthTotal"
}

# 2) SQS visible / notVisible
$qurl = $null
try { $qurl = Invoke-AwsCli sqs get-queue-url --queue-name $QueueName --query "QueueUrl" --output text 2>$null } catch {}
if (-not $qurl) {
    Fail "SQS" "큐 URL 조회 실패 ($QueueName)"
} else {
    $attrs = Invoke-AwsCli sqs get-queue-attributes --queue-url $qurl --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --output json 2>$null | ConvertFrom-Json
    $v = [int]($attrs.Attributes.ApproximateNumberOfMessages)
    $nv = [int]($attrs.Attributes.ApproximateNumberOfMessagesNotVisible)
    Ok "SQS" "visible=$v notVisible=$nv total=$($v+$nv)"
}

# 3) ASG desired / min / max
$asg = $null
try { $asg = Invoke-AwsCli autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgName --query "AutoScalingGroups[0]" --output json 2>$null | ConvertFrom-Json } catch {}
if (-not $asg) {
    Fail "ASG" "ASG fetch failed ($AsgName)"
} else {
    Ok "ASG" "desired=$($asg.DesiredCapacity) min=$($asg.MinSize) max=$($asg.MaxSize)"
}

# 4) Recent scaling events
$act = $null
try { $act = Invoke-AwsCli autoscaling describe-scaling-activities --auto-scaling-group-name $AsgName --max-items 5 --output json 2>$null | ConvertFrom-Json } catch {}
if ($act -and $act.Activities -and $act.Activities.Count -gt 0) {
    $last = $act.Activities[0]
    Ok "ScalingEvents" "Recent: $($last.StatusCode) $($last.Description)"
} else {
    Ok "ScalingEvents" "No recent activity"
}

# 5) DLQ / redrive
$hasDlq = $false
$hasRedrive = $false
if ($qurl) {
    $allAttrs = $null
    try { $allAttrs = Invoke-AwsCli sqs get-queue-attributes --queue-url $qurl --attribute-names All --output json 2>$null | ConvertFrom-Json } catch {}
    if ($allAttrs -and $allAttrs.Attributes -and $allAttrs.Attributes.RedrivePolicy) { $hasRedrive = $true }
}
$dlqUrl = $null
try { $dlqUrl = Invoke-AwsCli sqs get-queue-url --queue-name $DlqName --query "QueueUrl" --output text 2>$null } catch {}
if ($dlqUrl) { $hasDlq = $true }
if ($hasDlq -and $hasRedrive) {
    Ok "DLQ" "DLQ exists, RedrivePolicy set"
} elseif ($hasDlq) {
    Warn "DLQ" "DLQ 존재하나 RedrivePolicy 없음"
} else {
    Fail "DLQ" "DLQ missing or RedrivePolicy not set"
}

# 6) Lambda in scaling path
$fn = $null
try { $fn = Invoke-AwsCli lambda get-function --function-name $LambdaName --output json 2>$null } catch {}
if ($fn -and $metricName -eq "VideoQueueDepthTotal") {
    Warn "LambdaInPath" "Scaling metric (VideoQueueDepthTotal) published by Lambda. OK but Lambda failure stops scaling."
} elseif ($metricName -eq "BacklogCount") {
    Warn "LambdaInPath" "BacklogCount uses Lambda/API. Switch to SQS-based."
}

# 요약
$failCount = ($results.Values | Where-Object { -not $_.ok }).Count
Write-Host "`n========== Summary ==========" -ForegroundColor Cyan
if ($failCount -eq 0) {
    Write-Host "  Result: OK (requirements met)" -ForegroundColor Green
} else {
    Write-Host "  Result: FAIL ($failCount failed)" -ForegroundColor Red
}
Write-Host ""
