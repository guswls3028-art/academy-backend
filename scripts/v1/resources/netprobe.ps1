# Netprobe: submit job to Ops queue, wait SUCCEEDED. FAILED/TIMEOUT -> throw.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
function Invoke-Netprobe {
    param([int]$TimeoutSec = 1200, [int]$RunnableFailSec = 300)
    $ErrorActionPreference = "Stop"
    if ($script:PlanMode) { return @{ jobId = ""; status = "skipped" } }
    $jobName = "netprobe-" + (Get-Date -Format "yyyyMMddHHmmss")
    $submitOut = aws batch submit-job --job-name $jobName --job-queue $script:OpsQueueName --job-definition $script:OpsJobDefNetprobe --region $script:Region --output json 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Netprobe submit failed: $submitOut" }
    $submit = $submitOut | ConvertFrom-Json
    $jobId = $submit.jobId
    Write-Host "  Netprobe jobId=$jobId" -ForegroundColor Cyan
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $desc = Invoke-AwsJson @("batch", "describe-jobs", "--jobs", $jobId, "--region", $script:Region, "--output", "json")
        if (-not $desc -or -not $desc.jobs -or $desc.jobs.Count -eq 0) { Start-Sleep -Seconds 10; $elapsed += 10; continue }
        $job = $desc.jobs[0]
        $status = $job.status
        Write-Host "  status=$status" -ForegroundColor Gray
        if ($status -eq "RUNNABLE" -and $elapsed -ge $RunnableFailSec) {
            throw "Netprobe stuck RUNNABLE ($RunnableFailSec)s; jobId=$jobId"
        }
        if ($status -eq "SUCCEEDED") {
            Write-Ok "Netprobe SUCCEEDED"
            return @{ jobId = $jobId; status = $status }
        }
        if ($status -eq "FAILED") {
            throw "Netprobe FAILED: jobId=$jobId statusReason=$($job.statusReason)"
        }
        Start-Sleep -Seconds 10
        $elapsed += 10
    }
    throw "Netprobe timeout (${TimeoutSec}s); jobId=$jobId"
}
