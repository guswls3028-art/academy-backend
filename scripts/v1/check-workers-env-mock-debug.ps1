# Check /academy/workers/env for SOLAPI_MOCK and DEBUG (read-only).
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\ssot.ps1")
$null = Load-SSOT -Env "prod"

$r = aws ssm get-parameter --name $script:SsmWorkersEnv --with-decryption --query "Parameter.Value" --output text --region $script:Region --profile default 2>&1
if ($LASTEXITCODE -ne 0) { Write-Error "get-parameter failed: $r"; exit 1 }
$b64 = ($r | Out-String).Trim()
$json = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64))
$obj = $json | ConvertFrom-Json
Write-Host "SOLAPI_MOCK=[$($obj.SOLAPI_MOCK)]"
Write-Host "DEBUG=[$($obj.DEBUG)]"
$hasMock = $null -ne $obj.PSObject.Properties["SOLAPI_MOCK"]
$hasDebug = $null -ne $obj.PSObject.Properties["DEBUG"]
Write-Host "HasKey SOLAPI_MOCK=$hasMock DEBUG=$hasDebug"
