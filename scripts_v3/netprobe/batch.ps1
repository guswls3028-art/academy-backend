# SSOT v3 — Netprobe: Ops Queue에 netprobe Job 제출 후 SUCCEEDED 대기
param(
    [string]$Region = $script:Region,
    [string]$JobQueueName = $script:OpsQueueName,
    [string]$JobDefName = $script:OpsNetprobeJobDef
)

$jobName = "netprobe-" + (Get-Date -Format "yyyyMMddHHmmss")
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$submitOut = aws batch submit-job --job-name $jobName --job-queue $JobQueueName --job-definition $JobDefName --region $Region --output json 2>&1
$ErrorActionPreference = $prevErr
if ($LASTEXITCODE -ne 0) {
    Write-Fail "submit-job failed"
    Write-Host ($submitOut | Out-String) -ForegroundColor Gray
    throw "Netprobe submit failed"
}
$submit = $submitOut | ConvertFrom-Json
if (-not $submit.jobId) { throw "Netprobe: no jobId" }
$jobId = $submit.jobId
Write-Host "  Netprobe jobId=$jobId" -ForegroundColor Cyan

$maxWait = 300
$elapsed = 0
while ($elapsed -lt $maxWait) {
    $desc = aws batch describe-jobs --jobs $jobId --region $Region --output json 2>&1 | ConvertFrom-Json
    if (-not $desc.jobs -or $desc.jobs.Count -eq 0) { Start-Sleep -Seconds 10; $elapsed += 10; continue }
    $job = $desc.jobs[0]
    $status = $job.status
    Write-Host "  status=$status" -ForegroundColor Gray
    if ($status -eq "SUCCEEDED") {
        Write-Ok "Netprobe SUCCEEDED"
        return $jobId
    }
    if ($status -eq "FAILED") {
        Write-Fail "Netprobe FAILED: $($job.statusReason)"
        throw "Netprobe FAILED"
    }
    Start-Sleep -Seconds 10
    $elapsed += 10
}
Write-Fail "Netprobe timeout"
throw "Netprobe timeout"
