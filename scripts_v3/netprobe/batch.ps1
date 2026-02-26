# Netprobe: submit job to Ops queue, wait SUCCEEDED. Returns jobId and status for Evidence.
# Requires script:Region, script:OpsQueueName, script:OpsJobDefNetprobe (from env/prod.ps1).
param(
    [string]$Region = $script:Region,
    [string]$JobQueueName = $script:OpsQueueName,
    [string]$JobDefName = $script:OpsJobDefNetprobe,
    [int]$TimeoutSec = 300,
    [int]$RunnableFailSec = 180
)
$ErrorActionPreference = "Stop"
$jobName = "netprobe-" + (Get-Date -Format "yyyyMMddHHmmss")
$submitOut = aws batch submit-job --job-name $jobName --job-queue $JobQueueName --job-definition $JobDefName --region $Region --output json 2>&1
if ($LASTEXITCODE -ne 0) { throw "Netprobe submit failed: $submitOut" }
$submit = $submitOut | ConvertFrom-Json
$jobId = $submit.jobId
Write-Host "  Netprobe jobId=$jobId" -ForegroundColor Cyan
$elapsed = 0
while ($elapsed -lt $TimeoutSec) {
    $desc = Invoke-AwsJson @("batch", "describe-jobs", "--jobs", $jobId, "--region", $Region, "--output", "json")
    if (-not $desc -or -not $desc.jobs -or $desc.jobs.Count -eq 0) { Start-Sleep -Seconds 10; $elapsed += 10; continue }
    $job = $desc.jobs[0]
    $status = $job.status
    Write-Host "  status=$status" -ForegroundColor Gray
    if ($status -eq "RUNNABLE" -and $elapsed -ge $RunnableFailSec) {
        Write-Host "Netprobe stuck RUNNABLE ($RunnableFailSec)s" -ForegroundColor Red
        return @{ jobId = $jobId; status = $status }
    }
    if ($status -eq "SUCCEEDED") {
        Write-Ok "Netprobe SUCCEEDED"
        return @{ jobId = $jobId; status = $status }
    }
    if ($status -eq "FAILED") {
        throw "Netprobe FAILED: $($job.statusReason)"
    }
    Start-Sleep -Seconds 10
    $elapsed += 10
}
throw "Netprobe timeout (${TimeoutSec}s)"
