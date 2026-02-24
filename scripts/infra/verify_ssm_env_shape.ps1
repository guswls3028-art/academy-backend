# ==============================================================================
# Verify SSM /academy/workers/env: parameter exists. No Value fetch (avoids encoding/exit 255).
# Usage: .\scripts\infra\verify_ssm_env_shape.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [Parameter(Mandatory=$true)][string]$Region,
    [string]$ParamName = "/academy/workers/env"
)

$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$ErrorActionPreference = "Stop"

# Existence only — do NOT request Parameter.Value
$prev = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$paramJson = aws ssm get-parameter --name $ParamName --region $Region --query "Parameter.{Name:Name,Type:Type,Version:Version}" --output json 2>&1
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $prev
if ($exitCode -ne 0) {
    Write-Host "FAIL: SSM get-parameter failed (exit $exitCode). Parameter may not exist or no permission." -ForegroundColor Red
    exit 1
}
$param = $null
try { $param = $paramJson | ConvertFrom-Json } catch {}
if (-not $param -or -not $param.Name) {
    Write-Host "FAIL: SSM parameter could not be read." -ForegroundColor Red
    exit 1
}
Write-Host "OK: SSM parameter exists (Name=$($param.Name), Version=$($param.Version)). Values not fetched." -ForegroundColor Green
