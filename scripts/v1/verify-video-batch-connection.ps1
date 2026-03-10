# ==============================================================================
# API ↔ Video Batch 연결 상태 점검
# ==============================================================================
# AWS 자격 증명: default 프로필 사용 (문서/운영 가이드와 동일).
# 사용: pwsh -File scripts/v1/verify-video-batch-connection.ps1
# ==============================================================================

$ErrorActionPreference = "Stop"
$Region = "ap-northeast-2"
$Profile = "default"

Write-Host "`n=== 1) SSM /academy/api/env — VIDEO_BATCH_* 값 ===" -ForegroundColor Cyan
try {
    $raw = aws ssm get-parameter --name "/academy/api/env" --region $Region --profile $Profile --with-decryption --query "Parameter.Value" --output text 2>&1
    if ($LASTEXITCODE -ne 0) { throw $raw }
    $json = $raw
    if ($raw -match '^[A-Za-z0-9+/]+=*$') {
        try { $json = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($raw)) } catch { }
    }
    $obj = $json | ConvertFrom-Json
    $keys = @(
        "VIDEO_BATCH_JOB_QUEUE",
        "VIDEO_BATCH_JOB_DEFINITION",
        "VIDEO_BATCH_JOB_QUEUE_LONG",
        "VIDEO_BATCH_JOB_DEFINITION_LONG",
        "VIDEO_BATCH_COMPUTE_ENV_NAME"
    )
    $expected = @{
        VIDEO_BATCH_JOB_QUEUE = "academy-v1-video-batch-queue"
        VIDEO_BATCH_JOB_DEFINITION = "academy-v1-video-batch-jobdef"
        VIDEO_BATCH_JOB_QUEUE_LONG = "academy-v1-video-batch-long-queue"
        VIDEO_BATCH_JOB_DEFINITION_LONG = "academy-v1-video-batch-long-jobdef"
        VIDEO_BATCH_COMPUTE_ENV_NAME = "academy-v1-video-batch-ce"
    }
    $allOk = $true
    foreach ($k in $keys) {
        $v = $obj.PSObject.Properties[$k].Value
        $exp = $expected[$k]
        $ok = ($v -eq $exp)
        if (-not $ok) { $allOk = $false }
        $color = if ($ok) { "Green" } else { "Red" }
        $status = if ($ok) { "OK" } else { "MISMATCH (expected: $exp)" }
        Write-Host "  $k = $v" -ForegroundColor $color
        if (-not $ok) { Write-Host "    -> $status" -ForegroundColor Red }
    }
    if ($allOk) { Write-Host "  SSM VIDEO_BATCH_* 일치" -ForegroundColor Green }
    else { Write-Host "  SSM 수정 필요: pwsh scripts/v1/update-api-env-sqs.ps1 실행 후 instance-refresh 또는 refresh-api-env" -ForegroundColor Yellow }
} catch {
    Write-Host "  ERROR: $_" -ForegroundColor Red
    Write-Host "  (자격 증명 또는 SSM 권한 확인)" -ForegroundColor Gray
}

Write-Host "`n=== 2) AWS Batch Job Queues ===" -ForegroundColor Cyan
try {
    $q = aws batch describe-job-queues --region $Region --profile $Profile --query "jobQueues[*].jobQueueName" --output text 2>&1
    if ($LASTEXITCODE -ne 0) { throw $q }
    Write-Host "  $q" -ForegroundColor Gray
    $hasV1 = $q -match "academy-v1-video-batch-queue"
    if ($hasV1) { Write-Host "  academy-v1-video-batch-queue 존재" -ForegroundColor Green }
    else { Write-Host "  academy-v1-video-batch-queue 없음" -ForegroundColor Red }
} catch {
    Write-Host "  ERROR: $_" -ForegroundColor Red
}

Write-Host "`n=== 3) AWS Batch Job Definitions (ACTIVE) ===" -ForegroundColor Cyan
try {
    $jd = aws batch describe-job-definitions --status ACTIVE --region $Region --profile $Profile --query "jobDefinitions[*].jobDefinitionName" --output text 2>&1
    if ($LASTEXITCODE -ne 0) { throw $jd }
    $jdList = $jd -split "\s+"
    $v1JobDef = $jdList | Where-Object { $_ -match "academy-v1-video-batch-jobdef" }
    if ($v1JobDef) { Write-Host "  academy-v1-video-batch-jobdef 존재" -ForegroundColor Green; Write-Host "  $($v1JobDef -join ' ')" -ForegroundColor Gray }
    else { Write-Host "  academy-v1-video-batch-jobdef 없음" -ForegroundColor Red }
    Write-Host "  (전체: $($jdList.Count)개)" -ForegroundColor Gray
} catch {
    Write-Host "  ERROR: $_" -ForegroundColor Red
}

Write-Host "`n=== 4) AWS Batch Compute Environments ===" -ForegroundColor Cyan
try {
    $ce = aws batch describe-compute-environments --region $Region --profile $Profile --query "computeEnvironments[*].computeEnvironmentName" --output text 2>&1
    if ($LASTEXITCODE -ne 0) { throw $ce }
    $hasCe = $ce -match "academy-v1-video-batch-ce"
    if ($hasCe) { Write-Host "  academy-v1-video-batch-ce 존재" -ForegroundColor Green }
    else { Write-Host "  academy-v1-video-batch-ce 없음" -ForegroundColor Red }
    Write-Host "  $ce" -ForegroundColor Gray
} catch {
    Write-Host "  ERROR: $_" -ForegroundColor Red
}

Write-Host "`n=== 5) 최근 Batch Jobs (academy-v1-video-batch-queue) ===" -ForegroundColor Cyan
try {
    $jobs = aws batch list-jobs --job-queue academy-v1-video-batch-queue --region $Region --profile $Profile --query "jobSummaryList[-5].{id:jobId,name:jobName,status:status,created:createdAt}" --output table 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Host "  (큐 없거나 권한 없음)" -ForegroundColor Yellow; Write-Host "  $jobs" -ForegroundColor Gray }
    else { Write-Host $jobs -ForegroundColor Gray }
} catch {
    Write-Host "  $_" -ForegroundColor Gray
}

Write-Host "`n=== 점검 완료 ===" -ForegroundColor Cyan
Write-Host "연결 참조 문서: docs/00-SSOT/v1/reports/API-VIDEO-BATCH-REDIS-CONNECTION-REFERENCE.md" -ForegroundColor Gray
