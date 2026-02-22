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
function Aws { param([parameter(ValueFromRemainingArguments)]$Rest) $a = @($Rest) + $AwsBase; & aws @a }

$results = @{}
function Ok { param([string]$K, [string]$V) $results[$K] = @{ ok = $true; msg = $V }; Write-Host "  [OK] $K : $V" -ForegroundColor Green }
function Fail { param([string]$K, [string]$V) $results[$K] = @{ ok = $false; msg = $V }; Write-Host "  [FAIL] $K : $V" -ForegroundColor Red }
function Warn { param([string]$K, [string]$V) $results[$K] = @{ ok = $true; warn = $V }; Write-Host "  [WARN] $K : $V" -ForegroundColor Yellow }

Write-Host "`n========== Video Worker One-Click Validate ==========" -ForegroundColor Cyan

# 1) Scaling metric SQS-based?
$pol = Aws autoscaling describe-policies --auto-scaling-group-name $AsgName --output json 2>$null | ConvertFrom-Json
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
$qurl = Aws sqs get-queue-url --queue-name $QueueName --query "QueueUrl" --output text 2>$null
if (-not $qurl) {
    Fail "SQS" "큐 URL 조회 실패 ($QueueName)"
} else {
    $attrs = Aws sqs get-queue-attributes --queue-url $qurl --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --output json 2>$null | ConvertFrom-Json
    $v = [int]($attrs.Attributes.ApproximateNumberOfMessages)
    $nv = [int]($attrs.Attributes.ApproximateNumberOfMessagesNotVisible)
    Ok "SQS" "visible=$v notVisible=$nv total=$($v+$nv)"
}

# 3) ASG desired / min / max
$asg = Aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names $AsgName --query "AutoScalingGroups[0]" --output json 2>$null | ConvertFrom-Json
if (-not $asg) {
    Fail "ASG" "ASG fetch failed ($AsgName)"
} else {
    Ok "ASG" "desired=$($asg.DesiredCapacity) min=$($asg.MinSize) max=$($asg.MaxSize)"
}

# 4) Recent scaling events
$act = Aws autoscaling describe-scaling-activities --auto-scaling-group-name $AsgName --max-items 5 --output json 2>$null | ConvertFrom-Json
if ($act -and $act.Activities -and $act.Activities.Count -gt 0) {
    $last = $act.Activities[0]
    Ok "ScalingEvents" "Recent: $($last.StatusCode) $($last.Description)"
} else {
    Ok "ScalingEvents" "No recent activity"
}

# 5) DLQ / redrive 설정 유무
$hasDlq = $false
$hasRedrive = $false
if ($qurl) {
    $allAttrs = Aws sqs get-queue-attributes --queue-url $qurl --attribute-names All --output json 2>$null | ConvertFrom-Json
    if ($allAttrs.Attributes.RedrivePolicy) { $hasRedrive = $true }
}
$dlqUrl = Aws sqs get-queue-url --queue-name $DlqName --query "QueueUrl" --output text 2>$null
if ($dlqUrl) { $hasDlq = $true }
if ($hasDlq -and $hasRedrive) {
    Ok "DLQ" "DLQ 존재, RedrivePolicy 설정됨"
} elseif ($hasDlq) {
    Warn "DLQ" "DLQ 존재하나 RedrivePolicy 없음"
} else {
    Fail "DLQ" "DLQ 없음 또는 RedrivePolicy 미설정"
}

# 6) Lambda가 스케일링 경로에 있는지 (VideoQueueDepthTotal은 Lambda가 발행 → 있으면 경고로 안내)
$fn = Aws lambda get-function --function-name $LambdaName --output json 2>$null
if ($fn -and $metricName -eq "VideoQueueDepthTotal") {
    Warn "LambdaInPath" "스케일링 메트릭(VideoQueueDepthTotal)을 Lambda가 발행 중. 정상 동작이지만 Lambda 장애 시 스케일 정지."
} elseif ($metricName -eq "BacklogCount") {
    Warn "LambdaInPath" "BacklogCount 사용 시 Lambda/API 의존. SQS 기반으로 전환 권장."
}

# 요약
$failCount = ($results.Values | Where-Object { -not $_.ok }).Count
Write-Host "`n========== 요약 ==========" -ForegroundColor Cyan
if ($failCount -eq 0) {
    Write-Host "  결과: OK (요구사항 만족)" -ForegroundColor Green
} else {
    Write-Host "  결과: FAIL (실패 $failCount 건)" -ForegroundColor Red
}
Write-Host ""
