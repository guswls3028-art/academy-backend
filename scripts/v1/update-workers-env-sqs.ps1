# ==============================================================================
# SSM /academy/workers/env 에 SQS 큐 이름 주입 (params SSOT)
# ==============================================================================
# 메시징/AI 워커가 academy-v1-messaging-queue, academy-v1-ai-queue 사용하도록 SSM 갱신.
# 기존 SSM이 있을 때 수동 실행. deploy.ps1 Bootstrap은 신규 생성 시에만 SQS 주입.
# 사용: pwsh scripts/v1/update-workers-env-sqs.ps1 [-AwsProfile default]
# ==============================================================================
param([string]$AwsProfile = "")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\env.ps1")
if ($AwsProfile) { $env:AWS_PROFILE = $AwsProfile; if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" } }

. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")
$null = Load-SSOT -Env "prod"

$paramName = $script:SsmWorkersEnv
if (-not $paramName) {
    Write-Host "SsmWorkersEnv not set." -ForegroundColor Red
    exit 1
}

$existing = $null
try {
    $existing = Invoke-AwsJson @("ssm", "get-parameter", "--name", $paramName, "--with-decryption", "--region", $script:Region, "--output", "json")
} catch {
    Write-Host "SSM $paramName not found or no access." -ForegroundColor Red
    exit 1
}

$valueB64 = $existing.Parameter.Value
$jsonStr = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($valueB64))
$obj = $jsonStr | ConvertFrom-Json

# params SSOT 큐 이름 주입
$obj | Add-Member -NotePropertyName "MESSAGING_SQS_QUEUE_NAME" -NotePropertyValue $script:MessagingSqsQueueName -Force
$obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_BASIC" -NotePropertyValue $script:AiSqsQueueName -Force
$obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_LITE" -NotePropertyValue $script:AiSqsQueueName -Force
$obj | Add-Member -NotePropertyName "AI_SQS_QUEUE_NAME_PREMIUM" -NotePropertyValue $script:AiSqsQueueName -Force
# Messaging 워커: API env에서 SOLAPI_* 복사 (API와 동일 키 사용)
$apiParam = $script:SsmApiEnv
if ($apiParam) {
    try {
        $apiRaw = Invoke-AwsJson @("ssm", "get-parameter", "--name", $apiParam, "--with-decryption", "--region", $script:Region, "--output", "json")
        if ($apiRaw -and $apiRaw.Parameter -and $apiRaw.Parameter.Value) {
            $apiVal = $apiRaw.Parameter.Value
            $apiStr = $apiVal
            if ($apiVal -match '^[A-Za-z0-9+/]+=*$') {
                try { $apiStr = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($apiVal)) } catch { }
            }
            $apiObj = $apiStr | ConvertFrom-Json
            foreach ($k in @("SOLAPI_API_KEY", "SOLAPI_API_SECRET", "SOLAPI_SENDER", "SOLAPI_KAKAO_PF_ID", "SOLAPI_KAKAO_TEMPLATE_ID")) {
                if ($apiObj.PSObject.Properties[$k] -and [string]$apiObj.$k -ne "") {
                    $obj | Add-Member -NotePropertyName $k -NotePropertyValue $apiObj.$k -Force
                }
            }
            Write-Host "  SOLAPI_* copied from $apiParam" -ForegroundColor Gray
        }
    } catch { Write-Host "  (API env $apiParam not found, SOLAPI_* skip)" -ForegroundColor Gray }
}
# SOLAPI_* 미설정 시 Mock 모드 (config.py에서 placeholder 허용)
if (-not $obj.PSObject.Properties["SOLAPI_API_KEY"] -or [string]$obj.SOLAPI_API_KEY -eq "") {
    $obj | Add-Member -NotePropertyName "SOLAPI_MOCK" -NotePropertyValue "true" -Force
    Write-Host "  SOLAPI_MOCK=true (no SOLAPI keys)" -ForegroundColor Gray
}

$newJson = $obj | ConvertTo-Json -Compress -Depth 10
$newBytes = [System.Text.Encoding]::UTF8.GetBytes($newJson)
$newB64 = [Convert]::ToBase64String($newBytes)

Invoke-Aws @("ssm", "put-parameter", "--name", $paramName, "--type", "SecureString", "--value", $newB64, "--overwrite", "--region", $script:Region) -ErrorMessage "put-parameter workers env" | Out-Null

Write-Host "SSM $paramName updated with SQS queue names:" -ForegroundColor Green
Write-Host "  MESSAGING_SQS_QUEUE_NAME=$($script:MessagingSqsQueueName)" -ForegroundColor Gray
Write-Host "  AI_SQS_QUEUE_NAME_*= $($script:AiSqsQueueName)" -ForegroundColor Gray
Write-Host "`nMessaging/AI 워커 인스턴스 재시작(instance-refresh) 후 적용됨." -ForegroundColor Cyan
