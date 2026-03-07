# ==============================================================================
# SSM /academy/api/env 에 SQS 큐 이름 주입 (params SSOT)
# ==============================================================================
# API가 메시징/AI job enqueue 시 academy-v1-messaging-queue, academy-v1-ai-queue 사용.
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

$newJson = $obj | ConvertTo-Json -Compress -Depth 10
# API env 저장 형식 확인 (plain JSON vs base64)
$newValue = $newJson
if ($valueRaw -match '^[A-Za-z0-9+/]+=*$') {
    $newBytes = [System.Text.Encoding]::UTF8.GetBytes($newJson)
    $newValue = [Convert]::ToBase64String($newBytes)
}

Invoke-Aws @("ssm", "put-parameter", "--name", $paramName, "--type", "SecureString", "--value", $newValue, "--overwrite", "--region", $script:Region) -ErrorMessage "put-parameter api env" | Out-Null

Write-Host "SSM $paramName updated with SQS queue names:" -ForegroundColor Green
Write-Host "  MESSAGING_SQS_QUEUE_NAME=$($script:MessagingSqsQueueName)" -ForegroundColor Gray
Write-Host "  AI_SQS_QUEUE_NAME_*= $($script:AiSqsQueueName)" -ForegroundColor Gray
Write-Host "`nAPI 인스턴스 refresh-api-env.ps1 실행 또는 instance-refresh 후 적용됨." -ForegroundColor Cyan
