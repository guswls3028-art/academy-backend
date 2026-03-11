# One-time script: Remove SOLAPI_MOCK from /academy/workers/env and put back.
# Run: pwsh -File scripts/v1/remove-solapi-mock-from-workers-ssm.ps1
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\ssot.ps1")
$null = Load-SSOT -Env "prod"

$r = aws ssm get-parameter --name $script:SsmWorkersEnv --with-decryption --query "Parameter.Value" --output text --region $script:Region --profile default 2>&1
if ($LASTEXITCODE -ne 0) { Write-Error "get-parameter failed: $r"; exit 1 }
$b64 = ($r | Out-String).Trim()
$json = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64))
$obj = $json | ConvertFrom-Json
Write-Host "SOLAPI_MOCK before: $($obj.SOLAPI_MOCK)"
$obj.PSObject.Properties.Remove("SOLAPI_MOCK")
$newJson = ($obj | ConvertTo-Json -Compress -Depth 10)
$bytes = [System.Text.Encoding]::UTF8.GetBytes($newJson)
$newB64 = [Convert]::ToBase64String($bytes)
aws ssm put-parameter --name $script:SsmWorkersEnv --type SecureString --value $newB64 --overwrite --region $script:Region --profile default
if ($LASTEXITCODE -ne 0) { Write-Error "put-parameter failed"; exit 1 }
Write-Host "SOLAPI_MOCK removed; SSM updated."
