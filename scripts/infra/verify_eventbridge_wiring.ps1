# ==============================================================================
# Verify EventBridge rules exist, ENABLED, targets present with BatchParameters (reconcile/scan_stuck -> Ops queue).
# Usage: .\scripts\infra\verify_eventbridge_wiring.ps1 -Region ap-northeast-2 -OpsJobQueueName academy-video-ops-queue
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$OpsJobQueueName = "academy-video-ops-queue"
)
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$ErrorActionPreference = "Stop"
$ReconcileRule = "academy-reconcile-video-jobs"
$ScanStuckRule = "academy-video-scan-stuck-rate"

function ExecJson($argsArray) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @argsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

$fail = 0
$queueArn = (ExecJson @("batch", "describe-job-queues", "--job-queues", $OpsJobQueueName, "--region", $Region, "--output", "json")).jobQueues[0].jobQueueArn
if (-not $queueArn) {
    Write-Host "FAIL: Ops job queue $OpsJobQueueName not found." -ForegroundColor Red
    exit 1
}

foreach ($ruleName in @($ReconcileRule, $ScanStuckRule)) {
    $r = ExecJson @("events", "describe-rule", "--name", $ruleName, "--region", $Region, "--output", "json")
    if (-not $r -or -not $r.Name) {
        Write-Host "FAIL: EventBridge rule $ruleName does not exist." -ForegroundColor Red
        $fail = 1
        continue
    }
    if ($r.State -ne "ENABLED") {
        Write-Host "FAIL: EventBridge rule $ruleName state=$($r.State) (expected ENABLED)." -ForegroundColor Red
        $fail = 1
    }
    $targets = ExecJson @("events", "list-targets-by-rule", "--rule", $ruleName, "--region", $Region, "--output", "json")
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
            Write-Host "FAIL: EventBridge rule $ruleName target JobQueue ARN does not match Ops queue $OpsJobQueueName." -ForegroundColor Red
            $fail = 1
        }
    }
}

if ($fail -ne 0) { exit 1 }
Write-Host "EventBridge wiring: rules exist, ENABLED, Batch targets -> $OpsJobQueueName." -ForegroundColor Green
