# ==============================================================================
# Submit netprobe job to Ops queue, poll until SUCCEEDED/FAILED, print logStreamName and last ~200 log lines.
# On timeout: outputs Evidence E (describe-jobs statusReason/container.reason) and clear failure message.
# Usage: .\scripts\infra\run_netprobe_job.ps1 -Region ap-northeast-2 -JobQueueName academy-video-ops-queue
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$JobQueueName = "academy-video-ops-queue",
    [string]$JobDefName = "academy-video-ops-netprobe",
    [string]$JobIdOutFile = "",
    [int]$RunnableFailSeconds = 180
)
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$ErrorActionPreference = "Stop"

$jobName = "netprobe-" + (Get-Date -Format "yyyyMMddHHmmss")
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $submitOut = cmd /c "aws batch submit-job --job-name $jobName --job-queue $JobQueueName --job-definition $JobDefName --region $Region --output json 2>&1"
} finally {
    $ErrorActionPreference = $prevErr
}
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Write-Host "FAIL: submit-job failed (exit $exitCode)." -ForegroundColor Red
    if ($submitOut) { Write-Host ($submitOut | Out-String) -ForegroundColor Gray }
    exit 1
}
$jsonStr = ($submitOut | Out-String).Trim()
$submit = $null
try { $submit = $jsonStr | ConvertFrom-Json } catch {}
if (-not $submit -or -not $submit.jobId) {
    Write-Host "FAIL: submit-job returned no jobId." -ForegroundColor Red
    if ($submitOut) { Write-Host ($submitOut | Out-String) -ForegroundColor Gray }
    exit 1
}
$jobId = $submit.jobId
if ($JobIdOutFile) { [System.IO.File]::WriteAllText($JobIdOutFile, $jobId, [System.Text.UTF8Encoding]::new($false)) }
Write-Host "Submitted jobId=$jobId" -ForegroundColor Cyan

function Get-DescribeJobsEvidence {
    param([string]$Jid, [string]$Reg)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = aws batch describe-jobs --jobs $Jid --region $Reg --output json 2>&1
    $ErrorActionPreference = $prev
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    try { return ($out | Out-String).Trim() | ConvertFrom-Json } catch { return $null }
}

$maxWait = 300
$elapsed = 0
while ($elapsed -lt $maxWait) {
    $ErrorActionPreference = "Continue"
    $descOut = aws batch describe-jobs --jobs $jobId --region $Region --output json 2>&1
    $ErrorActionPreference = $prevErr
    if ($LASTEXITCODE -ne 0) { Write-Host "  describe-jobs failed" -ForegroundColor Red; Start-Sleep -Seconds 10; $elapsed += 10; continue }
    $descStr = ($descOut | Out-String).Trim()
    $desc = $null
    try { $desc = $descStr | ConvertFrom-Json } catch {}
    if (-not $desc -or -not $desc.jobs -or $desc.jobs.Count -eq 0) {
        Write-Host "  describe-jobs: no jobs in response" -ForegroundColor Red
        Start-Sleep -Seconds 10
        $elapsed += 10
        continue
    }
    $job = $desc.jobs[0]
    $status = $job.status
    Write-Host "  status=$status" -ForegroundColor Gray
    if ($status -eq "RUNNABLE" -and $elapsed -ge $RunnableFailSeconds) {
        Write-Host "`n=== Evidence E (Netprobe job describe-jobs) ===" -ForegroundColor Cyan
        Write-Host "  jobId=$jobId status=$status statusReason=$($job.statusReason)" -ForegroundColor Gray
        if ($job.container) { Write-Host "  container.reason=$($job.container.reason) exitCode=$($job.container.exitCode)" -ForegroundColor Gray }
        Write-Host "`nNetprobe stuck RUNNABLE ($RunnableFailSeconds)s: no ECS capacity or no outbound. Check Ops CE subnets (NAT/VPCE), Batch SG egress." -ForegroundColor Red
        exit 1
    }
    if ($status -eq "SUCCEEDED") {
        $cont = $job.container
        $logStream = $cont.logStreamName
        Write-Host "logStreamName=$logStream" -ForegroundColor Cyan
        $group = $cont.logConfiguration.options."awslogs-group"
        if ($logStream -and $group) {
            $events = aws logs get-log-events --log-group-name $group --log-stream-name $logStream --limit 200 --region $Region --output json 2>&1 | ConvertFrom-Json
            Write-Host "--- Last log lines ---" -ForegroundColor Yellow
            $events.events | ForEach-Object { Write-Host $_.message }
        }
        Write-Host "SUCCEEDED" -ForegroundColor Green
        exit 0
    }
    if ($status -eq "FAILED") {
        Write-Host "FAILED: reason=$($job.statusReason)" -ForegroundColor Red
        $cont = $job.container
        $logStream = $cont.logStreamName
        if ($logStream) {
            $group = $cont.logConfiguration.options."awslogs-group"
            $events = aws logs get-log-events --log-group-name $group --log-stream-name $logStream --limit 200 --region $Region --output json 2>&1 | ConvertFrom-Json
            $events.events | ForEach-Object { Write-Host $_.message }
        }
        exit 1
    }
    Start-Sleep -Seconds 10
    $elapsed += 10
}

# Timeout: Evidence E + clear error message
Write-Host "`n=== Evidence E (Netprobe job describe-jobs) ===" -ForegroundColor Cyan
$ev = Get-DescribeJobsEvidence -Jid $jobId -Reg $Region
if ($ev -and $ev.jobs -and $ev.jobs.Count -gt 0) {
    $j = $ev.jobs[0]
    Write-Host "  jobId=$jobId status=$($j.status) statusReason=$($j.statusReason)" -ForegroundColor Gray
    if ($j.container) {
        Write-Host "  container.reason=$($j.container.reason) exitCode=$($j.container.exitCode)" -ForegroundColor Gray
    }
} else {
    Write-Host "  describe-jobs failed or empty for jobId=$jobId" -ForegroundColor Yellow
}
Write-Host "`nNetprobe stuck RUNNABLE: likely no ECS container instances / no outbound network from subnets. Check Ops CE subnets have NAT Gateway or IGW outbound (or required VPC Endpoints). Ensure Batch SG egress allows 0.0.0.0/0." -ForegroundColor Red
exit 1
