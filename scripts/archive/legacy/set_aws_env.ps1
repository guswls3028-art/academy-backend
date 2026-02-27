# AWS CLI/ECR auth - load from .env.aws into current session
# Usage: . .\scripts\set_aws_env.ps1   (dot-source so current shell gets env)
$envFile = Join-Path (Get-Location) ".env.aws"
if (-not (Test-Path $envFile)) {
    Write-Host "Missing: .env.aws (copy .env.aws.example and fill values)" -ForegroundColor Yellow
    exit 1
}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)\s*$') {
        [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim(), 'Process')
    }
}
Write-Host "OK: AWS env loaded from .env.aws" -ForegroundColor Green
