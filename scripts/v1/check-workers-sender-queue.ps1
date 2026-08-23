# Read-only, secret-free API/worker messaging environment alignment check.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\ssot.ps1")
$null = Load-SSOT -Env "prod"

$apiRaw = aws ssm get-parameter --name $script:SsmApiEnv --with-decryption --query "Parameter.Value" --output text --region $script:Region --profile default 2>&1
if ($LASTEXITCODE -ne 0) { Write-Error "api env get-parameter failed"; exit 1 }
$workerRaw = aws ssm get-parameter --name $script:SsmWorkersEnv --with-decryption --query "Parameter.Value" --output text --region $script:Region --profile default 2>&1
if ($LASTEXITCODE -ne 0) { Write-Error "worker env get-parameter failed"; exit 1 }

$api = (($apiRaw | Out-String).Trim()) | ConvertFrom-Json
$workerJson = [System.Text.Encoding]::UTF8.GetString(
    [Convert]::FromBase64String(($workerRaw | Out-String).Trim())
)
$worker = $workerJson | ConvertFrom-Json
$keys = @("SOLAPI_API_KEY", "SOLAPI_API_SECRET", "SOLAPI_SENDER")
$present = $true
$equal = $true
foreach ($key in $keys) {
    $apiValue = [string]$api.PSObject.Properties[$key].Value
    $workerValue = [string]$worker.PSObject.Properties[$key].Value
    if (-not $apiValue -or -not $workerValue) { $present = $false }
    if ($apiValue -cne $workerValue) { $equal = $false }
}

Write-Host "SOLAPI_COMMON_CONFIG_PRESENT=$($present.ToString().ToLowerInvariant())"
Write-Host "SOLAPI_API_WORKER_EQUAL=$($equal.ToString().ToLowerInvariant())"
Write-Host "MESSAGING_QUEUE_CONFIGURED=$([bool]([string]$worker.MESSAGING_SQS_QUEUE_NAME))"
Write-Host "OWNER_TENANT_CONFIGURED=$([bool]([string]$worker.OWNER_TENANT_ID))"
