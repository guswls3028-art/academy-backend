# Print SOLAPI_SENDER and MESSAGING_SQS_QUEUE_NAME from workers env (read-only).
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\ssot.ps1")
$null = Load-SSOT -Env "prod"

$r = aws ssm get-parameter --name $script:SsmWorkersEnv --with-decryption --query "Parameter.Value" --output text --region $script:Region --profile default 2>&1
if ($LASTEXITCODE -ne 0) { Write-Error "get-parameter failed"; exit 1 }
$b64 = ($r | Out-String).Trim()
$json = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64))
$obj = $json | ConvertFrom-Json
Write-Host "SOLAPI_SENDER=[$($obj.SOLAPI_SENDER)]"
Write-Host "MESSAGING_SQS_QUEUE_NAME=[$($obj.MESSAGING_SQS_QUEUE_NAME)]"
Write-Host "OWNER_TENANT_ID=[$($obj.OWNER_TENANT_ID)]"
