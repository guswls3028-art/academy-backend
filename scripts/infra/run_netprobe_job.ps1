# ==============================================================================
# Submit netprobe job, poll until SUCCEEDED/FAILED, print logStreamName and last ~200 log lines.
# Usage: .\scripts\infra\run_netprobe_job.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$JobQueueName = "academy-video-batch-queue",
    [string]$JobDefName = "academy-video-ops-netprobe"
)
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$ErrorActionPreference = "Stop"

$jobName = "netprobe-" + (Get-Date -Format "yyyyMMddHHmmss")
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$submitOut = aws batch submit-job --job-name $jobName --job-queue $JobQueueName --job-definition $JobDefName --region $Region --output json 2>&1
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $prevErr
if ($exitCode -ne 0) {
    Write-Host "FAIL: submit-job failed (exit $exitCode)." -ForegroundColor Red
    if ($submitOut) { Write-Host ($submitOut | Out-String) -ForegroundColor Gray }
    exit 1
}
# stderr may be merged; take the line that looks like JSON
$jsonLine = $submitOut
if ($submitOut -is [array]) { $jsonLine = ($submitOut | Where-Object { $_ -match '^\s*\{' } | Select-Object -First 1) }
if (-not $jsonLine) { $jsonLine = ($submitOut | Out-String).Trim() }
$submit = $null
try { $submit = $jsonLine | ConvertFrom-Json } catch {}
if (-not $submit -or -not $submit.jobId) {
    Write-Host "FAIL: submit-job returned no jobId." -ForegroundColor Red
    if ($submitOut) { Write-Host ($submitOut | Out-String) -ForegroundColor Gray }
    exit 1
}
$jobId = $submit.jobId
Write-Host "Submitted jobId=$jobId" -ForegroundColor Cyan

$maxWait = 300
$elapsed = 0
while ($elapsed -lt $maxWait) {
    $ErrorActionPreference = "Continue"
    $descOut = aws batch describe-jobs --jobs $jobId --region $Region --output json 2>&1
    $ErrorActionPreference = $prevErr
    if ($LASTEXITCODE -ne 0) { Write-Host "  describe-jobs failed" -ForegroundColor Red; Start-Sleep -Seconds 10; $elapsed += 10; continue }
    $descJson = $descOut
    if ($descOut -is [array]) { $descJson = ($descOut | Where-Object { $_ -match '^\s*\{' } | Select-Object -First 1) }
    if (-not $descJson) { $descJson = ($descOut | Out-String).Trim() }
    $desc = $null
    try { $desc = $descJson | ConvertFrom-Json } catch {}
    $job = $desc.jobs[0]
    $status = $job.status
    Write-Host "  status=$status" -ForegroundColor Gray
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
Write-Host "Timeout waiting for job." -ForegroundColor Red
exit 1
