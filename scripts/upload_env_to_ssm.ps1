# ==============================================================================
# .env -> SSM /academy/workers/env (Windows: use fileb:// to avoid CLI decode errors)
# Usage: .\scripts\upload_env_to_ssm.ps1
# ==============================================================================

param(
    [string]$RepoRoot = (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) ".."),
    [string]$Region = "ap-northeast-2",
    [string]$ParameterName = "/academy/workers/env"
)

$ErrorActionPreference = "Stop"
$envPath = Join-Path $RepoRoot ".env"

if (-not (Test-Path $envPath)) {
    Write-Host "upload_env_to_ssm: .env not found at $envPath" -ForegroundColor Yellow
    exit 1
}

# Read .env content as string (UTF-8 then Default); normalize LF
$content = $null
foreach ($enc in @([System.Text.Encoding]::UTF8, [System.Text.Encoding]::Default)) {
    try {
        $content = [System.IO.File]::ReadAllText($envPath, $enc)
        break
    } catch { continue }
}
if (-not $content) { Write-Host "upload_env_to_ssm: could not read .env" -ForegroundColor Red; exit 1 }
$content = $content -replace "`r`n", "`n" -replace "`r", "`n"

# Pass content directly as --value string (avoid file:// encoding issues on Windows)
$ea = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
aws ssm put-parameter --name $ParameterName --type SecureString --value "$content" --overwrite --region $Region
$ok = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $ea

if ($ok) {
    Write-Host "SSM $ParameterName updated." -ForegroundColor Green
    exit 0
} else {
    Write-Host "upload_env_to_ssm: put-parameter failed." -ForegroundColor Red
    exit 1
}
