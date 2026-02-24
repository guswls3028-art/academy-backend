# ==============================================================================
# Verify EventBridge rules exist, ENABLED, targets present with BatchParameters. Exit non-zero if missing.
# Usage: .\scripts\infra\verify_eventbridge_wiring.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$JobQueueName = "academy-video-batch-queue"
)

$ErrorActionPreference = "Stop"
$ReconcileRule = "academy-reconcile-video-jobs"
$ScanStuckRule = "academy-video-scan-stuck-rate"

function ExecJson($cmd) {
    $out = Invoke-Expression $cmd 2>&1
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

$fail = 0
$queueArn = (ExecJson "aws batch describe-job-queues --job-queues $JobQueueName --region $Region --output json").jobQueues[0].jobQueueArn
if (-not $queueArn) {
    Write-Host "FAIL: Job queue $JobQueueName not found." -ForegroundColor Red
    exit 1
}

foreach ($ruleName in @($ReconcileRule, $ScanStuckRule)) {
    $r = ExecJson "aws events describe-rule --name $ruleName --region $Region --output json 2>&1"
    if (-not $r -or -not $r.Name) {
        Write-Host "FAIL: EventBridge rule $ruleName does not exist." -ForegroundColor Red
        $fail = 1
        continue
    }
    if ($r.State -ne "ENABLED") {
        Write-Host "FAIL: EventBridge rule $ruleName state=$($r.State) (expected ENABLED)." -ForegroundColor Red
        $fail = 1
    }
    $targets = ExecJson "aws events list-targets-by-rule --rule $ruleName --region $Region --output json 2>&1"
    if (-not $targets.Targets -or $targets.Targets.Count -eq 0) {
        Write-Host "FAIL: EventBridge rule $ruleName has no targets." -ForegroundColor Red
        $fail = 1
    } else {
        $t = $targets.Targets[0]
        if (-not $t.BatchParameters) {
            Write-Host "FAIL: EventBridge rule $ruleName target is not Batch SubmitJob." -ForegroundColor Red
            $fail = 1
        }
        if ($t.Arn -ne $queueArn) {
            Write-Host "FAIL: EventBridge rule $ruleName target JobQueue ARN does not match." -ForegroundColor Red
            $fail = 1
        }
    }
}

if ($fail -ne 0) { exit 1 }
Write-Host "EventBridge wiring: rules exist, ENABLED, Batch targets present." -ForegroundColor Green
