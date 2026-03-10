# ==============================================================================
# SSM /academy/api/env 에 SQS 큐 이름 + Video Batch(SSOT) + Redis(선택) 주입
# ==============================================================================
# deploy.ps1 실행 시 인프라 Ensure 후 Invoke-SyncEnvFromSSOT 가 자동으로 API/Workers env를
# SSOT와 Redis discovery 기준으로 동기화하므로, 정식 배포는 deploy.ps1만 반복 실행하면 됨.
# 이 스크립트는 배포 없이 SSM만 갱신할 때(예: refresh-api-env 전에 수동 갱신) 사용.
# - SQS: 메시징/AI job enqueue 시 사용하는 큐 이름 (params SSOT).
# - Video Batch: upload_complete 후 submit_batch_job 가 바라보는 큐/JobDef/CE (params SSOT).
# 사용: pwsh scripts/v1/update-api-env-sqs.ps1 [-AwsProfile default]
# ==============================================================================
param([string]$AwsProfile = "")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\env.ps1")
if ($AwsProfile) { $env:AWS_PROFILE = $AwsProfile; if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" } }

. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")
$null = Load-SSOT -Env "prod"

$paramName = $script:SsmApiEnv
if (-not $paramName) {
    Write-Host "SsmApiEnv not set." -ForegroundColor Red
    exit 1
}

$existing = $null
try {
    $existing = Invoke-AwsJson @("ssm", "get-parameter", "--name", $paramName, "--with-decryption", "--region", $script:Region, "--output", "json")
} catch {
    Write-Host "SSM $paramName not found or no access." -ForegroundColor Red
    exit 1
}

$valueRaw = $existing.Parameter.Value
# API env는 base64 또는 plain JSON
$jsonStr = $valueRaw
if ($valueRaw -match '^[A-Za-z0-9+/]+=*$') {
    try {
        $jsonStr = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($valueRaw))
    } catch { }
}
$obj = $jsonStr | ConvertFrom-Json

$obj | Add-Member -NotePropertyName "MESSAGING_SQS_QUEUE_NAME" -NotePropertyValue $script:MessagingSqsQueueName -Force
$obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_BASIC" -NotePropertyValue $script:AiSqsQueueName -Force
$obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_LITE" -NotePropertyValue $script:AiSqsQueueName -Force
$obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_PREMIUM" -NotePropertyValue $script:AiSqsQueueName -Force

# Video Batch (params SSOT) — API가 submit_batch_job 시 참조하는 큐/JobDef/CE
$obj | Add-Member -NotePropertyName "VIDEO_BATCH_JOB_QUEUE" -NotePropertyValue $script:VideoQueueName -Force
$obj | Add-Member -NotePropertyName "VIDEO_BATCH_JOB_DEFINITION" -NotePropertyValue $script:VideoJobDefName -Force
$obj | Add-Member -NotePropertyName "VIDEO_BATCH_COMPUTE_ENV_NAME" -NotePropertyValue $script:VideoCEName -Force
if ($script:VideoLongQueueName) {
    $obj | Add-Member -NotePropertyName "VIDEO_BATCH_JOB_QUEUE_LONG" -NotePropertyValue $script:VideoLongQueueName -Force
    $obj | Add-Member -NotePropertyName "VIDEO_BATCH_JOB_DEFINITION_LONG" -NotePropertyValue $script:VideoLongJobDefName -Force
}

$newJson = $obj | ConvertTo-Json -Compress -Depth 10
# API env 저장 형식 확인 (plain JSON vs base64)
$newValue = $newJson
if ($valueRaw -match '^[A-Za-z0-9+/]+=*$') {
    $newBytes = [System.Text.Encoding]::UTF8.GetBytes($newJson)
    $newValue = [Convert]::ToBase64String($newBytes)
}

Invoke-Aws @("ssm", "put-parameter", "--name", $paramName, "--type", "SecureString", "--value", $newValue, "--overwrite", "--region", $script:Region) -ErrorMessage "put-parameter api env" | Out-Null

Write-Host "SSM $paramName updated:" -ForegroundColor Green
Write-Host "  SQS: MESSAGING_SQS_QUEUE_NAME=$($script:MessagingSqsQueueName)" -ForegroundColor Gray
Write-Host "  SQS: AI_SQS_QUEUE_NAME_*= $($script:AiSqsQueueName)" -ForegroundColor Gray
Write-Host "  Video Batch: VIDEO_BATCH_JOB_QUEUE=$($script:VideoQueueName)" -ForegroundColor Gray
Write-Host "  Video Batch: VIDEO_BATCH_JOB_DEFINITION=$($script:VideoJobDefName)" -ForegroundColor Gray
if ($script:VideoLongQueueName) {
    Write-Host "  Video Batch Long: VIDEO_BATCH_JOB_QUEUE_LONG=$($script:VideoLongQueueName)" -ForegroundColor Gray
}
Write-Host "`nAPI 인스턴스 refresh-api-env.ps1 실행 또는 instance-refresh 후 적용됨." -ForegroundColor Cyan
Write-Host "연결 참조 대조: docs/00-SSOT/v1/reports/API-VIDEO-BATCH-REDIS-CONNECTION-REFERENCE.md" -ForegroundColor Cyan
