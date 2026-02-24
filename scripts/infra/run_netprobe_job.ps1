# ==============================================================================
# Submit netprobe job, poll until SUCCEEDED/FAILED, print logStreamName and last ~200 log lines.
# Usage: .\scripts\infra\run_netprobe_job.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue
# ==============================================================================
if ($OutputEncoding) { $OutputEncoding = [System.Text.Encoding]::UTF8 }
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

param(
    [string]$Region = "ap-northeast-2",
    [string]$JobQueueName = "academy-video-batch-queue",
    [string]$JobDefName = "academy-video-ops-netprobe"
)

$ErrorActionPreference = "Stop"
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

$jobName = "netprobe-" + (Get-Date -Format "yyyyMMddHHmmss")
$submit = ExecJson "batch", "submit-job", "--job-name", $jobName, "--job-queue", $JobQueueName, "--job-definition", $JobDefName, "--region", $Region, "--output", "json"
if (-not $submit -or -not $submit.jobId) {
    Write-Host "FAIL: submit-job failed (job definition or queue missing?)." -ForegroundColor Red
    exit 1
}
$jobId = $submit.jobId
Write-Host "Submitted jobId=$jobId" -ForegroundColor Cyan

$maxWait = 300
$elapsed = 0
while ($elapsed -lt $maxWait) {
    $desc = ExecJson "aws batch describe-jobs --jobs $jobId --region $Region --output json"
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
